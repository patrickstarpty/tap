"""Task-owned OpenCode V1 live probes; durable output contains redacted facts only."""

import asyncio
import ast
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from scripts.opencode_probe.cli import make_child_env
from scripts.opencode_probe.evaluate import verified_provenance
from scripts.opencode_probe.gateway import BudgetGate
from scripts.opencode_probe.ledger import ProbeLedger
from scripts.opencode_probe.legacy import LegacyClient, classify_session, parse_sse
from scripts.opencode_probe.models import ProbeAgent
from scripts.opencode_probe.process import serve_probe
from scripts.opencode_probe.profiles import export_probe_config, export_profiles
from scripts.opencode_probe.resume_fixture import prepare_resume_fixture

_PROMPTS = {
    "simple-question": "What is 6 times 7? Reply with just the number. Do not use tools or delegate.",
    "auto-delegation": "Create a small Python script square.py that prints the squares of 2 and 3. Choose the appropriate specialist to implement and run it.",
    "explicit-delegation": "Ask the QAE specialist to inspect this workspace and report whether square.py exists. Do not do the inspection yourself.",
    "parallel": "Use two specialists concurrently for independent tasks: SDE writes one.py containing print(1) and runs it; SDET writes two.py containing print(2) and runs it. Report their results.",
    "permissions": "Delegate this request to the QAE specialist: ask QAE to attempt writing denied.txt containing denied. The coordinator must not write the file or invoke an editor itself. Report QAE's actual tool result.",
    "control-receipt": "Write first.txt containing first, then report completion.",
    "resume-workspace": "The supervisor has prepared waiter.py in this workspace. Run python3 waiter.py exactly once in the foreground. Do not edit or recreate waiter.py. Report the result. This is a synthetic interruption test.",
    "reconcile": "Write recovered.txt containing recovered, then report completion.",
    "budget-enforcement": "Write budget.txt containing budget, then report completion.",
    "roster-5": "Ask the SDE specialist to inspect this workspace and report whether square.py exists. Do not inspect it yourself.",
    "roster-25": "Ask the SDE specialist to inspect this workspace and report whether square.py exists. Do not inspect it yourself.",
    "roster-100": "Ask the SDE specialist to inspect this workspace and report whether square.py exists. Do not inspect it yourself.",
}


def _profiles(
    count: int = 4, *, orchestrator_revision: str = "probe-0.3-r2"
) -> tuple[ProbeAgent, ...]:
    if count not in (4, 5, 25, 100):
        raise ValueError("unsupported synthetic roster size")
    core = (
        ProbeAgent(
            "orchestrator",
            orchestrator_revision,
            "Internal coordinator",
            "Answer simple factual questions directly. For coding tasks, choose and delegate implementation to the authorized specialist whose role best matches the work; use independent specialists concurrently when tasks are independent. Finish tasks and report concise results.",
            ("read", "glob", "edit", "bash", "task"),
            True,
        ),
        ProbeAgent(
            "sde",
            "probe-0.3",
            "Software engineer for implementation",
            "Implement the requested synthetic code and check it.",
            ("read", "glob", "edit", "bash"),
            True,
        ),
        ProbeAgent(
            "sdet",
            "probe-0.3",
            "Test development engineer",
            "Create and run the requested synthetic test or script.",
            ("read", "glob", "edit", "bash"),
            True,
        ),
        ProbeAgent(
            "qae",
            "probe-0.3",
            "Read-only quality engineer",
            "Inspect and report findings. You have no write or shell permission.",
            ("read", "glob"),
            True,
        ),
    )
    fillers = tuple(
        ProbeAgent(
            f"fictional_{number:03d}",
            "probe-0.3-roster",
            f"Fictional specialist {number:03d} for an unrelated domain",
            "Handle only requests for your named fictional domain.",
            ("read",),
            True,
        )
        for number in range(1, count - len(core) + 1)
    )
    return core + fillers


def _profile_provenance(
    agents: tuple[ProbeAgent, ...], hashes: dict[str, str]
) -> dict[str, object]:
    revisions = {agent.agent_id: agent.revision_id for agent in agents if agent.enabled}
    identity = {"revisions": revisions, "hashes": hashes}
    return {
        "revisions": revisions,
        "aggregate_digest": hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _safe_square_script(source: str) -> bool:
    if len(source.encode()) > 4096:
        return False
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError):
        return False
    if not 0 < len(tree.body) <= 16:
        return False
    predicted_bytes = 0
    try:
        for statement in tree.body:
            if (
                not isinstance(statement, ast.Expr)
                or not isinstance(statement.value, ast.Call)
                or not isinstance(statement.value.func, ast.Name)
                or statement.value.func.id != "print"
                or statement.value.keywords
            ):
                return False
            values = [_bounded_print_value(arg) for arg in statement.value.args]
            if any(value is None for value in values):
                return False
            predicted_bytes += sum(len(str(value).encode()) for value in values)
            predicted_bytes += max(len(values) - 1, 0) + 1
            if predicted_bytes > 256:
                return False
    except (OverflowError, RecursionError, ValueError, ZeroDivisionError):
        return False
    return True


def _bounded_print_value(node: ast.expr) -> int | float | str | None:
    if isinstance(node, ast.Constant):
        value = node.value
        if type(value) is str:
            return value if len(value.encode()) <= 256 else None
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and abs(value) <= 10_000
        ):
            return value
        return None
    if not isinstance(node, ast.BinOp):
        return None
    left = _bounded_print_value(node.left)
    right = _bounded_print_value(node.right)
    if left is None or right is None:
        return None
    if isinstance(node.op, ast.Add) and type(left) is str and type(right) is str:
        combined = left + right
        return combined if len(combined.encode()) <= 256 else None
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    if isinstance(node.op, ast.Pow):
        if type(right) is not int or not 0 <= right <= 8 or abs(left) > 100:
            return None
        result = left**right
    elif isinstance(node.op, ast.Mult):
        result = left * right
    elif isinstance(node.op, ast.Add):
        result = left + right
    elif isinstance(node.op, ast.Sub):
        result = left - right
    elif isinstance(node.op, ast.Div):
        result = left / right
    else:
        return None
    return result if math.isfinite(result) and abs(result) <= 10_000 else None


def _read_bounded_script(path: Path) -> str | None:
    if path.is_symlink():
        return None
    try:
        with path.open("rb") as source:
            raw = source.read(4097)
        return raw.decode("utf-8") if len(raw) <= 4096 else None
    except (OSError, UnicodeDecodeError):
        return None


