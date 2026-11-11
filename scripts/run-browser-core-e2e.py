#!/usr/bin/env python3
"""Opt-in isolated Tapper Chat → coding → browser → receipt/replay acceptance.

Synthetic mode spends no provider calls. Real mode requires explicit bounded inputs.
All database, site, model, and app resources have a unique supervisor owner.
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_RUNNER = ROOT / "scripts/run-workflow-e2e.py"
spec = importlib.util.spec_from_file_location("workflow_e2e_helpers", WORKFLOW_RUNNER)
assert spec and spec.loader
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
SITE_SCRIPT = ROOT / "scripts/browser_core_site.mjs"
BROWSER_IMAGE = "tapper-browser-runtime:1.62.1-arm64"
CODING_IMAGE = "tapper-coding-runtime:1.19.5-arm64"


def synthetic_cases() -> list[dict[str, object]]:
    return [
        {
            "name": "lookup",
            "goal": "Core lookup: On http://example.test/ find the order for the supplied customer and region. Return the actual order code read from the result page as JSON.",
            "startUrl": "http://example.test/",
            "inputNotes": "customer=Ada; region=west",
            "expected": "Return the page's order code and customer/region.",
            "status": "succeeded",
            "outputPattern": r"ORDER-[A-F0-9]{8}",
            "replay": True,
        },
        {
            "name": "form",
            "goal": "Core form: On http://example.test/review check the supplied account and amount in the request form. Return the actual review result as JSON.",
            "startUrl": "http://example.test/review",
            "inputNotes": "account=AC-42; amount=120",
            "expected": "Review result is AC-42 / 120 / MATCH.",
            "status": "succeeded",
            "outputPattern": "AC-42 / 120 / MATCH",
        },
        {
            "name": "multipage",
            "goal": "Core multipage: Starting at http://example.test/journey, follow the details and finish links, then report the actual final page text as JSON.",
            "startUrl": "http://example.test/journey",
            "inputNotes": "Review all three pages",
            "expected": "Report the final page status.",
            "status": "succeeded",
            "outputPattern": "Three pages reviewed",
        },
        {
            "name": "wrong-expected",
            "goal": "Core wrong-expected: Check the order result against the stated expected code.",
            "startUrl": "http://example.test/",
            "inputNotes": "customer=Ada; region=west",
            "expected": "Order code must be ORDER-WRONG.",
            "status": "failed",
        },
        {
            "name": "missing-input",
            "goal": "Core missing-input: A secret account PIN is required to continue this check. Ask for it if absent.",
            "startUrl": "http://example.test/review",
            "expected": "Review the account using the PIN.",
            "status": "awaiting_input",
        },
    ]


def synthetic_script(name: str) -> str:
    common = (
        "import { test, expect } from '@playwright/test';\nimport fs from 'node:fs';\n"
    )
    if name in {"lookup", "wrong-expected"}:
        assertion = (
            "await expect(page.locator('#result')).toContainText('ORDER-WRONG');"
            if name == "wrong-expected"
            else "expect(value).toMatch(/^Ada \\/ west \\/ ORDER-[A-F0-9]{8}$/u);"
        )
        return (
            common
            + """test('query order', async ({ page }, testInfo) => {
  const input = JSON.parse(fs.readFileSync(process.env.TAP_BROWSER_INPUTS_PATH || '/opt/browser/frozen/inputs.json', 'utf8'));
  const notes = Object.fromEntries([...input.goal.matchAll(/(customer|region|account|amount)=([A-Za-z0-9-]+)/g)].map(match => [match[1], match[2]]));
  await page.goto('http://example.test/');
  await page.getByLabel('Customer').fill(notes.customer);
  await page.getByLabel('Region').selectOption(notes.region);
  await page.getByRole('button', { name: 'Find order' }).click();
  const value = await page.locator('#result').innerText();
  """
            + assertion
            + """
  await testInfo.attach('tapper-result', {
    body: Buffer.from(JSON.stringify({ orderResult: value })), contentType: 'application/json'
  });
});
"""
        )
    if name == "form":
        return (
            common
            + """test('check request form', async ({ page }, testInfo) => {
  const input = JSON.parse(fs.readFileSync(process.env.TAP_BROWSER_INPUTS_PATH || '/opt/browser/frozen/inputs.json', 'utf8'));
  const notes = Object.fromEntries([...input.goal.matchAll(/(customer|region|account|amount)=([A-Za-z0-9-]+)/g)].map(match => [match[1], match[2]]));
  await page.goto('http://example.test/review');
  await page.getByLabel('Account').fill(notes.account);
  await page.getByLabel('Amount').fill(notes.amount);
  await page.getByRole('button', { name: 'Check request' }).click();
  const value = await page.locator('#result').innerText();
  expect(value).toBe('AC-42 / 120 / MATCH');
  await testInfo.attach('tapper-result', {
    body: Buffer.from(JSON.stringify({ reviewResult: value })), contentType: 'application/json'
  });
});
"""
        )
    if name == "multipage":
        return (
            common
            + """test('review three pages', async ({ page }, testInfo) => {
  await page.goto('http://example.test/journey');
  await expect(page.getByRole('heading', { name: 'Travel checklist' })).toBeVisible();
  await page.getByRole('link', { name: 'See details' }).click();
  await expect(page.getByRole('heading', { name: 'Journey details' })).toBeVisible();
  await page.getByRole('link', { name: 'Finish review' }).click();
  const value = await page.locator('#journey-result').innerText();
  expect(value).toBe('Three pages reviewed');
  await testInfo.attach('tapper-result', {
    body: Buffer.from(JSON.stringify({ finalStatus: value })), contentType: 'application/json'
  });
});
"""
        )
    raise ValueError("unknown synthetic scenario")


class SyntheticModel(BaseHTTPRequestHandler):
    stages: dict[str, int] = {}
    calls: dict[str, int] = {}
    observed: set[str] = set()

    def do_GET(self) -> None:
        if self.path != "/stats":
            self.send_error(404)
            return
        payload = json.dumps(
            {"calls": self.calls, "observed": sorted(self.observed)}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 8 * 1024 * 1024:
            self.send_error(413)
            return
        request = json.loads(self.rfile.read(size))
        body = json.dumps(request)
        has_tools = bool(request.get("tools"))
        names = [
            case["name"]
            for case in synthetic_cases()
            if f"Core {case['name']}:" in body
        ]
        if "Core cancel:" in body:
            names.append("cancel")
        if has_tools and len(names) != 1:
            self.send_error(422)
            return
        name = str(names[0]) if has_tools else "probe"
        self.calls[name] = self.calls.get(name, 0) + 1
        stage = self.stages.get(name, 0) if has_tools else -1
        tool = None
        if has_tools:
            definitions = {item["function"]["name"] for item in request["tools"]}
            if "tap_browser" not in definitions:
                self.send_error(422, "native browser tool missing")
                return
            observations = []
            for message in request["messages"]:
                if message.get("role") != "tool":
                    continue
                try:
                    observed = json.loads(message.get("content", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(observed, dict) and observed.get("sequence"):
                    observations.append(observed)
            if observations:
                self.observed.add(name)
            start_url = (
                "http://example.test/"
                if name == "cancel"
                else next(
                    case["startUrl"]
                    for case in synthetic_cases()
                    if case["name"] == name
                )
            )
            actions = [
                {
                    "type": "plan",
                    "steps": [
                        {
                            "id": "inspect",
                            "keyword": "Given",
                            "title": "Inspect the requested page",
                        },
                        {
                            "id": "operate",
                            "keyword": "When",
                            "title": "Apply supplied inputs",
                        },
                        {
                            "id": "verify",
                            "keyword": "Then",
                            "title": "Verify requested result",
                        },
                    ],
                },
                {"type": "navigate", "step": "inspect", "url": start_url},
            ]
            if name in {"lookup", "wrong-expected", "form"}:
                first, second = (
                    ("account", "amount") if name == "form" else ("customer", "region")
                )
                values = {
                    key: re.search(rf"{key}=([A-Za-z0-9-]+)", body)[1]
                    for key in (first, second)
                }
                if name == "lookup":
                    # Exercise a real recoverable tool error in the OpenCode session.
                    actions.append(
                        {
                            "type": "fill",
                            "step": "operate",
                            "selector": "[",
                            "value": values[first],
                        }
                    )
                actions.extend(
                    [
                        {
                            "type": "fill",
                            "step": "operate",
                            "selector": f"[name={first}]",
                            "value": values[first],
                        },
                        {
                            "type": "fill" if name == "form" else "select",
                            "step": "operate",
                            "selector": f"[name={second}]",
                            "value": values[second],
                        },
                        {
                            "type": "click",
                            "step": "operate",
                            "selector": "button[type=submit]",
                        },
                        {
                            "type": "read",
                            "step": "verify",
                            "selector": "#result",
                            "name": "result",
                            "nonempty": True,
                            **(
                                {"contains": "ORDER-WRONG", "timeout_ms": 100}
                                if name == "wrong-expected"
                                else {}
                            ),
                        },
                    ]
                )
            elif name == "multipage":
                actions.extend(
                    [
                        {
                            "type": "click",
                            "step": "operate",
                            "selector": "a[href='/journey/details']",
                        },
                        {
                            "type": "click",
                            "step": "operate",
                            "selector": "a[href='/journey/finish']",
                        },
                        {
                            "type": "read",
                            "step": "verify",
                            "selector": "#journey-result",
                            "name": "result",
                            "equals": "Three pages reviewed",
                        },
                    ]
                )
            command = None
            if stage < len(actions):
                if (
                    stage == 3
                    and name == "lookup"
                    and not any(o.get("error") for o in observations)
                ):
                    self.send_error(
                        422, "tool failure was not returned to the same session"
                    )
                    return
                tool = {
                    "index": 0,
                    "id": f"call_{name}_{stage}",
                    "type": "function",
                    "function": {
                        "name": "tap_browser",
                        "arguments": json.dumps({"action": actions[stage]}),
                    },
                }
            elif stage == len(actions):
                if name == "cancel":
                    command = "sleep 90"
                elif name == "missing-input":
                    command = (
                        "cat > tapper-input-request.json <<'EOF'\n"
                        + json.dumps(
                            {
                                "reason": "missing_input",
                                "question": "Provide the account PIN in a new run.",
                            }
                        )
                        + "\nEOF"
                    )
                else:
                    command = (
                        "cat > flow.spec.ts <<'EOF'\n" + synthetic_script(name) + "EOF"
                    )
            if command is not None:
                tool = {
                    "index": 0,
                    "id": f"call_{name}_{stage}",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps(
                            {
                                "command": command,
                                "description": "Save the observed task artifact",
                            }
                        ),
                    },
                }
            self.stages[name] = stage + 1
        chunk = {
            "id": f"chatcmpl-{name}-{stage}",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "qwen-plus",
            "choices": [
                {
                    "index": 0,
                    "delta": (
                        {"role": "assistant", "tool_calls": [tool]}
                        if tool
                        else {"role": "assistant", "content": "Done."}
                    ),
                    "finish_reason": "tool_calls" if tool else "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass


def load_reviewed_contract(path: Path) -> dict[str, object]:
    """Load explicit test inputs, never infer reviewed facts from a model response."""
    from tap.modules.workflows.domain.acceptance_contract import (
        BrowserAcceptanceContract,
    )

    with path.open("rb") as file:
        data = file.read(32769)
    if len(data) > 32768:
        raise ValueError("reviewed contract file exceeds 32768 bytes")
    contract = BrowserAcceptanceContract.model_validate_json(data)
    return contract.model_dump(mode="json", by_alias=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("synthetic", "real"), default="synthetic")
    parser.add_argument(
        "--execution-mode", choices=("docker", "local"), default="docker"
    )
    parser.add_argument("--opencode-binary", type=Path)
    parser.add_argument("--lca-bridge", action="store_true")

    parser.add_argument(
        "--case",
        choices=(
            "lookup",
            "form",
            "multipage",
            "wrong-expected",
            "missing-input",
            "cancel",
        ),
    )
    parser.add_argument("--site", choices=("owned", "public"), default="owned")
    parser.add_argument("--goal")
    parser.add_argument("--start-url")
    parser.add_argument("--input-notes")
    parser.add_argument("--expected")
    parser.add_argument("--output-pattern")
    parser.add_argument("--max-model-calls", type=int)
    parser.add_argument("--max-cost-usd", type=Decimal)
    parser.add_argument("--deadline-seconds", type=int)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--acceptance-contract-file", type=Path)
    args = parser.parse_args()
    if args.lca_bridge and (args.mode != "synthetic" or args.execution_mode != "local"):
        parser.error("LCA bridge acceptance requires zero-cost synthetic local mode")
    if args.execution_mode == "local" and (
        args.opencode_binary is None
        or not args.opencode_binary.is_absolute()
        or not args.opencode_binary.is_file()
    ):
        parser.error("local execution requires an existing absolute --opencode-binary")
    if args.mode == "synthetic" and args.site != "owned":
        parser.error("synthetic mode requires the owned site")
    if args.mode == "real":
        required = (
            args.goal,
            args.start_url,
            args.input_notes,
            args.expected,
            args.max_model_calls,
            args.max_cost_usd,
            args.deadline_seconds,
        )
        if not all(required):
            parser.error(
                "real mode requires goal, URL, input, expected result and exact call/cost/deadline bounds"
            )
        parsed = urlsplit(args.start_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            parser.error("start URL must be an absolute credential-free HTTP(S) URL")
        if args.site == "owned" and parsed.hostname != "example.test":
            parser.error("owned-site mode requires example.test")
        if (
            not 1 <= args.max_model_calls <= 60
            or not args.max_cost_usd.is_finite()
            or not Decimal("0") < args.max_cost_usd <= Decimal("1.0")
            or not 30 <= args.deadline_seconds <= 900
        ):
            parser.error("real experiment bounds exceed supported limits")
        if args.env_file is None or not args.env_file.is_file():
            parser.error(
                "real mode requires an explicit existing host model configuration file"
            )
    return args


def selected_browser_env(env: dict[str, str]) -> dict[str, str]:
    names = (
        "PATH",
        "HOME",
        "TAP_BROWSER_DATABASE_URL",
        "TAP_BROWSER_OBJECT_ROOT",
        "TAP_BROWSER_PROJECT_ID",
        "TAP_BROWSER_ACCESS_TOKEN",
        "TAP_LCA_GATEWAY_TOKEN",
        "TAP_BROWSER_GATEWAY_TOKEN",
        "TAP_BROWSER_WORKER_TOKEN",
        "TAP_BROWSER_CONTROL_ENABLED",
        "TAP_BROWSER_API_ORIGIN",
        "TAP_BROWSER_TOKEN_EXPIRES_AT",
        "TAP_BROWSER_IMAGE",
        "TAP_BROWSER_EXECUTION_MODE",
        "TAP_BROWSER_LOCAL_TEST_TARGET",
        "TAP_BROWSER_LOCAL_UPSTREAM_PROXY",
        "PLAYWRIGHT_BROWSERS_PATH",
        "TAP_BROWSER_WORKER_POLL_SECONDS",
        "DOCKER_CONTEXT",
    )
    return {name: env[name] for name in names if name in env}


def run_playwright(env: dict[str, str], timeout: int) -> None:
    helpers.run_browser(
        [
            "corepack",
            "pnpm",
            "--filter",
            "@tap/ai-frontend",
            "exec",
            "playwright",
            "test",
            "tests/e2e/browser-core-live.spec.ts",
            "--config=playwright.config.ts",
            "--reporter=line",
            "--workers=1",
            "--output="
            + env["TAPPER_BROWSER_CORE_EVIDENCE_DIR"]
            + "/playwright-output",
        ],
        env,
        timeout=timeout,
    )


def export_evidence(env: dict[str, str], directory: Path) -> None:
    """Export private usage and original scripts before any owned DB cleanup."""
    import asyncio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap_platform.browser_automation.http import browser_service_from_environment
    from tap_platform.browser_automation.store import sessions as browser_sessions
    from tap_platform.browser_automation.store import actions as browser_actions

    async def save() -> None:
        engine = create_async_engine(env["TAP_DATABASE_URL"])
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            store = MysqlWorkflowStore(sessions)
            async with sessions() as session:
                ids = (
                    (await session.execute(select(workflow_task.c.task_id)))
                    .scalars()
                    .all()
                )
            manifest = []
            browser_service, _principal, _token = browser_service_from_environment()
            assert browser_service is not None
            with browser_service.store.engine.connect() as connection:
                session_rows = (
                    connection.execute(select(browser_sessions)).mappings().all()
                )
                action_rows = (
                    connection.execute(select(browser_actions)).mappings().all()
                )
            (directory / "browser-session-ledger.json").write_text(
                json.dumps(
                    {
                        "sessions": [
                            {
                                "id": row["id"],
                                "task_id": row["task_id"],
                                "status": row["status"],
                                "created_at": row["created_at"],
                                "cancel_requested": row["cancel_requested"],
                                "request": row["request"],
                                "observation": row["observation"],
                                "stop_receipt": row["stop_receipt"],
                            }
                            for row in session_rows
                        ],
                        "actions": [
                            {
                                "id": row["id"],
                                "session_id": row["session_id"],
                                "sequence": row["sequence"],
                                "status": row["status"],
                                "request": row["request"],
                                "observation": row["observation"],
                            }
                            for row in action_rows
                        ],
                    },
                    indent=2,
                    default=str,
                )
            )
            for task_id in ids:
                snapshot = await store.snapshot(VALIDATION_SCOPE, task_id)
                coding_events = [
                    event.payload
                    for event in snapshot["events"]
                    if event.kind == "browser.coding_result"
                ]
                result = coding_events[-1] if coding_events else {}
                bundle_id = result.get("bundle_ref")
                script_hash = None
                if isinstance(bundle_id, str):
                    bundle, frozen_inputs = browser_service.frozen_bundle(
                        "tapper-demo", bundle_id
                    )
                    script_hash = bundle.sha256
                    (directory / f"{task_id}-flow.spec.ts").write_bytes(
                        bundle.script_bytes
                    )
                    (directory / f"{task_id}-inputs.json").write_text(
                        json.dumps(frozen_inputs, indent=2, default=str)
                    )
                record = {
                    "task_id": task_id,
                    "status": snapshot["status"],
                    "budget_used": snapshot["budget_used"],
                    "bundle_id": bundle_id,
                    "script_sha256": script_hash,
                    "terminal_result": result,
                    "frozen_request": snapshot["request_json"],
                    "events": [
                        {"kind": event.kind, "payload": event.payload}
                        for event in snapshot["events"]
                        if event.kind
                        in {
                            "action.reserved",
                            "action.settled",
                            "browser.coding_result",
                            "browser.stop_confirmed",
                            "task.dispatch_blocked",
                        }
                    ],
                }
                (directory / f"{task_id}-audit.json").write_text(
                    json.dumps(record, indent=2, default=str)
                )
                manifest.append(
                    {
                        "task_id": task_id,
                        "status": snapshot["status"],
                        "budget_used": snapshot["budget_used"],
                        "script_sha256": script_hash,
                    }
                )
            (directory / "usage-manifest.json").write_text(
                json.dumps(manifest, indent=2, default=str)
            )
        finally:
            await engine.dispose()

    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(selected_browser_env(env))
        asyncio.run(save())
    finally:
        os.environ.clear()
        os.environ.update(previous)


def failure_accounting_confirmed(directory: Path) -> bool:
    """Do not discard a failed run's DB while a task or model action is open."""
    try:
        manifest = json.loads((directory / "usage-manifest.json").read_text())
        if not isinstance(manifest, list):
            return False
        for row in manifest:
            if row["status"] not in {
                "succeeded",
                "failed",
                "canceled",
                "skipped",
                "awaiting_input",
            }:
                return False
            audit = json.loads((directory / f"{row['task_id']}-audit.json").read_text())
            reserved = {
                event["payload"]["action_id"]
                for event in audit["events"]
                if event["kind"] == "action.reserved"
            }
            settled = {
                event["payload"]["action_id"]
                for event in audit["events"]
                if event["kind"] == "action.settled"
            }
            if not reserved <= settled:
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError):
        return False


