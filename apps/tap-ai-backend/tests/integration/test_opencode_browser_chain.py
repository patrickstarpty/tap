"""Opt-in owned MySQL/Docker proof for the OpenCode to Task2 HTTP boundary."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

import pytest
import uvicorn
from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tap_platform.browser_automation.docker import BrowserRuntime, SubprocessDockerClient
from tap_platform.browser_automation.http import (
    browser_service_from_environment,
    create_browser_app,
)
from tap_platform.browser_automation.objects import BrowserEvidenceSink
from tap_platform.browser_automation.worker import BrowserWorker

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.workflows.adapters.browser_client import BrowserApiClient, canonical_json_bytes
from tap.modules.workflows.adapters.coding_mailbox import CodingMailbox
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.opencode_profiles import build_config
from tap.modules.workflows.adapters.opencode_runtime import (
    CodingConfig,
    CodingRuntime,
    DockerCodingContainer,
)
from tap.modules.workflows.ports.coding import CodingResult

IMAGE = "tapper-browser-runtime:1.62.1-arm64"
CODING_IMAGE = "tapper-coding-runtime:1.19.5-arm64"


def write_diagnostic(
    path: Path,
    task_id: str,
    snapshot: dict,
    result: CodingResult | None,
    error_type: str | None,
) -> None:
    settlements = []
    for event in snapshot["events"]:
        if event.kind == "action.settled":
            value = event.payload.get("result", {})
            settlements.append(
                {
                    "action_id": event.payload.get("action_id"),
                    **{
                        key: value.get(key)
                        for key in (
                            "status",
                            "usage_verified",
                            "prompt_tokens",
                            "completion_tokens",
                            "cost_usd",
                        )
                    },
                }
            )
    diagnostic = {
        "task_id": task_id,
        "status": result.status if result else None,
        "error_type": error_type,
        "budget_used": snapshot["budget_used"],
        "gate": result.usage if result else None,
        "stop_facts": (
            {
                key: result.terminal_evidence.get(key)
                for key in ("coding_stopped", "inspector_status", "primary_error_type")
            }
            if result
            else None
        ),
        "settlements": settlements,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(diagnostic, file, sort_keys=True)


def command(*args: str, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, **kwargs)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("TAP_RUN_CODING_INTEGRATION") != "1",
    reason="requires owned Docker images and isolated MySQL",
)
async def test_actual_task2_http_tool_loop_and_frozen_execution(
    tmp_path: Path, monkeypatch
) -> None:
    mysql = f"tap-task3-mysql-{os.getpid()}"
    site = f"tap-task3-site-{os.getpid()}"
    command(
        "docker",
        "run",
        "--rm",
        "-d",
        "--name",
        mysql,
        "--label",
        "tap.owner=browser-task3",
        "-e",
        "MYSQL_ROOT_PASSWORD=task3-local-only",
        "-e",
        "MYSQL_DATABASE=tap_task3",
        "-p",
        "127.0.0.1::3306",
        "mysql:8.4.6",
    )
    api_task = worker_task = None
    api_server = None
    coding = None
    site_started = False
    try:
        for _ in range(80):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    mysql,
                    "mysqladmin",
                    "ping",
                    "-h",
                    "127.0.0.1",
                    "-ptask3-local-only",
                    "--silent",
                ],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("owned MySQL startup deadline")
        port = command("docker", "port", mysql, "3306/tcp").stdout.strip().rsplit(":", 1)[1]
        database = f"mysql+pymysql://root:task3-local-only@127.0.0.1:{port}/tap_task3"
        workflow_database = (
            f"mysql+pymysql://root:task3-local-only@127.0.0.1:{port}/tap_task3_workflow"
        )
        command(
            "docker",
            "exec",
            mysql,
            "mysql",
            "-uroot",
            "-ptask3-local-only",
            "-e",
            "CREATE DATABASE tap_task3_workflow",
        )
        migration_env = {**os.environ, "TAP_DATABASE_URL": database}
        command(
            ".venv/bin/alembic",
            "-c",
            "apps/backend/alembic.ini",
            "upgrade",
            "head",
            env=migration_env,
        )
        command(
            ".venv/bin/alembic",
            "-c",
            "apps/tap-ai-backend/alembic.ini",
            "upgrade",
            "head",
            env={**os.environ, "TAP_ALEMBIC_DATABASE_URL": workflow_database},
        )
        command(
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            site,
            "--label",
            "tap.owner=browser-task3",
            "--network",
            "bridge",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=16m,uid=10001,gid=10001",
            IMAGE,
            "node",
            "/opt/browser/synthetic-site.mjs",
        )
        site_started = True
        raw_site = json.loads(command("docker", "inspect", site).stdout)
        site_ip = raw_site[0]["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        monkeypatch.setenv("TAP_BROWSER_DATABASE_URL", database)
        monkeypatch.setenv("TAP_BROWSER_OBJECT_ROOT", str(tmp_path / "objects"))
        monkeypatch.setenv("TAP_BROWSER_PROJECT_ID", "task3")
        monkeypatch.setenv("TAP_BROWSER_ACCESS_TOKEN", "x" * 16)
        monkeypatch.setenv(
            "TAP_BROWSER_TOKEN_EXPIRES_AT", datetime(2099, 1, 1, tzinfo=UTC).isoformat()
        )
        service, principal, _ = browser_service_from_environment()
        assert service is not None and principal is not None
        runtime = BrowserRuntime(
            SubprocessDockerClient(),
            IMAGE,
            evidence_sink=BrowserEvidenceSink(service.store, service.objects),
            project_id="task3",
            test_target=f"http://{site_ip}:8080",
        )
        worker = BrowserWorker(service, runtime, "task3")
        worker_task = asyncio.create_task(worker.run(0.05))
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            api_port = address.getsockname()[1]
        api_server = uvicorn.Server(
            uvicorn.Config(
                create_browser_app(),
                host="127.0.0.1",
                port=api_port,
                log_level="error",
                access_log=False,
            )
        )
        api_task = asyncio.create_task(api_server.serve())
        for _ in range(100):
            if api_server.started:
                break
            await asyncio.sleep(0.05)
        assert api_server.started
        client = BrowserApiClient(f"http://127.0.0.1:{api_port}", "task3", "x" * 16)
        task_id = "task3-integration"
        session = await client.session(task_id, ["http://example.test"], 90, "session-task3")
        assert session["status"] == "active"
        session_id = session["session_id"]
        actual_image = command(
            "docker", "image", "inspect", CODING_IMAGE, "--format", "{{.Id}}"
        ).stdout.strip()
        config = CodingConfig(
            f"http://127.0.0.1:{api_port}",
            "task3",
            "x" * 16,
            CODING_IMAGE,
            actual_image,
            "http://127.0.0.1:4000",
            "synthetic",
            129024,
            2048,
            1.0,
            2.0,
        )
        mailbox = CodingMailbox.create(tmp_path / "mailbox")
        coding = DockerCodingContainer(config, mailbox.root)
        frozen = {
            "model_alias": "qwen-plus",
            "root": {
                "agent_id": "orchestrator",
                "revision_id": "r1",
                "instruction": (
                    "Inspect the approved synthetic site using browserctl before coding."
                ),
                "description": "Browser coding",
            },
            "candidates": [],
            "requested_ids": [],
        }
        await coding.start(
            build_config(
                frozen, "http://127.0.0.1:39001", "synthetic-local-token", output_tokens=2048
            ),
            "synthetic-local-token",
            25,
        )
        await coding.set_goal(
            "Inspect http://example.test/ using node /opt/tapper/browserctl.mjs. "
            "Then write flow.spec.ts from actual observations."
        )
        sizes: list[dict[str, object]] = []
        tool_sent = False

        async def handle(item):
            nonlocal tool_sent
            if item.kind == "browser":
                receipt = await client.action(session_id, item.payload, item.identifier)
                assert receipt["status"] == "completed"
                return receipt["observation"]
            body = base64.b64decode(item.payload["bodyBase64"], validate=True)
            parsed = json.loads(body)
            sizes.append(
                {
                    "bytes": len(body),
                    "tools": len(parsed.get("tools", [])),
                    "messages": len(parsed.get("messages", [])),
                    "actual_page_in_context": b"Synthetic Tapper Site" in body,
                }
            )
            if parsed.get("tools") and not tool_sent:
                tool_sent = True
                command_text = 'node /opt/tapper/browserctl.mjs \'{"type":"navigate","url":"http://example.test/"}\''
                arguments = json.dumps(
                    {"command": command_text, "description": "Inspect synthetic site"}
                )
                chunk = {
                    "id": "chatcmpl-task3",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "qwen-plus",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_task3",
                                        "type": "function",
                                        "function": {"name": "bash", "arguments": arguments},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
                payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                return {
                    "status": 200,
                    "contentType": "text/event-stream",
                    "bodyBase64": base64.b64encode(payload).decode(),
                }
            return {
                "status": 429,
                "contentType": "application/json",
                "bodyBase64": base64.b64encode(b'{"error":"synthetic denial"}').decode(),
            }

        running = asyncio.create_task(coding.run_prompt())
        while not running.done():
            if not await mailbox.serve_once(handle):
                await asyncio.sleep(0.02)
        terminal = await running
        assert terminal["terminal"] == "incomplete"
        assert tool_sent
        assert any(item["actual_page_in_context"] for item in sizes), sizes
        assert max(item["bytes"] for item in sizes) <= 131072
        print("observed_model_request_sizes", sizes)
        assert await coding.stop()
        mailbox.close()
        assert (await client.cancel_session(session_id))["status"] == "canceled"

        # The model is synthetic, so this deterministic script tests the frozen
        # Task2 execution/reporting contract without claiming model generation.
        script = """import { test, expect } from '@playwright/test';