def _bounded_artifact_digest(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as artifact:
            while chunk := artifact.read(65_536):
                total += len(chunk)
                if total > 1_048_576:
                    return None
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _run_square_script(
    script: Path, workspace: Path
) -> tuple[int | None, str | None, str | None]:
    process = subprocess.Popen(
        (sys.executable, "-I", str(script)),
        cwd=workspace,
        env={"PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    captured = bytearray()
    limit: str | None = None
    deadline = time.monotonic() + 3
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                limit = "timeout"
                break
            chunk = os.read(process.stdout.fileno(), 257 - len(captured))
            if not chunk:
                break
            captured.extend(chunk)
            if len(captured) > 256:
                limit = "stdout_cap"
                break
    if limit is not None:
        process.kill()
    try:
        exit_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        limit = "timeout"
        exit_code = None
    finally:
        process.stdout.close()
    if limit is not None:
        return None, None, limit
    try:
        return exit_code, captured.decode("utf-8").strip(), None
    except UnicodeDecodeError:
        return None, None, "stdout_encoding"


def _square_output_matches(output: str) -> bool:
    """Accept values or labelled input/result pairs for the synthetic task."""
    if len(output.encode()) > 256:
        return False
    values = [
        float(value)
        for value in re.findall(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?![\w.])", output)
    ]
    return values in ([4.0, 9.0], [2.0, 4.0, 3.0, 9.0])


def _process_group_children(leader_pid: int | None) -> int | None:
    if leader_pid is None:
        return None
    result = subprocess.run(
        ("ps", "-axo", "pid=,pgid="),
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if result.returncode != 0:
        return None
    count = 0
    for line in result.stdout.splitlines():
        values = line.split()
        if len(values) == 2 and values[0].isdigit() and values[1].isdigit():
            if int(values[1]) == leader_pid and int(values[0]) != leader_pid:
                count += 1
    return count


def _owned_marker_status(workspace: Path, owner_group: int | None) -> dict[str, object]:
    marker = workspace / "owned.pid"
    if not marker.is_file() or marker.is_symlink():
        return {"marker_present": False, "identity_verified": False, "alive": None}
    try:
        raw = marker.read_text().strip()
    except OSError:
        return {"marker_present": True, "identity_verified": False, "alive": None}
    if not re.fullmatch(r"[1-9][0-9]{0,7}", raw):
        return {"marker_present": True, "identity_verified": False, "alive": None}
    pid = int(raw)
    base: dict[str, object] = {
        "marker_present": True,
        "pid": pid,
        "identity_verified": False,
        "alive": None,
    }
    if type(owner_group) is not int or owner_group < 1:
        return base
    try:
        process = subprocess.run(
            ("ps", "-p", str(pid), "-o", "pid=,pgid=,lstart=,comm=,command="),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if process.returncode != 0:
            if (
                process.returncode == 1
                and not process.stdout.strip()
                and not process.stderr.strip()
            ):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    base["alive"] = False
                    base["absence_verified"] = True
                except OSError:
                    pass
            return base
        if len(process.stdout.splitlines()) != 1:
            return base
        fields = process.stdout.strip().split(maxsplit=8)
        if len(fields) != 9 or not fields[0].isdigit() or not fields[1].isdigit():
            return base
        if int(fields[0]) != pid:
            return base
        base["alive"] = True
        command = shlex.split(fields[8])
        if not command:
            return base
        script = workspace / "waiter.py"
        executable = Path(fields[7]).name
        command_executable = Path(command[0]).name
        if (
            not base["alive"]
            or int(fields[1]) != owner_group
            or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable)
            or command_executable != executable
            or script.is_symlink()
            or not script.is_file()
            or len(command) != 2
            or (workspace / command[1]).resolve() != script.resolve()
        ):
            return base
        cwd = subprocess.run(
            ("lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        cwd_lines = cwd.stdout.splitlines()
        if (
            cwd.returncode != 0
            or f"p{pid}" not in cwd_lines
            or "fcwd" not in cwd_lines
            or sum(line.startswith("n") for line in cwd_lines) != 1
        ):
            return base
        cwd_path = Path(next(line[1:] for line in cwd_lines if line.startswith("n")))
        if cwd_path.is_symlink() or cwd_path.resolve() != workspace.resolve():
            return base
        base.update(
            identity_verified=True,
            owner_group=owner_group,
            start_identity=" ".join(fields[2:7]),
            executable=executable,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return base
    return base


def _post_abort_action(prior: dict[str, object], current: dict[str, object]) -> str:
    if prior.get("identity_verified") is not True or prior.get("alive") is not True:
        return "unknown"
    if current.get("pid") != prior.get("pid"):
        return "unknown"
    if current.get("alive") is False and current.get("absence_verified") is True:
        return "continue"
    if current.get("identity_verified") is not True or current.get("alive") is not True:
        return "unknown"
    if all(
        current.get(field) == prior.get(field)
        for field in ("owner_group", "start_identity", "executable")
    ):
        return "terminate"
    return "unknown"


def _signal_owned_waiter(
    workspace: Path, owner_group: int | None, prior: dict[str, object]
) -> bool:
    pid = prior.get("pid")
    if prior.get("identity_verified") is not True or type(pid) is not int:
        return False
    current = _owned_marker_status(workspace, owner_group)
    for field in ("pid", "owner_group", "start_identity", "executable"):
        if current.get(field) != prior.get(field):
            return False
    if current.get("identity_verified") is not True:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


async def _lost_controller_prompt(
    client: LegacyClient, env: dict[str, str], workspace: Path, session_id: str
) -> tuple[str, dict[str, object]]:
    code = (
        "import base64,json,os,secrets,sys,time,urllib.request\n"
        "origin,sid=sys.argv[1:]\n"
        "mid='msg_'+secrets.token_hex(12)\n"
        "body=json.dumps({'parts':[{'type':'text','text':'Write recovered.txt containing recovered, then report completion.'}],'messageID':mid}).encode()\n"
        "auth=base64.b64encode(('opencode:'+os.environ['OPENCODE_SERVER_PASSWORD']).encode()).decode()\n"
        "req=urllib.request.Request(origin+'/session/'+sid+'/prompt_async',body,{'Authorization':'Basic '+auth,'Content-Type':'application/json'},method='POST')\n"
        "with urllib.request.urlopen(req,timeout=5) as response: assert response.status==204\n"
        "print(mid,flush=True)\n"
        "time.sleep(60)\n"
    )
    origin = f"http://127.0.0.1:{client._port}"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        code,
        origin,
        session_id,
        cwd=workspace,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        raw = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        user_id = raw.decode().strip()
        if not re.fullmatch(r"msg_[a-f0-9]{24}", user_id):
            raise ValueError("lost controller did not admit a prompt")
        os.killpg(process.pid, signal.SIGKILL)
        await asyncio.wait_for(process.wait(), timeout=5)
        return user_id, {
            "controller_pid": process.pid,
            "controller_exit_code": process.returncode,
            "same_server_session_id": session_id,
            "new_session_started": False,
        }
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


class _Events:
    def __init__(self, client: LegacyClient) -> None:
        self.client = client
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.lines: list[bytes] = []
        self.invalid = 0
        self.error: str | None = None
        self.thread = threading.Thread(target=self._collect, daemon=True)

    def _collect(self) -> None:
        # The legacy client has already verified this task-owned loopback server.
        conn = http.client.HTTPConnection("127.0.0.1", self.client._port, timeout=180)
        try:
            conn.request("GET", "/event", headers={"Authorization": self.client._auth})
            response = conn.getresponse()
            if response.status != 200:
                self.error = "event_subscription_http_error"
                return
            self.ready.set()
            while not self.stop.is_set() and len(self.lines) < 4096:
                line = response.fp.readline(4097)
                if not line:
                    break
                if len(line) > 4096:
                    self.invalid += 1
                    break
                self.lines.append(line)
        except OSError:
            if not self.stop.is_set():
                self.error = "event_transport_error"
        finally:
            self.ready.set()
            conn.close()

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            raise RuntimeError("event subscription failed")

    def events(self) -> tuple[tuple[dict[str, Any], ...], int]:
        return cast(
            tuple[tuple[dict[str, Any], ...], int], parse_sse(tuple(self.lines))
        )

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)


async def _await_terminal(
    client: LegacyClient, collector: _Events, session_id: str, deadline: float
) -> str:
    observed = 0
    while time.monotonic() < deadline:
        events, _ = collector.events()
        for event in events[observed:]:
            client.observe_event(session_id, event)
        observed = len(events)
        if await asyncio.to_thread(client.reconcile, session_id) == "complete":
            return "complete"
        await asyncio.sleep(0.2)
    return "incomplete"


@contextmanager
def _accounted_gate(
    gate: BudgetGate,
    ledger: ProbeLedger,
    reservation_id: str,
    worst_case_per_attempt_usd: float,
    report: dict[str, Any],
) -> Iterator[BudgetGate]:
    """Settle only after owned gateway handlers have closed and been joined."""
    entered = False
    try:
        with gate:
            entered = True
            yield gate
    finally:
        if entered and gate._closed and gate._server is None and gate._thread is None:
            with gate._lock:
                calls = gate.calls
                records = tuple(gate.records)
                denials = tuple(gate.denials)
                budget_exceeded = gate.budget_exceeded
            missing = calls - len(records)
            if missing < 0:
                raise ValueError("gateway records exceed admitted calls")
            report["gate"] = {
                "calls": calls,
                "budget_exceeded": budget_exceeded,
                "unrecorded_calls": missing,
                "records": [
                    {
                        "gate_request_id": record.gate_request_id,
                        "upstream_request_id": record.request_id,
                        "claimed_session_id": record.claimed_session_id,
                        "status": record.status,
                        "usage_verified": record.usage_verified,
                        "prompt_tokens": record.prompt_tokens,
                        "completion_tokens": record.completion_tokens,
                        "estimated_cost_usd": record.estimated_cost_usd,
                    }
                    for record in records
                ],
                "denials": [
                    {
                        "status": item.status,
                        "reason": item.reason,
                        "claimed_session_id": item.claimed_session_id,
                        "payload_fields": item.payload_fields,
                        "requested_output_tokens": item.requested_output_tokens,
                    }
                    for item in denials
                ],
                "reservation_per_attempt_usd": worst_case_per_attempt_usd,
            }
            if missing:
                report["status"] = "unverified"
                report["terminal"] = "incomplete"
                report["reason"] = "admitted_call_without_final_gateway_record"
            cost = missing * worst_case_per_attempt_usd + sum(
                record.estimated_cost_usd
                if record.usage_verified and record.estimated_cost_usd is not None
                else worst_case_per_attempt_usd
                for record in records
            )
            ledger.settle(reservation_id, calls, cost)


async def run_freeze_experiment(
    *,
    binary: Path,
    gateway_env_file: Path,
    ledger_path: Path,
    max_calls: int,
    max_seconds: int,
    max_cost_usd: float,
) -> dict[str, object]:
    """Keep the original live snapshot while a new task loads a changed revision."""
    ledger = ProbeLedger(ledger_path)
    origin, key = _gateway_values(gateway_env_file)
    input_price, output_price, input_ceiling, output_ceiling = _model_metadata(
        origin, key
    )
    prior_calls, prior_cost = ledger.totals()
    authorized_calls, authorized_cost = ledger.limits()
    call_limit = min(max_calls, 5, authorized_calls - prior_calls)
    cost_limit = min(max_cost_usd, authorized_cost - prior_cost)
    reservation = (input_ceiling * input_price + 512 * output_price) / 1_000_000
    if call_limit < 1 or cost_limit < reservation:
        raise ValueError("cumulative probe budget exhausted")
    reservation_id = ledger.reserve("config-freeze", call_limit, cost_limit)
    report: dict[str, Any] = {
        "source": "opencode-real",
        "scenario": "config-freeze",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "cli_version": "1.19.5",
        "model": "litellm/qwen-plus",
        "status": "unverified",
        "terminal": "incomplete",
        "reason": "freeze_evidence_incomplete",
        "redaction": "revision and profile digests, session IDs, terminal states, gate usage only; prompt and response bodies omitted",
        "prior_ledger": {"calls": prior_calls, "charged_cost_usd": prior_cost},
    }
    temporary = Path(tempfile.mkdtemp(prefix="tap-opencode-freeze-")).resolve()
    try:
        old_workspace = temporary / "old"
        new_workspace = temporary / "new"
        source = temporary / "source_revision"
        old_workspace.mkdir(mode=0o700)
        new_workspace.mkdir(mode=0o700)
        source.write_text("probe-0.3-freeze-r1\n")
        first_revision = source.read_text().strip()
        old_agents = _profiles(orchestrator_revision=first_revision)
        old_profiles = export_profiles(
            old_workspace, old_agents, frozenset(agent.agent_id for agent in old_agents)
        )
        report["old_profile_provenance"] = _profile_provenance(old_agents, old_profiles)
        old_config = export_probe_config(old_workspace)
        report["config_digest"] = old_config
        old_hash = old_profiles[".opencode/agents/orchestrator.md"]
        with _accounted_gate(
            BudgetGate(
                origin,
                key,
                max_calls=call_limit,
                max_seconds=min(max_seconds, 180),
                max_cost_usd=cost_limit,
                input_price_per_million=input_price,
                output_price_per_million=output_price,
                max_input_tokens=input_ceiling,
                max_output_tokens=512,
            ),
            ledger,
            reservation_id,
            reservation,
            report,
        ) as gate:
            try:
                old_env = make_child_env(
                    old_workspace,
                    gate.url,
                    gate.token,
                    secrets.token_urlsafe(24),
                    model_input_limit=input_ceiling,
                    model_output_limit=512,
                )
                deadline = time.monotonic() + min(max_seconds, 180) - 5
                async with serve_probe(
                    (str(binary.resolve()),),
                    old_workspace,
                    old_env,
                    timeout_seconds=min(max_seconds, 180),
                    profile_hashes=old_profiles,
                    config_digest=old_config,
                ) as old_client:
                    old_session = await asyncio.to_thread(old_client.create_session)
                    report["root_session_id"] = old_session
                    old_events = _Events(old_client)
                    old_events.start()
                    try:
                        await asyncio.to_thread(
                            old_client.prompt_async,
                            old_session,
                            [
                                {
                                    "type": "text",
                                    "text": "Run bash command sleep 8, then answer 42. This is a synthetic config freeze test.",
                                }
                            ],
                        )
                        saw_busy = False
                        while time.monotonic() < min(deadline, time.monotonic() + 15):
                            events, _ = old_events.events()
                            saw_busy = any(
                                event.get("type") == "session.status"
                                and isinstance(event.get("properties"), dict)
                                and event["properties"].get("sessionID") == old_session
                                and event["properties"].get("status")
                                == {"type": "busy"}
                                for event in events
                            )
                            if saw_busy:
                                break
                            await asyncio.sleep(0.1)
                        report["freeze"] = {
                            "old_busy_at_source_change": saw_busy,
                            "old_revision": first_revision,
                        }
                        source.write_text("probe-0.3-freeze-r2\n")
                        second_revision = source.read_text().strip()
                        new_agents = _profiles(orchestrator_revision=second_revision)
                        new_profiles = export_profiles(
                            new_workspace,
                            new_agents,
                            frozenset(agent.agent_id for agent in new_agents),
                        )
                        report["new_profile_provenance"] = _profile_provenance(
                            new_agents, new_profiles
                        )
                        new_config = export_probe_config(new_workspace)
                        report["freeze"].update(
                            {
                                "new_revision": second_revision,
                                "old_profile_hash_before": old_hash,
                                "old_profile_hash_after": hashlib.sha256(
                                    (
                                        old_workspace
                                        / ".opencode/agents/orchestrator.md"
                                    ).read_bytes()
                                ).hexdigest(),
                                "new_profile_hash": new_profiles[
                                    ".opencode/agents/orchestrator.md"
                                ],
                                "new_config_digest": new_config,
                            }
                        )
                        new_env = make_child_env(
                            new_workspace,
                            gate.url,
                            gate.token,
                            secrets.token_urlsafe(24),
                            model_input_limit=input_ceiling,
                            model_output_limit=512,
                        )
                        async with serve_probe(
                            (str(binary.resolve()),),
                            new_workspace,
                            new_env,
                            timeout_seconds=min(max_seconds, 180),
                            profile_hashes=new_profiles,
                            config_digest=new_config,
                        ) as new_client:
                            new_session = await asyncio.to_thread(
                                new_client.create_session
                            )
                            report["freeze"]["new_session_id"] = new_session
                            new_events = _Events(new_client)
                            new_events.start()
                            try:
                                await asyncio.to_thread(
                                    new_client.prompt_async,
                                    new_session,
                                    [
                                        {
                                            "type": "text",
                                            "text": "What is 6 times 7? Reply with just the number. Do not use tools.",
                                        }
                                    ],
                                )
                                report["freeze"][
                                    "new_terminal"
                                ] = await _await_terminal(
                                    new_client, new_events, new_session, deadline
                                )
                            finally:
                                new_events.close()
                        report["freeze"]["old_terminal"] = await _await_terminal(
                            old_client, old_events, old_session, deadline
                        )
                        report["sessions"] = [old_session, new_session]
                        if (
                            report["freeze"]["old_terminal"]
                            == report["freeze"]["new_terminal"]
                            == "complete"
                        ):
                            report["terminal"] = "complete"
                        if (
                            report["terminal"] == "complete"
                            and saw_busy
                            and report["freeze"]["old_profile_hash_before"]
                            == report["freeze"]["old_profile_hash_after"]
                            and old_hash != report["freeze"]["new_profile_hash"]
                        ):
                            report["status"] = "supported"
                            report["reason"] = (
                                "old_live_snapshot_unchanged_new_session_loaded_revision"
                            )
                    finally:
                        old_events.close()
            except Exception as error:
                report["reason"] = "probe_error:" + type(error).__name__
    finally:
        shutil.rmtree(temporary)
    calls, charged = ledger.totals()
    report["cumulative_ledger"] = {"calls": calls, "charged_cost_usd": charged}
    return report


def _gateway_values(env_file: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name in {"LITELLM_BASE_URL", "LITELLM_MASTER_KEY"}:
            values[name] = value.strip().strip('"').strip("'")
    origin = values.get("LITELLM_BASE_URL", "")
    key = values.get("LITELLM_MASTER_KEY", "")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or not key
    ):
        raise ValueError("trusted loopback gateway configuration unavailable")
    return origin.rstrip("/"), key


def _model_metadata(origin: str, key: str) -> tuple[float, float, int, int]:
    parsed = urlsplit(origin)
    connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
    try:
        connection.request(
            "GET", "/v1/model/info", headers={"Authorization": f"Bearer {key}"}
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("trusted model metadata unavailable")
        document = json.loads(response.read(1_048_576))
    finally:
        connection.close()
    matches = [
        row for row in document.get("data", []) if row.get("model_name") == "qwen-plus"
    ]
    if len(matches) != 1:
        raise ValueError("approved model alias unavailable")
    info = matches[0]["model_info"]
    input_price = info.get("input_cost_per_token")
    output_price = info.get("output_cost_per_token")
    input_ceiling = info.get("max_input_tokens")
    output_ceiling = info.get("max_output_tokens")
    if (
        not isinstance(input_price, (int, float))
        or input_price <= 0
        or not isinstance(output_price, (int, float))
        or output_price <= 0
        or type(input_ceiling) is not int
        or input_ceiling <= 0
        or type(output_ceiling) is not int
        or output_ceiling < 512
    ):
        raise ValueError("approved model price or context ceiling untrusted")
    return (
        float(input_price) * 1_000_000,
        float(output_price) * 1_000_000,
        input_ceiling,
        output_ceiling,
    )


def _tool_denial_category(state: object) -> str | None:
    if not isinstance(state, dict) or state.get("status") != "error":
        return None
    error = state.get("error")
    if isinstance(error, dict) and (
        error.get("name") == "PermissionDeniedError"
        or error.get("code") == "PERMISSION_DENIED"
        or error.get("type") == "permission_denied"
    ):
        return "opencode_permission_denied"
    return None


def _tool_target_matches(raw_path: object, workspace: Path) -> bool:
    if not isinstance(raw_path, str) or not raw_path:
        return False
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return (
        not candidate.is_symlink()
        and candidate.resolve() == (workspace / "denied.txt").resolve()
    )


def _permission_target_digest(workspace: Path) -> str | None:
    target = workspace / "denied.txt"
    if target.is_symlink():
        return "unsafe"
    if not target.exists():
        return None
    if not target.is_file():
        return "unsafe"
    return _bounded_artifact_digest(target) or "unsafe"


def _facts(client: LegacyClient, session_id: str, workspace: Path) -> dict[str, Any]:
    root_status, messages = client._request("GET", f"/session/{session_id}/message")
    child_status, children = client._request("GET", f"/session/{session_id}/children")
    if (
        root_status != 200
        or child_status != 200
        or not isinstance(messages, list)
        or not isinstance(children, list)
    ):
        return {"readable": False}
    root_parts: list[dict[str, object]] = []
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        for part in entry.get("parts", []):
            if isinstance(part, dict) and part.get("type") == "tool":
                state = part.get("state")
                metadata = state.get("metadata") if isinstance(state, dict) else None
                root_parts.append(
                    {
                        "tool": part.get("tool"),
                        "status": state.get("status")
                        if isinstance(state, dict)
                        else None,
                        "child_session_id": metadata.get("sessionId")
                        if isinstance(metadata, dict)
                        else None,
                    }
                )
    child_facts = []
    for child in children:
        if not isinstance(child, dict) or not isinstance(child.get("id"), str):
            continue
        status, child_messages = client._request(
            "GET", f"/session/{child['id']}/message"
        )
        child_assistants = (
            [
                entry["info"]
                for entry in child_messages
                if isinstance(entry, dict)
                and isinstance(entry.get("info"), dict)
                and entry["info"].get("role") == "assistant"
            ]
            if status == 200 and isinstance(child_messages, list)
            else []
        )
        child_tools = []
        for entry in (
            child_messages if status == 200 and isinstance(child_messages, list) else []
        ):
            if not isinstance(entry, dict) or not isinstance(entry.get("parts"), list):
                continue
            for part in entry["parts"]:
                if not isinstance(part, dict) or part.get("type") != "tool":
                    continue
                state = part.get("state")
                payload = state.get("input") if isinstance(state, dict) else None
                raw_path = (
                    payload.get("filePath", payload.get("path"))
                    if isinstance(payload, dict)
                    else None
                )
                basename = Path(raw_path).name if isinstance(raw_path, str) else None
                child_tools.append(
                    {
                        "tool": part.get("tool"),
                        "status": state.get("status")
                        if isinstance(state, dict)
                        else None,
                        "file": basename
                        if basename in {"one.py", "two.py", "square.py", "denied.txt"}
                        else None,
                        "target_is_denied_workspace_file": _tool_target_matches(
                            raw_path, workspace
                        )
                        if part.get("tool") == "write"
                        else False,
                        "denial_category": _tool_denial_category(state),
                    }
                )
        starts = [
            info.get("time", {}).get("created")
            for info in child_assistants
            if isinstance(info.get("time"), dict)
            and isinstance(info["time"].get("created"), (int, float))
        ]
        ends = [
            info.get("time", {}).get("completed")
            for info in child_assistants
            if isinstance(info.get("time"), dict)
            and isinstance(info["time"].get("completed"), (int, float))
        ]
        child_facts.append(
            {
                "id": child["id"],
                "parent_id": child.get("parentID"),
                "agent": child.get("agent"),
                "message_count": len(child_messages)
                if status == 200 and isinstance(child_messages, list)
                else None,
                "assistant_finishes": [info.get("finish") for info in child_assistants],
                "assistant_errors": [
                    _safe_error(info.get("error")) for info in child_assistants
                ],
                "started_ms": min(starts) if starts else None,
                "ended_ms": max(ends) if ends else None,
                "tools": child_tools,
            }
        )
    assistant = [
        entry.get("info", {})
        for entry in messages
        if isinstance(entry, dict)
        and isinstance(entry.get("info"), dict)
        and entry["info"].get("role") == "assistant"
    ]
    final_text = ""
    for entry in messages:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("info"), dict)
            and entry["info"].get("role") == "assistant"
        ):
            parts = entry.get("parts")
            if isinstance(parts, list):
                final_text = "".join(
                    str(part.get("text", ""))
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "text"
                )
    return {
        "readable": True,
        "root_message_count": len(messages),
        "assistant_finishes": [info.get("finish") for info in assistant],
        "assistant_parent_ids": [info.get("parentID") for info in assistant],
        "assistant_errors": [_safe_error(info.get("error")) for info in assistant],
        "answer_is_42": final_text.strip() == "42",
        "tools": root_parts,
        "children": child_facts,
    }


def _safe_error(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {}
    for key in ("name", "code", "statusCode", "type"):
        field = value.get(key)
        if isinstance(field, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", field):
            result[key] = field
        elif key == "statusCode" and type(field) is int:
            result[key] = field
    data = value.get("data")
    if isinstance(data, dict):
        nested = _safe_error(data)
        if nested:
            result["data"] = nested
    return result or {"present": True}


def _first_action_count(workspace: Path) -> int | None:
    marker = workspace / "first-action.log"
    if not marker.is_file() or marker.is_symlink():
        return None
    try:
        with marker.open("rb") as source:
            raw = source.read(257)
    except OSError:
        return None
    if len(raw) > 256:
        return None
    lines = raw.splitlines(keepends=True)
    return len(lines) if lines and all(line == b"start\n" for line in lines) else None


def _resume_preconditions(
    workspace: Path,
    *,
    active_root: bool,
    active_bash: bool,
    marker: dict[str, object],
    fixture_digest: str | None = None,
) -> dict[str, object]:
    first = workspace / "first.txt"
    return {
        "first_present": first.is_file() and not first.is_symlink(),
        "second_absent": not (workspace / "second.txt").exists(),
        "root_busy": active_root,
        "bash_running": active_bash,
        "owned_process_verified": marker.get("identity_verified") is True,
        "first_action_count": _first_action_count(workspace),
        "fixture_unchanged": fixture_digest is not None
        and _bounded_artifact_digest(workspace / "waiter.py") == fixture_digest,
    }


async def run_scenario(
    scenario: str,
    *,
    binary: Path,
    gateway_env_file: Path,
    ledger_path: Path,
    max_calls: int,
    max_seconds: int,
    max_cost_usd: float = 0.50,
) -> dict[str, object]:
    if scenario not in _PROMPTS or not binary.is_file():
        raise ValueError("unknown scenario or missing pinned binary")
    ledger = ProbeLedger(ledger_path)
    prior_calls, prior_cost = ledger.totals()
    origin, key = _gateway_values(gateway_env_file)
    input_price, output_price, input_ceiling, output_ceiling = _model_metadata(
        origin, key
    )
    authorized_calls, authorized_cost = ledger.limits()
    remaining_calls = min(max_calls, 10, authorized_calls - prior_calls)
    remaining_cost = min(max_cost_usd, authorized_cost - prior_cost)
    reservation = (
        input_ceiling * input_price + min(512, output_ceiling) * output_price
    ) / 1_000_000
    if remaining_calls < 1 or remaining_cost < reservation:
        raise ValueError("cumulative probe budget exhausted")
    reservation_id = ledger.reserve(scenario, remaining_calls, remaining_cost)
    report: dict[str, Any] = {
        "source": "opencode-real",
        "scenario": scenario,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "cli_version": "1.19.5",
        "model": "litellm/qwen-plus",
        "terminal": "incomplete",
        "status": "unverified",
        "reason": "real_evidence_incomplete",
        "redaction": "only session IDs, event types, tool states, artifact SHA-256, gate counts and usage retained; prompts, responses and credentials omitted",
        "prior_ledger": {"calls": prior_calls, "charged_cost_usd": prior_cost},
    }
    temporary = tempfile.mkdtemp(prefix="tap-opencode-0-3-")
    try:
        workspace = Path(temporary).resolve()
        roster_count = (
            int(scenario.split("-")[1]) if scenario.startswith("roster-") else 4
        )
        roster = _profiles(roster_count)
        profiles = export_profiles(
            workspace, roster, frozenset(agent.agent_id for agent in roster)
        )
        report["profile_provenance"] = _profile_provenance(roster, profiles)
        config_digest = export_probe_config(workspace)
        report["config_digest"] = config_digest
        if scenario == "resume-workspace":
            report["resume"] = {
                "supervisor_fixture_sha256": prepare_resume_fixture(workspace),
                "fixture_provenance": "supervisor_prepared_synthetic_waiter",
            }
        if scenario.startswith("roster-"):
            report["roster"] = {"allowed_agents": roster_count}
        with _accounted_gate(
            BudgetGate(
                origin,
                key,
                max_calls=remaining_calls,
                max_seconds=min(max_seconds, 180),
                max_cost_usd=remaining_cost,
                input_price_per_million=input_price,
                output_price_per_million=output_price,
                max_input_tokens=input_ceiling,
                max_output_tokens=512,
            ),
            ledger,
            reservation_id,
            reservation,
            report,
        ) as gate:
            env = make_child_env(
                workspace,
                gate.url,
                gate.token,
                secrets.token_urlsafe(24),
                model_input_limit=input_ceiling,
                model_output_limit=512,
            )
            collector: _Events | None = None
            try:
                report["last_stage"] = "serve_preflight"
                started_at = time.monotonic()
                async with serve_probe(
                    (str(binary.resolve()),),
                    workspace,
                    env,
                    timeout_seconds=min(max_seconds, 180),
                    profile_hashes=profiles,
                    config_digest=config_digest,
                ) as client:
                    if scenario.startswith("roster-"):
                        report["roster"]["startup_seconds"] = round(
                            time.monotonic() - started_at, 3
                        )
                    report["last_stage"] = "create_session"
                    session_id = await asyncio.to_thread(client.create_session)
                    report["root_session_id"] = session_id
                    collector = _Events(client)
                    report["last_stage"] = "event_subscribe"
                    collector.start()
                    report["last_stage"] = "prompt_admission"
                    if scenario == "permissions":
                        report["permission_target"] = {
                            "before": _permission_target_digest(workspace)
                        }
                    if scenario == "reconcile":
                        user_id, loss = await _lost_controller_prompt(
                            client, env, workspace, session_id
                        )
                        report["reconcile_loss"] = loss
                    else:
                        user_id = await asyncio.to_thread(
                            client.prompt_async,
                            session_id,
                            [{"type": "text", "text": _PROMPTS[scenario]}],
                        )
                    report["user_message_id"] = user_id
                    report["last_stage"] = "reconcile"
                    observed = 0
                    steered = False
                    aborted = False
                    resumed = False
                    abort_at = 0.0
                    abort_event_index = 0
                    deadline = time.monotonic() + min(max_seconds, 180) - 5
                    while time.monotonic() < deadline:
                        events, invalid = collector.events()
                        for event in events[observed:]:
                            client.observe_event(session_id, event)
                        observed = len(events)
                        root_states = [
                            event["properties"].get("status", {}).get("type")
                            for event in events
                            if event.get("type") == "session.status"
                            and isinstance(event.get("properties"), dict)
                            and event["properties"].get("sessionID") == session_id
                            and isinstance(event["properties"].get("status"), dict)
                        ]
                        active_root = bool(root_states and root_states[-1] == "busy")
                        if (
                            scenario == "control-receipt"
                            and not steered
                            and gate.calls >= 2
                        ):
                            first_path = workspace / "first.txt"
                            report["control"] = {
                                "submitted_during_active_root": active_root,
                                "first_digest_at_submission": _bounded_artifact_digest(
                                    first_path
                                )
                                if first_path.is_file()
                                else None,
                            }
                            try:
                                second_id = await asyncio.to_thread(
                                    client.prompt_async,
                                    session_id,
                                    [
                                        {
                                            "type": "text",
                                            "text": "Additional requirement: write second.txt containing second. Preserve first.txt.",
                                        }
                                    ],
                                )
                                report["control"]["accepted_message_id"] = second_id
                                steered = True
                            except Exception as error:
                                report["control"]["admission_error"] = type(
                                    error
                                ).__name__
                                break
                        if scenario == "resume-workspace":
                            first_path = workspace / "first.txt"
                            second_path = workspace / "second.txt"
                            active_bash = any(
                                event.get("type") == "message.part.updated"
                                and isinstance(event.get("properties"), dict)
                                and isinstance(event["properties"].get("part"), dict)
                                and event["properties"]["part"].get("tool") == "bash"
                                and isinstance(
                                    event["properties"]["part"].get("state"), dict
                                )
                                and event["properties"]["part"]["state"].get("status")
                                == "running"
                                for event in events
                            )
                            marker = _owned_marker_status(workspace, client.serve_pid)
                            conditions = _resume_preconditions(
                                workspace,
                                active_root=active_root,
                                active_bash=active_bash,
                                marker=marker,
                                fixture_digest=report["resume"][
                                    "supervisor_fixture_sha256"
                                ],
                            )
                            if not aborted:
                                report["resume"]["pre_abort_conditions"] = conditions
                            if (
                                conditions["first_present"]
                                and conditions["second_absent"]
                                and not aborted
                                and conditions["root_busy"]
                                and conditions["bash_running"]
                                and conditions["owned_process_verified"]
                                and conditions["first_action_count"] == 1
                                and conditions["fixture_unchanged"]
                            ):
                                first_digest = _bounded_artifact_digest(first_path)
                                if first_digest is None:
                                    report["reason"] = "first_artifact_limit"
                                    break
                                report["resume"].update(
                                    {
                                        "first_digest_before_abort": first_digest,
                                        "first_action_count_before_abort": _first_action_count(
                                            workspace
                                        ),
                                        "second_absent_before_abort": True,
                                        "was_active_at_abort": active_root,
                                        "running_bash_observed": active_bash,
                                        "process_group_children_before_abort": _process_group_children(
                                            client.serve_pid
                                        ),
                                        "owned_marker_before_abort": marker,
                                    }
                                )
                                try:
                                    report["resume"][
                                        "abort_result"
                                    ] = await asyncio.to_thread(
                                        client.abort, session_id
                                    )
                                    aborted = True
                                    abort_at = time.monotonic()
                                    abort_event_index = observed
                                except Exception as error:
                                    report["resume"]["abort_error"] = type(
                                        error
                                    ).__name__
                                    break
                            elif (
                                aborted
                                and not resumed
                                and time.monotonic() - abort_at >= 1
                                and any(
                                    event.get("type") == "session.idle"
                                    and isinstance(event.get("properties"), dict)
                                    and event["properties"].get("sessionID")
                                    == session_id
                                    for event in events[abort_event_index:]
                                )
                            ):
                                report["resume"]["first_digest_after_abort"] = (
                                    _bounded_artifact_digest(first_path)
                                    if first_path.is_file()
                                    else None
                                )
                                if report["resume"]["first_digest_after_abort"] is None:
                                    report["reason"] = "first_artifact_limit"
                                    break
                                report["resume"]["first_action_count_after_abort"] = (
                                    _first_action_count(workspace)
                                )
                                report["resume"][
                                    "second_absent_after_abort"
                                ] = not second_path.exists()
                                report["resume"][
                                    "process_group_children_after_abort"
                                ] = _process_group_children(client.serve_pid)
                                after_marker = _owned_marker_status(
                                    workspace, client.serve_pid
                                )
                                report["resume"]["owned_marker_after_abort"] = (
                                    after_marker
                                )
                                prior_marker = report["resume"].get(
                                    "owned_marker_before_abort"
                                )
                                action = _post_abort_action(
                                    prior_marker
                                    if isinstance(prior_marker, dict)
                                    else {},
                                    after_marker,
                                )
                                report["resume"]["post_abort_process_action"] = action
                                if action == "terminate":
                                    report["resume"][
                                        "owned_process_terminated_by_supervisor"
                                    ] = _signal_owned_waiter(
                                        workspace, client.serve_pid, after_marker
                                    )
                                    if not report["resume"][
                                        "owned_process_terminated_by_supervisor"
                                    ]:
                                        report["reason"] = (
                                            "owned_process_identity_changed"
                                        )
                                        break
                                    await asyncio.sleep(0.2)
                                    continue
                                if action != "continue":
                                    report["reason"] = "owned_process_unverified"
                                    break
                                try:
                                    continuation_id = await asyncio.to_thread(
                                        client.prompt_async,
                                        session_id,
                                        [
                                            {
                                                "type": "text",
                                                "text": "Continue in this same session: write only second.txt containing second. Preserve first.txt. Do not run, edit, or recreate waiter.py; do not repeat the first action.",
                                            }
                                        ],
                                    )
                                    report["resume"]["continuation_message_id"] = (
                                        continuation_id
                                    )
                                    report["resume"]["continued_session_id"] = (
                                        session_id
                                    )
                                    resumed = True
                                except Exception as error:
                                    report["resume"]["continuation_error"] = type(
                                        error
                                    ).__name__
                                    break
                        root_idle = any(
                            event.get("type") == "session.idle"
                            and isinstance(event.get("properties"), dict)
                            and event["properties"].get("sessionID") == session_id
                            for event in events
                        )
                        root_errors = [
                            _safe_error(event.get("properties", {}).get("error"))
                            for event in events
                            if event.get("type") == "session.error"
                            and isinstance(event.get("properties"), dict)
                            and event["properties"].get("sessionID") == session_id
                        ]
                        if root_errors:
                            report["session_error_codes"] = root_errors
                        expected_abort = (
                            scenario == "resume-workspace"
                            and aborted
                            and all(
                                isinstance(error, dict)
                                and error.get("name") == "MessageAbortedError"
                                for error in root_errors
                            )
                        )
                        if (
                            root_idle
                            and root_errors
                            and not expected_abort
                            and scenario != "permissions"
                        ):
                            report["reason"] = "native_session_error"
                            break
                        if (
                            root_idle
                            and not (scenario == "control-receipt" and not steered)
                            and not (scenario == "resume-workspace" and not resumed)
                        ):
                            try:
                                if scenario == "reconcile":
                                    (
                                        status,
                                        recovered_messages,
                                    ) = await asyncio.to_thread(
                                        client._request,
                                        "GET",
                                        f"/session/{session_id}/message",
                                    )
                                    outcome = (
                                        classify_session(
                                            True,
                                            recovered_messages,
                                            [],
                                            expected_user_id=user_id,
                                        )
                                        if status == 200
                                        and isinstance(recovered_messages, list)
                                        else "incomplete"
                                    )
                                    report["reconcile_loss"]["old_session_found"] = (
                                        status == 200
                                    )
                                else:
                                    outcome = await asyncio.to_thread(
                                        client.reconcile, session_id
                                    )
                                if outcome == "complete":
                                    report["terminal"] = "complete"
                                    break
                                if scenario == "permissions" and outcome == "failed":
                                    report["terminal"] = "failed"
                                    break
                            except OSError:
                                pass
                        if gate.budget_exceeded:
                            break
                        await asyncio.sleep(0.5)
                    collector.close()
                    report["last_stage"] = "facts"
                    events, invalid = collector.events()
                    report["event_types"] = sorted(
                        {str(event.get("type")) for event in events}
                    )
                    report["event_count"] = len(events)
                    report["invalid_events"] = invalid + collector.invalid
                    report["event_error"] = collector.error
                    facts = await asyncio.to_thread(
                        _facts, client, session_id, workspace
                    )
                    report["session_facts"] = facts
                    if scenario == "permissions":
                        report["permission_target"]["after"] = (
                            _permission_target_digest(workspace)
                        )
                    if scenario.startswith("roster-") and isinstance(facts, dict):
                        report["roster"]["selected_agent"] = [
                            child.get("agent")
                            for child in facts.get("children", [])
                            if isinstance(child, dict)
                        ]
                    children = (
                        facts.get("children") if isinstance(facts, dict) else None
                    )
                    session_ids = (
                        [session_id]
                        + [
                            child["id"]
                            for child in children
                            if isinstance(child, dict)
                            and isinstance(child.get("id"), str)
                        ]
                        if isinstance(children, list)
                        else [session_id]
                    )
                    report["sessions"] = session_ids
                    artifact_names = (
                        "square.py",
                        "one.py",
                        "two.py",
                        "first.txt",
                        "second.txt",
                        "recovered.txt",
                        "budget.txt",
                        "denied.txt",
                    )
                    artifacts: dict[str, str] = {}
                    artifact_limits: list[str] = []
                    for name in artifact_names:
                        path = workspace / name
                        if path.is_file() and not path.is_symlink():
                            digest = _bounded_artifact_digest(path)
                            if digest is None:
                                artifact_limits.append(name)
                            else:
                                artifacts[name] = digest
                    report["artifacts"] = artifacts
                    if artifact_limits:
                        report["artifact_limits"] = artifact_limits
                    if scenario == "resume-workspace" and "resume" in report:
                        report["resume"]["first_action_count_final"] = (
                            _first_action_count(workspace)
                        )
                        report["resume"]["fixture_unchanged_final"] = (
                            _bounded_artifact_digest(workspace / "waiter.py")
                            == report["resume"]["supervisor_fixture_sha256"]
                        )
                    script = workspace / "square.py"
                    if script.is_file() and not script.is_symlink():
                        source = _read_bounded_script(script)
                        safe = source is not None and _safe_square_script(source)
                        script_check: dict[str, object] = {
                            "safe_ast": safe,
                            "exit_code": None,
                            "expected_output": False,
                        }
                        if not safe:
                            script_check["limit_reason"] = (
                                "source_size_or_encoding"
                                if source is None
                                else "unsafe_expression"
                            )
                        if safe:
                            exit_code, stdout, limit = _run_square_script(
                                script, workspace
                            )
                            script_check.update(
                                {
                                    "exit_code": exit_code,
                                    "expected_output": exit_code == 0
                                    and stdout is not None
                                    and _square_output_matches(stdout),
                                    "synthetic_stdout": stdout
                                    if stdout is not None
                                    and all(
                                        character.isprintable() or character in "\n\r\t"
                                        for character in stdout
                                    )
                                    else None,
                                }
                            )
                            if limit is not None:
                                script_check["limit_reason"] = limit
                        report["script_check"] = script_check
                    if (
                        artifact_limits
                        or (
                            "script_check" in report
                            and "limit_reason" in report["script_check"]
                        )
                        or (
                            scenario == "resume-workspace"
                            and report["resume"]["fixture_unchanged_final"] is not True
                        )
                    ):
                        report["provider_terminal"] = report["terminal"]
                        report["terminal"] = "incomplete"
                        report["status"] = "unverified"
                        report["reason"] = "artifact_inspection_limit"
                        report["last_stage"] = "artifact_limit"
                    else:
                        report["last_stage"] = "complete"
            except Exception as error:
                report["reason"] = "probe_error:" + type(error).__name__
            finally:
                if collector is not None:
                    collector.close()
        if scenario.startswith("roster-") and "gate" in report:
            report["roster"]["input_tokens_observed"] = sum(
                record["prompt_tokens"]
                for record in report["gate"]["records"]
                if record["prompt_tokens"] is not None
            )
    finally:
        if os.environ.get("TAP_OPENCODE_RETAIN_DIAGNOSTICS") == "1":
            report["private_diagnostic_workspace"] = temporary
        else:
            shutil.rmtree(temporary)
    report["cumulative_ledger"] = {
        "calls": ledger.totals()[0],
        "charged_cost_usd": ledger.totals()[1],
    }
    final_facts = report.get("session_facts")
    if (
        scenario == "simple-question"
        and verified_provenance(report)
        and isinstance(final_facts, dict)
        and final_facts.get("answer_is_42") is True
        and final_facts.get("children") == []
        and final_facts.get("tools") == []
        and report["gate"].get("denials") == []
    ):
        report["status"] = "supported"
        report["reason"] = "answer_and_root_only_verified"
    return report