def cancel_active_on_failure(env: dict[str, str], directory: Path) -> None:
    """Ask the shipped task API to stop any live browser work before teardown."""
    import asyncio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
    from tap.modules.workflows.adapters.schema import workflow_task

    async def running() -> list[dict[str, object]]:
        engine = create_async_engine(env["TAP_DATABASE_URL"])
        try:
            store = MysqlWorkflowStore(
                async_sessionmaker(engine, expire_on_commit=False)
            )
            async with store._sessions() as session:
                ids = (
                    (await session.execute(select(workflow_task.c.task_id)))
                    .scalars()
                    .all()
                )
            return [
                {"task_id": task_id, "plan_revision": snapshot["plan_revision"]}
                for task_id in ids
                if (snapshot := await store.snapshot(VALIDATION_SCOPE, task_id))[
                    "status"
                ]
                == "running"
            ]
        finally:
            await engine.dispose()

    pending = asyncio.run(running())
    receipts: list[dict[str, object]] = []
    for task in pending:
        task_id = str(task["task_id"])
        body = json.dumps(
            {
                "kind": "cancel",
                "expectedPlanRevision": task["plan_revision"],
                "targetNodeId": None,
                "text": None,
            }
        ).encode()
        request = Request(
            f"{env['TAPPER_API_BASE_URL']}/api/v1/projects/tapper-demo/workflows/{task_id}/commands",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": uuid.uuid4().hex,
                "Origin": env["TAPPER_WEB_BASE_URL"],
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                receipt = json.loads(response.read(4096))
                receipts.append(
                    {
                        "task_id": task_id,
                        "http_status": response.status,
                        "status": receipt.get("status"),
                    }
                )
        except OSError as error:
            receipts.append({"task_id": task_id, "error_type": type(error).__name__})
    deadline = time.monotonic() + 45
    while pending and time.monotonic() < deadline:
        pending = asyncio.run(running())
        if pending:
            time.sleep(0.5)
    (directory / "failure-cancel.json").write_text(
        json.dumps({"receipts": receipts, "still_running": pending}, indent=2)
    )


def verify_cancel_evidence(directory: Path) -> None:
    """Require durable browser stop proof after the UI requested cancellation."""
    canceled = json.loads((directory / "cancel-snapshot.json").read_text())
    task_id = canceled["taskId"]
    audit = json.loads((directory / f"{task_id}-audit.json").read_text())
    ledger = json.loads((directory / "browser-session-ledger.json").read_text())
    sessions = [row for row in ledger["sessions"] if row["task_id"] == task_id]
    if (
        canceled["status"] != "canceled"
        or audit["status"] != "canceled"
        or not any(
            event["kind"] == "browser.stop_confirmed" for event in audit["events"]
        )
        or len(sessions) != 1
        or sessions[0]["status"] != "canceled"
        or sessions[0]["stop_receipt"].get("stopped") is not True
        or not any(
            row["session_id"] == sessions[0]["id"] and row["status"] == "completed"
            for row in ledger["actions"]
        )
    ):
        raise AssertionError(
            "canceled browser task lacks action and durable stop proof"
        )


def finish_owned_compose(
    compose: list[str], env: dict[str, str], *, preserve_accounting: bool
) -> None:
    # compose is the unique project command built and checked before startup.
    # Stop its middleware on retained accounting, preserving all state/volumes.
    command = (
        ["stop"] if preserve_accounting else ["down", "--volumes", "--remove-orphans"]
    )
    helpers.run(compose + command, env, timeout=180)


def model_execution_settings(mode: str, values: dict[str, str]) -> dict[str, str]:
    """Keep real planning/execution selection and conservative billing explicit."""
    alias = values.get("TAP_BROWSER_CODING_MODEL_ALIAS", "qwen-plus")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", alias):
        raise ValueError("invalid model alias")
    defaults = {
        "TAP_BROWSER_CODING_MAX_INPUT_TOKENS": "129024",
        "TAP_BROWSER_CODING_INPUT_PRICE_PER_MILLION": "0.4",
        "TAP_BROWSER_CODING_OUTPUT_PRICE_PER_MILLION": "1.2",
    }
    if (
        mode == "real"
        and alias != "qwen-plus"
        and any(not values.get(key) for key in defaults)
    ):
        raise ValueError(
            "nondefault real model requires explicit token limit and prices"
        )
    settings = {key: values.get(key, default) for key, default in defaults.items()}
    if (
        not settings["TAP_BROWSER_CODING_MAX_INPUT_TOKENS"].isdigit()
        or not 1 <= int(settings["TAP_BROWSER_CODING_MAX_INPUT_TOKENS"]) <= 2_000_000
    ):
        raise ValueError("invalid model input token limit")
    for key in defaults:
        if "PRICE" in key:
            try:
                price = Decimal(settings[key])
            except Exception as error:
                raise ValueError("invalid model price") from error
            if not price.is_finite() or price <= 0:
                raise ValueError("model price must be finite and positive")
    return {
        **settings,
        "TAP_BROWSER_CODING_MODEL_ALIAS": alias,
        "TAPPER_DEFAULT_CHAT_MODEL": alias,
    }


def main() -> int:
    args = parse_args()
    model_settings = model_execution_settings(args.mode, dict(os.environ))
    reviewed_contract = (
        load_reviewed_contract(args.acceptance_contract_file)
        if args.acceptance_contract_file is not None
        else None
    )
    if reviewed_contract is not None and args.mode != "real":
        raise ValueError(
            "contract file requires real mode; synthetic fixtures own their reviewed inputs"
        )
    nonce = uuid.uuid4().hex[:12]
    project = f"tap-browser-core-{nonce}"
    state = Path(tempfile.mkdtemp(prefix=f"{project}-state-"))
    artifacts = Path(tempfile.mkdtemp(prefix=f"{project}-evidence-"))
    state.chmod(0o700)
    artifacts.chmod(0o700)
    env = helpers.isolated_env(project, state, helpers.free_ports(8))
    env["DOCKER_CONTEXT"] = helpers.local_docker(env)
    context = env["DOCKER_CONTEXT"]
    browser_port = helpers.free_ports(1)[0]
    env.update(
        {
            "TAP_BROWSER_DATABASE_URL": env["TAP_ALEMBIC_DATABASE_URL"].replace(
                "/tap?", "/tap_browser?"
            ),
            "TAP_BROWSER_OBJECT_ROOT": str(state / "browser-objects"),
            "TAP_BROWSER_PROJECT_ID": "tapper-demo",
            "TAP_BROWSER_ACCESS_TOKEN": uuid.uuid4().hex + uuid.uuid4().hex,
            "TAP_BROWSER_TOKEN_EXPIRES_AT": (
                datetime.now(UTC) + timedelta(hours=1)
            ).isoformat(),
            "TAP_BROWSER_IMAGE": BROWSER_IMAGE,
            "TAP_BROWSER_EXECUTION_MODE": args.execution_mode,
            "TAP_BROWSER_LOCAL_OPENCODE_BINARY": str(args.opencode_binary or ""),
            "TAP_BROWSER_WORKER_POLL_SECONDS": "0.05",
            "TAP_BROWSER_API_ORIGIN": f"http://127.0.0.1:{browser_port}",
            "TAP_BROWSER_CODING_ENABLED": "true",
            "TAP_BROWSER_CODING_IMAGE": CODING_IMAGE,
            "TAP_BROWSER_SERVICE_MODE": "external",
            **model_settings,
            "TAPPER_MODEL_TIMEOUT_SECONDS": "60" if args.mode == "real" else "15",
            "TAP_BROWSER_CODING_MAX_OUTPUT_TOKENS": "4096"
            if args.mode == "real"
            else "2048",
            "TAP_WORKFLOW_MAX_MODEL_CALLS": str(
                args.max_model_calls if args.mode == "real" else 12
            ),
            "TAP_WORKFLOW_MAX_COST_MICROS": str(
                int(args.max_cost_usd * 1_000_000) if args.mode == "real" else 500_000
            ),
            "TAP_WORKFLOW_MAX_SECONDS": str(
                args.deadline_seconds if args.mode == "real" else 240
            ),
            "TAPPER_BROWSER_CORE_E2E": "1",
            "TAPPER_BROWSER_CORE_EVIDENCE_DIR": str(artifacts),
            "TAPPER_BROWSER_CORE_TASK_TIMEOUT_MS": str(
                (args.deadline_seconds if args.mode == "real" else 240) * 1000 + 30_000
            ),
            "TAPPER_BROWSER_CORE_RUN_CANCEL": "1"
            if args.mode == "synthetic" and args.case in (None, "cancel")
            else "0",
        }
    )
    if args.lca_bridge:
        env.update(
            {
                "TAP_LCA_ENABLED": "true",
                "TAP_BROWSER_CONTROL_ENABLED": "true",
                "TAP_BROWSER_GATEWAY_TOKEN": uuid.uuid4().hex + uuid.uuid4().hex,
                "TAP_BROWSER_WORKER_TOKEN": uuid.uuid4().hex + uuid.uuid4().hex,
                "TAP_LCA_GATEWAY_TOKEN": uuid.uuid4().hex + uuid.uuid4().hex,
                "TAP_LCA_BACKEND_URL": env["TAP_BROWSER_API_ORIGIN"],
                "TAP_WEB_PORT": str(helpers.free_ports(1)[0]),
                "TAPPER_BROWSER_CORE_LCA_BRIDGE": "1",
            }
        )
    upstream_proxy = os.environ.get("TAP_BROWSER_LOCAL_UPSTREAM_PROXY")
    if upstream_proxy and args.execution_mode == "local":
        env["TAP_BROWSER_LOCAL_UPSTREAM_PROXY"] = upstream_proxy
    if args.mode == "real":
        # Exercise the production model router as well as the coding runtime.
        # Readiness checks catalog metadata; it does not issue a model completion.
        env["TAP_DEMO_MODE"] = ""
        env["TAPPER_MODEL_BACKEND"] = "litellm"
        env["TAP_AGENT_TEAM_RUNTIME_METRICS"] = "langfuse"
        values = dotenv_values(args.env_file)
        origin, key = values.get("LITELLM_BASE_URL"), values.get("LITELLM_MASTER_KEY")
        if not isinstance(origin, str) or not isinstance(key, str) or len(key) < 16:
            raise RuntimeError("host model configuration is incomplete")
        env["LITELLM_BASE_URL"] = origin
        env["LITELLM_MASTER_KEY"] = key
        scenarios = [
            {
                "name": "live",
                "goal": args.goal,
                "startUrl": args.start_url,
                "inputNotes": args.input_notes,
                **(
                    {"acceptanceContract": reviewed_contract}
                    if reviewed_contract is not None
                    else {}
                ),
                "expected": args.expected,
                "outputPattern": args.output_pattern,
                "status": "succeeded",
                "replay": True,
            }
        ]
    else:
        scenarios = [
            case
            for case in synthetic_cases()
            if args.case is None or case["name"] == args.case
        ]
    case_file = state / "cases.json"
    case_file.write_text(json.dumps(scenarios), encoding="utf-8")
    env["TAPPER_BROWSER_CORE_CASES"] = str(case_file)
    (artifacts / "experiment.json").write_text(
        json.dumps(
            {
                "mode": args.mode,
                "execution_mode": args.execution_mode,
                "model_settings": model_settings,
                "site": args.site,
                "project": project,
                "max_model_calls": args.max_model_calls if args.mode == "real" else 12,
                "max_cost_usd": str(
                    args.max_cost_usd if args.mode == "real" else Decimal("0.50")
                ),
                "deadline_seconds": args.deadline_seconds
                if args.mode == "real"
                else 240,
                "started_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )

    site_name = f"{project}-site"
    site_id = None
    local_site = None
    app = browser_api = browser_worker = None
    model = model_thread = None
    compose = None
    owned = False
    success = False
    phase = "preflight"
    try:
        if args.execution_mode == "local":
            version = subprocess.run(
                [str(args.opencode_binary), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            if version.stdout.strip() != "1.19.5":
                raise RuntimeError("local OpenCode version mismatch")
        else:
            for image in (BROWSER_IMAGE, CODING_IMAGE):
                helpers.run(
                    ["docker", "image", "inspect", image, "--format", "{{.Id}}"], env
                )
            env["TAP_BROWSER_CODING_IMAGE_ID"] = subprocess.check_output(
                ["docker", "image", "inspect", CODING_IMAGE, "--format", "{{.Id}}"],
                text=True,
            ).strip()
            image_spec = importlib.util.spec_from_file_location(
                "browser_image_builder", ROOT / "scripts/build_coding_image.py"
            )
            assert image_spec and image_spec.loader
            image_builder = importlib.util.module_from_spec(image_spec)
            image_spec.loader.exec_module(image_builder)
            image_builder.verify_browser_image_sources(ROOT, BROWSER_IMAGE)
        from tap.entrypoints.tapper_runtime import TapperSettings

        TapperSettings.from_mapping(env)
        if args.execution_mode == "docker":
            helpers.run(["bash", "scripts/build-tapper-parser.sh", "verify"], env)
        phase = "compose-config"
        override = helpers.write_owned_data_override(state)
        owned_config = json.loads(override.read_text())
        subnet = str(ipaddress.IPv4Address("10.224.0.0") + (int(project[-3:], 16) << 8))
        owned_config["networks"] = {
            "default": {"ipam": {"config": [{"subnet": subnet + "/24"}]}}
        }
        override.write_text(json.dumps(owned_config), encoding="utf-8")
        compose = [
            "docker",
            "--context",
            context,
            "compose",
            "-f",
            str(helpers.COMPOSE),
            "-f",
            str(override),
            "-p",
            project,
            "--profile",
            "milvus",
        ]
        helpers.verify_owned_data_mounts(
            helpers.run(compose + ["config", "--format", "json"], env), state
        )
        if helpers.run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            env,
        ):
            raise RuntimeError("owned Compose project already exists")
        env["TAPPER_OBJECT_STORE_IMAGE"] = helpers.run(
            ["bash", "scripts/build-tapper-object-store.sh", "verify"], env
        )
        owned = True
        phase = "compose-up"
        helpers.run(
            compose + ["up", "-d", "--wait", "--wait-timeout", "180"], env, timeout=300
        )
        phase = "database-migrations"
        mysql_id = helpers.run(
            [
                "docker",
                "ps",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=mysql",
                "--format",
                "{{.ID}}",
            ],
            env,
        )
        helpers.write_owned_database_marker(env, state, mysql_id, context)
        helpers.run(
            [
                "docker",
                "exec",
                mysql_id,
                "mysql",
                "-uroot",
                "-pworkflow-e2e-root",
                "-e",
                "CREATE DATABASE tap_browser; GRANT ALL PRIVILEGES ON tap_browser.* TO 'tap'@'%';",
            ],
            env,
        )
        helpers.run(
            [
                "uv",
                "run",
                "--project",
                "apps/tap-ai-backend",
                "alembic",
                "-c",
                "apps/tap-ai-backend/alembic.ini",
                "upgrade",
                "head",
            ],
            env,
        )
        browser_migration = {**env, "TAP_DATABASE_URL": env["TAP_BROWSER_DATABASE_URL"]}
        helpers.run(
            [
                "uv",
                "run",
                "--project",
                "apps/backend",
                "alembic",
                "-c",
                "apps/backend/alembic.ini",
                "upgrade",
                "head",
            ],
            browser_migration,
        )
        phase = "milvus-bootstrap"
        helpers.run(
            [
                "uv",
                "run",
                "--project",
                "apps/tap-ai-backend",
                "python",
                "scripts/milvus_bootstrap.py",
            ],
            {**env, "TAP_ALLOW_INITIAL_MILVUS_ROOT": "1"},
        )
        phase = "milvus-collection"
        helpers.run(
            [
                "uv",
                "run",
                "--project",
                "apps/tap-ai-backend",
                "python",
                "scripts/tapper_collection.py",
                "ensure",
            ],
            env,
        )
        phase = "site-startup"
        if args.site == "owned" and args.execution_mode == "local":
            site_port = helpers.free_ports(1)[0]
            env["TAP_BROWSER_LOCAL_TEST_TARGET"] = f"http://127.0.0.1:{site_port}"
            local_site = subprocess.Popen(
                ["node", str(SITE_SCRIPT)],
                cwd=ROOT,
                env={
                    **env,
                    "TAP_CORE_SITE_HOST": "127.0.0.1",
                    "TAP_CORE_SITE_PORT": str(site_port),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(40):
                if local_site.poll() is not None:
                    raise RuntimeError("owned local site exited")
                try:
                    with urlopen(
                        env["TAP_BROWSER_LOCAL_TEST_TARGET"], timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("owned local site did not become ready")
        elif args.site == "owned":
            site_id = helpers.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-d",
                    "--name",
                    site_name,
                    "--label",
                    f"tap.owner={project}",
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
                    "--mount",
                    f"type=bind,source={SITE_SCRIPT},target=/opt/core-site.mjs,readonly",
                    BROWSER_IMAGE,
                    "node",
                    "/opt/core-site.mjs",
                ],
                env,
            )
            if not re.fullmatch(r"[0-9a-f]{64}", site_id):
                raise RuntimeError("owned site container ID invalid")
            for _ in range(40):
                probe = subprocess.run(
                    [
                        "docker",
                        "exec",
                        site_id,
                        "node",
                        "-e",
                        "fetch('http://127.0.0.1:8080/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))",
                    ],
                    env=env,
                    capture_output=True,
                    timeout=5,
                )
                if probe.returncode == 0:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("owned site did not become ready")
        if args.mode == "synthetic":
            env["TAPPER_BROWSER_CORE_SYNTHETIC"] = "1"
            model = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticModel)
            model_thread = Thread(target=model.serve_forever, daemon=True)
            model_thread.start()
            env["LITELLM_BASE_URL"] = f"http://127.0.0.1:{model.server_port}"
        phase = "browser-services"
        browser_log = (state / "browser-api.log").open("w")
        browser_api = subprocess.Popen(
            [
                ".venv/bin/uvicorn",
                "tap_platform.app:app"
                if args.lca_bridge
                else "tap_platform.browser_automation.http:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(browser_port),
            ],
            cwd=ROOT,
            env=selected_browser_env(env),
            stdout=browser_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        worker_log = (state / "browser-worker.log").open("w")
        command = (
            [".venv/bin/python", "scripts/browser_core_worker.py"]
            if site_id
            else [".venv/bin/python", "-m", "tap_platform.browser_automation.worker"]
        )
        if site_id:
            command.extend(
                [
                    "--site-container-id",
                    site_id,
                    "--site-name",
                    site_name,
                    "--owner",
                    project,
                ]
            )
        browser_worker = subprocess.Popen(
            command,
            cwd=ROOT,
            env=selected_browser_env(env),
            stdout=worker_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(120):
            if browser_api.poll() is not None or browser_worker.poll() is not None:
                raise RuntimeError("owned browser service exited before readiness")
            try:
                with urlopen(
                    env["TAP_BROWSER_API_ORIGIN"] + "/health/live", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("owned browser API readiness deadline")
        phase = "combined-launcher"
        app_log = state / "apps.log"
        with app_log.open("w") as output:
            app = subprocess.Popen(
                ["bash", "scripts/run-tapper-dev.sh"],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            helpers.wait_ready(
                env["TAPPER_API_BASE_URL"] + "/health/ready", app, app_log, api=True
            )
            helpers.wait_ready(env["TAPPER_WEB_BASE_URL"] + "/", app, app_log)
            phase = "chat-browser-e2e"
            run_playwright(
                env,
                timeout=(args.deadline_seconds if args.mode == "real" else 240)
                * max(
                    1, len(scenarios) + (env["TAPPER_BROWSER_CORE_RUN_CANCEL"] == "1")
                )
                + 180,
            )
        if args.mode == "synthetic":
            # Decisions now use the native journaled gateway; require actual page
            # observations in the persisted task, not the obsolete HTTP coding fixture.
            for case in scenarios:
                snapshot = json.loads(
                    (artifacts / f"{case['name']}-snapshot.json").read_text()
                )
                if not any(
                    e["kind"] == "browser.action_finished"
                    and e["payload"].get("observation_sequence")
                    for e in snapshot["events"]
                ):
                    raise RuntimeError(
                        "native browser fixture did not receive a page observation"
                    )
        success = True
    except BaseException as error:
        (artifacts / "failure.txt").write_text(
            f"phase={phase}\n{traceback.format_exc()}"
        )
        print(
            f"Browser core acceptance failed at {phase} ({type(error).__name__}); private state: {state}; evidence: {artifacts}",
            file=sys.stderr,
        )
    finally:
        if args.mode == "synthetic":
            (artifacts / "synthetic-model.json").write_text(
                json.dumps(
                    {
                        "calls": SyntheticModel.calls,
                        "stages": SyntheticModel.stages,
                        "observed": sorted(SyntheticModel.observed),
                    },
                    indent=2,
                )
            )
        if not success and app is not None:
            try:
                cancel_active_on_failure(env, artifacts)
            except BaseException as error:
                (artifacts / "failure-cancel.json").write_text(
                    json.dumps({"error_type": type(error).__name__})
                )
        for process in (app, browser_worker, browser_api, local_site):
            if process is not None:
                helpers.stop_owned_group(process, grace=125 if process is app else 35)
        if model is not None:
            model.shutdown()
            model.server_close()
        if model_thread is not None:
            model_thread.join(timeout=5)
        export_ok = False
        try:
            export_evidence(env, artifacts)
            export_ok = True
            if success and env["TAPPER_BROWSER_CORE_RUN_CANCEL"] == "1":
                verify_cancel_evidence(artifacts)
        except BaseException as error:
            print(
                f"Evidence export incomplete ({type(error).__name__}); owned state retained: {state}",
                file=sys.stderr,
            )
            success = False
        preserve_accounting = (
            owned
            and not success
            and (not export_ok or not failure_accounting_confirmed(artifacts))
        )
        if preserve_accounting:
            (artifacts / "conservative-accounting.json").write_text(
                json.dumps(
                    {
                        "status": "unconfirmed; owned database retained",
                        "max_model_calls": args.max_model_calls
                        if args.mode == "real"
                        else 8,
                        "max_cost_usd": str(
                            args.max_cost_usd
                            if args.mode == "real"
                            else Decimal("0.50")
                        ),
                        "export_complete": export_ok,
                    },
                    indent=2,
                )
            )
        for path in artifacts.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
        if site_id is not None:
            inspected = subprocess.run(
                ["docker", "inspect", site_id], env=env, capture_output=True, text=True
            )
            if inspected.returncode == 0:
                from browser_core_worker import verified_site_target

                verified_site_target(
                    json.loads(inspected.stdout), site_id, site_name, project
                )
                helpers.run(["docker", "rm", "-f", site_id], env)
        if owned and compose is not None:
            finish_owned_compose(compose, env, preserve_accounting=preserve_accounting)
        if success:
            shutil.rmtree(state)
    if success:
        print(
            f"Browser core {args.mode} acceptance passed; private evidence: {artifacts}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