test('lookup', async ({ page }, testInfo) => {
  await page.goto('http://example.test/');
  await page.getByLabel('Customer').fill('Ada');
  await page.getByLabel('Region').selectOption('west');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.locator('#result')).toHaveText('Ada / west / FOUND');
  await testInfo.attach('tapper-result', {
    body: Buffer.from(JSON.stringify({ value: await page.locator('#result').innerText() })),
    contentType: 'application/json'
  });
});
"""
        bundle = await client.bundle(task_id, script, {}, ["http://example.test"], "bundle-task3")
        execution = await client.execute(
            task_id,
            bundle["bundle_id"],
            bundle["input_sha256"],
            {"deadline_seconds": 90, "max_output_bytes": 5_000_000},
            "execution-task3",
            deadline_seconds=90,
        )
        assert execution["status"] == "passed", execution
        assert execution["result"]["outputs"] == [{"value": "Ada / west / FOUND"}]
        assert {item["kind"] for item in execution["evidence"]} >= {"screenshot", "trace", "result"}
        replay = await client.replay(
            "task3-replay", execution["execution_id"], "replay-task3", deadline_seconds=90
        )
        assert replay["status"] == "passed", replay
        assert replay["result"]["outputs"] == execution["result"]["outputs"]

        # A local synthetic model exercises CodingRuntime.run itself, including
        # durable reservations and the actual OpenCode -> browserctl -> Task2 path.
        synthetic_state = {"calls": 0, "stage": 0, "saw_page": False}

        class SyntheticModel(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                synthetic_state["calls"] += 1
                has_tools = bool(body.get("tools"))
                saw_page = "Synthetic Tapper Site" in json.dumps(body)
                synthetic_state["saw_page"] |= saw_page
                if has_tools and synthetic_state["stage"] == 0:
                    synthetic_state["stage"] = 1
                    tool = {
                        "index": 0,
                        "id": "call_browse",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps(
                                {
                                    "command": "node /opt/tapper/browserctl.mjs "
                                    '\'{"type":"navigate","url":"http://example.test/"}\'',
                                    "description": "Inspect synthetic site",
                                }
                            ),
                        },
                    }
                elif has_tools and saw_page and synthetic_state["stage"] == 1:
                    synthetic_state["stage"] = 2
                    tool = {
                        "index": 0,
                        "id": "call_script",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps(
                                {
                                    "command": (
                                        "cat > /workspace/flow.spec.ts <<'EOF'\n" + script + "EOF"
                                    ),
                                    "description": "Write observed Playwright test",
                                }
                            ),
                        },
                    }
                else:
                    tool = None
                delta = (
                    {"role": "assistant", "tool_calls": [tool]}
                    if tool is not None
                    else {"role": "assistant", "content": "Done."}
                )
                frame = {
                    "id": "chatcmpl-synthetic",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "qwen-plus",
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if tool is not None else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
                payload = ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        synthetic = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticModel)
        thread = Thread(target=synthetic.serve_forever, daemon=True)
        thread.start()
        engine = create_async_engine(
            workflow_database.replace("mysql+pymysql:", "mysql+asyncmy:", 1)
        )
        try:
            store = MysqlWorkflowStore(async_sessionmaker(engine, expire_on_commit=False))
            inputs: dict[str, object] = {}
            frozen_synthetic = {
                "kind": "browser",
                "goal": "Inspect the synthetic site and return Ada / west / FOUND.",
                "model_alias": "qwen-plus",
                "root": {
                    "agent_id": "orchestrator",
                    "revision_id": "r1",
                    "description": "Browser coding",
                    "instruction": "Inspect the real page and write a Playwright test.",
                },
                "candidates": [],
                "requested_ids": [],
                "browser": {
                    "start_url": "http://example.test/",
                    "allowed_origins": ["http://example.test"],
                    "expected_result": "Ada / west / FOUND",
                    "input_data": inputs,
                    "input_sha256": hashlib.sha256(canonical_json_bytes(inputs)).hexdigest(),
                    "execution_limits": {"deadline_seconds": 90, "max_output_bytes": 5_000_000},
                },
                "budget_limit": {"model_calls": 10, "cost_micros": 1_000_000},
                "deadline_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
            }
            task = await store.create_task(VALIDATION_SCOPE, frozen_synthetic, "task3-no-model")
            lease = await store.claim_task(VALIDATION_SCOPE, task.task_id, "task3-no-model")
            assert lease is not None
            runtime = CodingRuntime(
                CodingConfig(
                    f"http://127.0.0.1:{api_port}",
                    "task3",
                    "x" * 16,
                    CODING_IMAGE,
                    actual_image,
                    f"http://127.0.0.1:{synthetic.server_port}",
                    "synthetic-key",
                    129024,
                    2048,
                    0.4,
                    1.2,
                ),
                browser=client,
                store=store,
            )
            generated = await runtime.run(
                task.task_id,
                frozen_synthetic,
                await store.snapshot(VALIDATION_SCOPE, task.task_id),
                VALIDATION_SCOPE,
                lease,
                None,
                0,  # type: ignore[arg-type]
            )
            durable = await store.snapshot(VALIDATION_SCOPE, task.task_id)
            synthetic_diagnostic = tmp_path / "synthetic-diagnostic.json"
            write_diagnostic(synthetic_diagnostic, task.task_id, durable, generated, None)
            assert synthetic_diagnostic.stat().st_mode & 0o077 == 0
            saved = json.loads(synthetic_diagnostic.read_text())
            assert saved["budget_used"]["model_calls"] == synthetic_state["calls"]
            print(
                "synthetic_runtime_result",
                json.dumps(
                    {
                        "status": generated.status,
                        "usage": generated.usage,
                        "budget_used": durable["budget_used"],
                        "outputs": generated.terminal_evidence.get("result", {}).get("outputs"),
                        "evidence_kinds": [
                            item["kind"] for item in generated.terminal_evidence.get("evidence", [])
                        ],
                        "synthetic_calls": synthetic_state["calls"],
                        "saw_page": synthetic_state["saw_page"],
                    },
                    default=str,
                ),
            )
            assert generated.status == "passed", generated
            assert generated.terminal_evidence["result"]["outputs"] == [
                {"value": "Ada / west / FOUND"}
            ]
            assert synthetic_state["stage"] == 2 and synthetic_state["saw_page"]
            assert durable["budget_used"]["model_calls"] == synthetic_state["calls"]
            assert durable["budget_used"]["cost_micros"] > 0
        finally:
            await engine.dispose()
            synthetic.shutdown()
            synthetic.server_close()
            thread.join()

        if os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") == "1":
            env_file = os.environ["TAP_CODING_REAL_MODEL_ENV_FILE"]
            values = dotenv_values(env_file)
            model_origin = str(values["LITELLM_BASE_URL"])
            model_key = str(values["LITELLM_MASTER_KEY"])
            info_request = Request(
                model_origin.rstrip("/") + "/model/info",
                headers={"Authorization": "Bearer " + model_key},
            )
            with urlopen(info_request, timeout=5) as info_response:
                catalog = json.load(info_response)["data"]
            metadata = next(
                item["model_info"] for item in catalog if item.get("model_name") == "qwen-plus"
            )
            assert metadata["max_input_tokens"] == 129024
            assert metadata["input_cost_per_token"] == 4e-7
            assert metadata["output_cost_per_token"] == 1.2e-6
            engine = create_async_engine(
                workflow_database.replace("mysql+pymysql:", "mysql+asyncmy:", 1)
            )
            store = None
            real_task_id = None
            real_result = None
            try:
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                store = MysqlWorkflowStore(sessions)
                inputs: dict[str, object] = {}
                frozen = {
                    "kind": "browser",
                    "goal": (
                        "On the approved synthetic site, enter Ada as Customer and west as Region, "
                        "submit Find, and return the verified result text in a structured "
                        "tapper-result JSON attachment. Inspect the actual page first."
                    ),
                    "model_alias": "qwen-plus",
                    "root": {
                        "agent_id": "orchestrator",
                        "revision_id": "r1",
                        "description": "Browser coding",
                        "instruction": "Inspect the real page and write a Playwright test.",
                    },
                    "candidates": [],
                    "requested_ids": [],
                    "browser": {
                        "start_url": "http://example.test/",
                        "allowed_origins": ["http://example.test"],
                        "expected_result": "Ada / west / FOUND",
                        "input_data": inputs,
                        "input_sha256": hashlib.sha256(canonical_json_bytes(inputs)).hexdigest(),
                        "execution_limits": {"deadline_seconds": 90, "max_output_bytes": 5_000_000},
                    },
                    "budget_limit": {"model_calls": 6, "cost_micros": 350_000},
                    "deadline_at": (datetime.now(UTC) + timedelta(seconds=240)).isoformat(),
                }
                task = await store.create_task(VALIDATION_SCOPE, frozen, "task3-real-once")
                real_task_id = task.task_id
                lease = await store.claim_task(VALIDATION_SCOPE, task.task_id, "task3-real-once")
                assert lease is not None
                runtime = CodingRuntime(
                    CodingConfig(
                        f"http://127.0.0.1:{api_port}",
                        "task3",
                        "x" * 16,
                        CODING_IMAGE,
                        actual_image,
                        model_origin,
                        model_key,
                        129024,
                        2048,
                        0.4,
                        1.2,
                    ),
                    browser=client,
                    store=store,
                )
                result = await runtime.run(
                    task.task_id,
                    frozen,
                    await store.snapshot(VALIDATION_SCOPE, task.task_id),
                    VALIDATION_SCOPE,
                    lease,
                    None,  # type: ignore[arg-type]  # runtime-only smoke; dispatch owns the node
                    0,
                )
                real_result = result
                durable = await store.snapshot(VALIDATION_SCOPE, task.task_id)
                print(
                    "real_model_result",
                    json.dumps(
                        {
                            "status": result.status,
                            "usage": result.usage,
                            "budget_used": durable["budget_used"],
                            "execution_ref": result.execution_ref,
                            "bundle_ref": result.bundle_ref,
                            "outputs": result.terminal_evidence.get("result", {}).get("outputs"),
                            "failure": result.terminal_evidence.get("failure"),
                            "stop_facts": {
                                key: result.terminal_evidence.get(key)
                                for key in (
                                    "coding_stopped",
                                    "inspector_status",
                                    "primary_error_type",
                                )
                            },
                        },
                        default=str,
                    ),
                )
                assert result.status == "passed", result
                outputs = result.terminal_evidence["result"]["outputs"]
                assert outputs and "Ada / west / FOUND" in json.dumps(outputs)
                assert durable["budget_used"]["model_calls"] <= 6
                assert durable["budget_used"]["cost_micros"] <= 350_000
            finally:
                if store is not None and real_task_id is not None:
                    durable = await store.snapshot(VALIDATION_SCOPE, real_task_id)
                    error = sys.exc_info()[1]
                    write_diagnostic(
                        Path(os.environ["TAP_CODING_DIAGNOSTIC_PATH"]),
                        real_task_id,
                        durable,
                        real_result,
                        type(error).__name__ if error else None,
                    )
                await engine.dispose()
    finally:
        if coding is not None:
            assert await coding.stop()
        if api_server is not None:
            api_server.should_exit = True
        if api_task is not None:
            await asyncio.wait_for(api_task, 10)
        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if site_started:
            subprocess.run(["docker", "rm", "-f", site], capture_output=True)
        subprocess.run(["docker", "rm", "-f", mysql], capture_output=True)
