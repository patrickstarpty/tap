"""Opt-in, zero-provider-cost OpenCode/native-tool/HTTP/Chromium protocol proof.

Runs only owned loopback fixtures, with SQLite persistence and no Docker services.
The scripted model is a protocol fixture, never a claim of model autonomy.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import shutil
import socket
import tempfile
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.http import create_browser_router
from tap_platform.browser_automation.local import LocalBrowserRuntime
from tap_platform.browser_automation.objects import (
    BrowserEvidenceSink,
    BrowserObjectStore,
)
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import BrowserStore, actions, metadata
from tap_platform.browser_automation.worker import BrowserWorker

from tap.modules.workflows.adapters.browser_client import BrowserApiClient
from tap.modules.workflows.adapters.coding_progress_guard import (
    CodingProgressGuard,
    browser_progress_units,
)
from tap.modules.workflows.adapters.coding_mailbox import CodingMailbox
from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
from tap.modules.workflows.adapters.opencode_profiles import build_config
from tap.modules.workflows.adapters.opencode_runtime import CodingConfig
from tap.modules.workflows.application.browser_steps import BrowserProgress

ROOT = Path(__file__).resolve().parents[1]
CATALOG_LABELS = [
    "Ask a question",
    *[f"Option {i}" for i in range(2, 16)],
    "Start request",
]
PLAN = {
    "type": "plan",
    "steps": [
        {"id": "click", "keyword": "When", "title": "Click owned button"},
        {"id": "result", "keyword": "Then", "title": "Read changed result"},
    ],
}
ACTIONS = [
    {"type": "navigate", "url": "http://example.test/"},
    {"type": "snapshot"},
    PLAN,
    {"type": "click", "step": "click", "selector": "#go"},
    {
        "type": "read",
        "step": "result",
        "selector": "#result",
        "name": "result",
        "equals": "CLICKED",
    },
]
REJECTED_PLAN = {"type": "plan", "steps": [PLAN["steps"][0], PLAN["steps"][0]]}
SCENARIOS = {
    "normal": ACTIONS,
    "plain_text": [],
    "schema_error": [{"type": "click", "step": "click"}],
    "server_rejected": [REJECTED_PLAN],
    "corrected": [REJECTED_PLAN, *ACTIONS],
    "preplan_write": [{"type": "click", "step": "click", "selector": "#go"}],
    "control_catalog_batched": [
        {"type": "navigate", "url": "http://example.test/catalog-bounded"},
        PLAN,
        {"native_catalog_page": 1},
        {"type": "click", "step": "click", "selector": "CATALOG_OBSERVED_SELECTOR"},
        {
            "type": "read",
            "step": "result",
            "selector": "#result",
            "name": "result",
            "equals": "STARTED",
        },
    ],
    "control_catalog_bounded": [
        {"type": "navigate", "url": "http://example.test/catalog-bounded"},
        PLAN,
        *[{"native_catalog_page": offset} for offset in (1, 5)],
        {"type": "click", "step": "click", "selector": "CATALOG_OBSERVED_SELECTOR"},
        {
            "type": "read",
            "step": "result",
            "selector": "#result",
            "name": "result",
            "equals": "STARTED",
        },
    ],
    "control_catalog": [
        {"type": "navigate", "url": "http://example.test/catalog"},
        PLAN,
        *[{"native_catalog_page": offset} for offset in (1, 5, 9, 13)],
        {"type": "click", "step": "click", "selector": "CATALOG_OBSERVED_SELECTOR"},
        {
            "type": "read",
            "step": "result",
            "selector": "#result",
            "name": "result",
            "equals": "STARTED",
        },
    ],
    "unsupported_selector": [
        {
            "type": "plan",
            "steps": [
                {"id": "open", "keyword": "Given", "title": "Observe owned controls"},
                {"id": "click", "keyword": "When", "title": "Continue owned flow"},
                {"id": "result", "keyword": "Then", "title": "Verify changed state"},
            ],
        },
        {"type": "navigate", "step": "open", "url": "http://example.test/"},
        {"type": "snapshot", "step": "open"},
        {"type": "click", "step": "click", "selector": "aria/Get in touch"},
        {"type": "snapshot", "step": "open"},
        {"type": "click", "step": "click", "selector": "CURRENT_OBSERVED_REF"},
        {
            "type": "read",
            "step": "result",
            "selector": "#result",
            "name": "result",
            "equals": "CLICKED",
        },
    ],
    "controlled_preflight": [
        {
            "type": "plan",
            "steps": [
                {"id": "open", "keyword": "Given", "title": "Open owned form"},
                {"id": "edit", "keyword": "When", "title": "Fill owned input"},
                {"id": "result", "keyword": "Then", "title": "Verify current input"},
            ],
        },
        {"type": "navigate", "step": "open", "url": "http://example.test/"},
        {"type": "fill", "step": "edit", "selector": "#missing", "value": "Ada"},
        {"type": "snapshot", "step": "edit"},
        {"type": "fill", "step": "edit", "selector": "#name", "value": "Ada"},
        {
            "type": "read",
            "step": "result",
            "selector": "#name",
            "name": "name",
            "attribute": "value",
            "equals": "Ada",
        },
    ],
    "outside_start": [
        {"type": "navigate", "url": "http://example.test/not-authorized"}
    ],
}


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class Site(QuietHandler):
    def do_GET(self):
        body = b"""<title>Owned protocol fixture</title><label>Name<input id="name"></label><button id="go" onclick="document.querySelector('#result').textContent='CLICKED'">Continue</button><p id="result">READY</p>"""
        if self.path in {"/catalog", "/catalog-bounded"}:
            labels = (
                CATALOG_LABELS
                if self.path == "/catalog"
                else [
                    "Ask a question",
                    *[f"Option {i}" for i in range(2, 8)],
                    "Start request",
                ]
            )
            controls = "".join(
                '<button onclick="document.querySelector(&quot;#result&quot;).textContent=&quot;'
                + ("STARTED" if label == "Start request" else "WRONG")
                + '&quot;">'
                + label
                + "</button>"
                for label in labels
            )
            body = (
                "<title>Owned neutral directory</title>"
                + (
                    "<p>" + "Neutral background information. " * 400 + "</p>"
                    if self.path == "/catalog-bounded"
                    else ""
                )
                + controls
                + '<p id="result">READY</p>'
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


def http_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fixture_result_check(action, observation):
    expected = action.get("equals")
    actual = observation.get("extracted", {}).get("value")
    return {
        "expected_result": expected,
        "result_verified": actual == expected if isinstance(expected, str) else None,
    }


async def scenario(name, root, browser, worker, binary):
    state = {
        "provider_requests": 0,
        "text_responses": 0,
        "tool_responses": 0,
        "json_arguments": 0,
        "json_text_responses": 0,
        "accepted_plans": 0,
        "completed_actions": 0,
        "server_rejections": 0,
        "expected_result": None,
        "result_verified": None,
        "requested_operations": [],
        "discovery_observations": 0,
    }
    script = SCENARIOS[name]
    catalog_case = name in {
        "control_catalog",
        "control_catalog_bounded",
        "control_catalog_batched",
    }
    bounded_catalog = name in {"control_catalog_bounded", "control_catalog_batched"}
    batched_catalog = name == "control_catalog_batched"
    expected_catalog_labels = (
        ["Ask a question", *[f"Option {i}" for i in range(2, 8)], "Start request"]
        if bounded_catalog
        else CATALOG_LABELS
    )
    expected_offsets = [1, 5] if bounded_catalog else [1, 5, 9, 13]
    observed_target = {}
    catalog = {}
    catalog_records = []
    read_call_ids = set()
    processed_read_ids = set()
    native_pages = {}
    native_page_order = []

    class Model(QuietHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["provider_requests"] += 1
            if state["provider_requests"] > 10:
                self.send_response(429)
                self.end_headers()
                self.wfile.write(
                    b'{"error":{"type":"fixture_limit","retryable":false}}'
                )
                return
            index = state["tool_responses"]
            tool = None
            if request.get("tools") and index < len(script):
                state["tool_responses"] += 1
                state["json_arguments"] += 1
                action = dict(script[index])
                native_tool = "tap_browser"
                if catalog_case:
                    for message in request.get("messages", []):
                        if message.get("role") != "tool":
                            continue
                        content = message.get("content")
                        if isinstance(content, list):
                            content = "\n".join(
                                part["text"]
                                for part in content
                                if isinstance(part, dict)
                                and isinstance(part.get("text"), str)
                            )
                        if not isinstance(content, str):
                            continue
                        if not catalog:
                            try:
                                receipt = json.loads(content)
                            except ValueError:
                                receipt = {}
                            if isinstance(receipt, dict) and isinstance(
                                receipt.get("control_catalog"), dict
                            ):
                                catalog.update(receipt["control_catalog"])
                                state["goal_absent_from_inline_controls"] = (
                                    "Start request"
                                    not in json.dumps(
                                        {
                                            key: receipt.get(key)
                                            for key in (
                                                "related_fields",
                                                "discovery_candidates",
                                            )
                                        }
                                    )
                                )
                        call_id = message.get("tool_call_id")
                        if (
                            call_id in read_call_ids
                            and call_id not in processed_read_ids
                        ):
                            records = []
                            for line in content.splitlines():
                                found = re.match(
                                    r"^\s*(?:\d+[:|]\s*)?(\{.*\})\s*$", line
                                )
                                if found:
                                    item = json.loads(found.group(1))
                                    if isinstance(item, dict) and isinstance(
                                        item.get("selector"), str
                                    ):
                                        records.append(item)
                            assert records, "native_read_return_missing_catalog_records"
                            native_pages[call_id] = records
                            processed_read_ids.add(call_id)
                    catalog_records[:] = [
                        row
                        for key in native_page_order
                        for row in native_pages.get(key, [])
                    ]
                    state["native_page_record_counts"] = [
                        len(native_pages[key])
                        for key in native_page_order
                        if key in native_pages
                    ]
                    if "native_catalog_page" in action:
                        assert catalog.get("page_size") == 4, "catalog_metadata_missing"
                        schema = next(
                            tool["function"]["parameters"]
                            for tool in request["tools"]
                            if tool["function"]["name"] == "read"
                        )
                        assert {"filePath", "offset", "limit"} <= schema[
                            "properties"
                        ].keys(), "native_read_schema_changed"
                        native_tool = "read"
                        native_arguments = {
                            "filePath": catalog["file"],
                            "offset": action["native_catalog_page"],
                            "limit": catalog["page_size"],
                        }
                        state.setdefault("native_read_offsets", []).append(
                            action["native_catalog_page"]
                        )
                        read_call_ids.add(f"call_{index}")
                        native_page_order.append(f"call_{index}")
                    elif action.get("selector") == "CATALOG_OBSERVED_SELECTOR":
                        matches = [
                            entry
                            for entry in catalog_records
                            if entry.get("label") == "Start request"
                        ]
                        assert len(matches) == 1, "catalog_goal_not_uniquely_observed"
                        action["selector"] = matches[0]["selector"]
                        observed_target["selector"] = action["selector"]
                        state["selector_from_native_read"] = True
                if action.get("selector") == "CURRENT_OBSERVED_REF":
                    action["selector"] = observed_target["selector"]
                    state["current_observed_ref_used"] = True
                tool = {
                    "index": 0,
                    "id": f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": native_tool,
                        "arguments": json.dumps(
                            native_arguments
                            if native_tool == "read"
                            else {"action": action}
                        ),
                    },
                }
                if batched_catalog and native_tool == "read":
                    second_id = f"call_{index}_page2"
                    read_call_ids.add(second_id)
                    native_page_order.append(second_id)
                    state["native_read_offsets"].append(5)
                    tool = [
                        tool,
                        {
                            "index": 1,
                            "id": second_id,
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": json.dumps(
                                    {**native_arguments, "offset": 5}
                                ),
                            },
                        },
                    ]
                    state["native_read_batches"] = 1
                    state["json_arguments"] += 1
            else:
                state["text_responses"] += 1
                if name == "plain_text":
                    state["json_text_responses"] += 1
            content = (
                json.dumps({"action": PLAN})
                if name == "plain_text"
                else "Fixture complete."
            )
            frame = {
                "id": "chatcmpl-owned",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "qwen-plus",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            **(
                                {
                                    "tool_calls": tool
                                    if isinstance(tool, list)
                                    else [tool]
                                }
                                if tool
                                else {"content": content}
                            ),
                        },
                        "finish_reason": "tool_calls" if tool else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }
            body = ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    model = http_server(Model)
    (root / name).mkdir()
    mailbox = CodingMailbox.create(root / name / "mailbox")
    container = LocalCodingContainer(
        CodingConfig.for_test(),
        mailbox.root,
        opencode_binary=binary,
        node_binary=Path(shutil.which("node")),
        client_path=ROOT / "deploy/browser/opencode-client.mjs",
    )
    controlled = name == "controlled_preflight"
    session = await browser.session(
        name,
        ["http://example.test"],
        90,
        f"session-{name}",
        **({"control_version": "browser-control-v1"} if controlled else {}),
    )
    assert session["status"] == "active"

    async def emit(kind, _payload):
        if kind == "browser.plan_created":
            state["accepted_plans"] += 1
        if (
            kind == "browser.discovery_finished"
            and _payload.get("outcome") == "completed"
        ):
            state["discovery_observations"] += 1

    progress = BrowserProgress(
        emit,
        frozen_start_url=(
            "http://example.test/catalog-bounded"
            if bounded_catalog
            else "http://example.test/catalog"
            if catalog_case
            else "http://example.test/"
        ),
        discovery_session_id=session["session_id"],
    )

    guard = CodingProgressGuard()

    async def execute(action, key):
        state["requested_operations"].append(action["type"])
        result = await browser.action(
            session["session_id"],
            {**action, **({"expected_epoch": 0} if controlled else {})},
            key,
            deadline_seconds=20,
        )
        if controlled and result.get("status") == "preflight_rejected":
            with worker.service.store.engine.connect() as connection:
                rows = connection.execute(
                    select(actions.c.request).where(
                        actions.c.session_id == session["session_id"]
                    )
                ).all()
            state["preflight_feedback"] = {
                "status": "preflight_rejected",
                "category": result.get("category"),
                "business_action_admitted": result.get("business_action_admitted"),
                "actual_fill_rows_at_rejection": sum(
                    row.request.get("type") == "fill" for row in rows
                ),
            }
        if catalog_case and action["type"] == "read":
            state["catalog_result_verified"] = (
                result.get("observation", {}).get("extracted", {}).get("value")
                == "STARTED"
            )
        if controlled and action["type"] == "read":
            state["corrected_value_verified"] = (
                result.get("observation", {}).get("extracted", {}).get("value") == "Ada"
            )
        if name == "unsupported_selector" and action["type"] == "snapshot":
            observation = result.get("observation", {})
            match = re.search(
                r'button "Continue" \[ref=((?:f[1-9]\d*)?e[1-9]\d*)\]',
                observation.get("bounded_snapshot", ""),
            )
            assert match, "owned button current ref missing"
            observed_target.update(
                selector="aria-ref=" + match.group(1), sequence=observation["sequence"]
            )
        if result.get("status") == "completed":
            state["completed_actions"] += 1
            if action["type"] == "read":
                state.update(
                    fixture_result_check(action, result.get("observation", {}))
                )
        return result

    async def handle(item):
        if item.kind == "model":
            if bounded_catalog:
                units = browser_progress_units(progress.recorded_actions)
                allowed = guard.allow(
                    bool(progress.steps), units, observed=progress.observation_progress
                )
                state.setdefault("guard_checks", []).append(
                    {
                        "tool_responses_before_turn": state["tool_responses"],
                        "has_plan": bool(progress.steps),
                        "progress_units": units,
                        "observed": progress.observation_progress,
                        "allowed": allowed,
                    }
                )
                if not allowed:
                    return {
                        "status": 409,
                        "contentType": "application/json",
                        "bodyBase64": base64.b64encode(
                            b'{"error":{"code":"no_progress","retryable":false}}'
                        ).decode(),
                    }

            def forward():
                req = Request(
                    f"http://127.0.0.1:{model.server_port}/v1/chat/completions",
                    data=base64.b64decode(item.payload["bodyBase64"]),
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(req, timeout=10) as response:
                    return {
                        "status": 200,
                        "contentType": "text/event-stream",
                        "bodyBase64": base64.b64encode(response.read()).decode(),
                    }

            return await asyncio.to_thread(forward)
        result = await progress.handle(
            item.payload,
            item.identifier,
            lambda action: execute(action, item.identifier),
        )
        if result.get("error"):
            state["server_rejections"] += 1
        if (
            name == "unsupported_selector"
            and result.get("code") == "unsupported_selector_syntax"
        ):
            with worker.service.store.engine.connect() as connection:
                rows = connection.execute(
                    select(actions.c.request).where(
                        actions.c.session_id == session["session_id"]
                    )
                ).all()
            state["selector_feedback"] = {
                "code": result["code"],
                "status": result.get("status"),
                "business_action_admitted": result.get("business_action_admitted"),
                "actual_clicks_at_rejection": sum(
                    row.request.get("type") == "click" for row in rows
                ),
                "observation_sequence_at_rejection": observed_target["sequence"],
            }
        return result

    request = {
        "model_alias": "qwen-plus",
        "root": {
            "agent_id": "orchestrator",
            "revision_id": "test",
            "description": "Owned protocol fixture",
            "instruction": "Use the native browser tool for the owned fixture.",
        },
    }
    config = build_config(
        request,
        "http://127.0.0.1:39001",
        "owned-synthetic-token",
        input_tokens=30720,
        output_tokens=2048,
    )
    running = None
    try:
        await container.start(config, "owned-synthetic-token", 90)
        await container.set_frozen_input(b"{}", hashlib.sha256(b"{}").hexdigest())
        await container.set_goal(
            "Execute the owned protocol fixture only. No external sites or purchases."
        )
        running = asyncio.create_task(container.run_prompt())
        async with asyncio.timeout(75):
            while not running.done():
                if not await mailbox.serve_once(handle):
                    await asyncio.sleep(0.01)
            terminal = await running
        state["terminal"] = terminal.get("terminal")
        state["diagnostics"] = terminal.get("diagnostics", [])
        state["unknown"] = progress.unknown_action
        (root / name / "safe-summary.json").write_text(json.dumps(state, indent=2))
        assert state["provider_requests"] <= 10, state
        assert state["json_arguments"] == state["tool_responses"] + (
            1 if batched_catalog else 0
        ), state
        assert state["tool_responses"] == len(script), state
        if name in {"normal", "corrected"}:
            assert state["accepted_plans"] == 1, state
            assert state["result_verified"] and state["completed_actions"] == 4, state
            assert state["requested_operations"] == [
                "navigate",
                "snapshot",
                "click",
                "read",
            ], state
            assert state["discovery_observations"] == 2, state
        if name == "plain_text":
            assert (
                state["tool_responses"]
                == state["completed_actions"]
                == state["accepted_plans"]
                == 0
            ), state
            assert state["json_text_responses"] > 0, state
        if name == "schema_error":
            assert state["completed_actions"] == state["accepted_plans"] == 0, state
            assert any(
                d["category"] == "validation_error" for d in state["diagnostics"]
            ), state
        if name in {"server_rejected", "preplan_write", "outside_start"}:
            assert state["requested_operations"] == [], state
            assert state["accepted_plans"] == state["completed_actions"] == 0, state
        if name in {"server_rejected", "corrected", "preplan_write", "outside_start"}:
            assert state["server_rejections"] == 1, state
            assert any(
                d["category"] == "server_rejected" for d in state["diagnostics"]
            ), state
        if catalog_case:
            with worker.service.store.engine.connect() as connection:
                rows = connection.execute(
                    select(actions.c.request, actions.c.status)
                    .where(actions.c.session_id == session["session_id"])
                    .order_by(actions.c.sequence)
                ).all()
            clicks = [row for row in rows if row.request["type"] == "click"]
            state["native_directory_pages_read"] = len(processed_read_ids)
            state["native_directory_records_read"] = len(catalog_records)
            state["catalog_metadata"] = {
                key: catalog.get(key)
                for key in (
                    "observed_fields_count",
                    "eligible_count",
                    "catalog_count",
                    "omitted",
                    "source_limit",
                    "source_bounded",
                    "page_size",
                    "line_count",
                )
            }
            assert state["goal_absent_from_inline_controls"], state
            assert state["native_directory_pages_read"] == len(expected_offsets), state
            assert state["native_read_offsets"] == expected_offsets, state
            assert state["native_page_record_counts"] == [4] * len(expected_offsets), (
                state
            )
            assert [
                entry["label"] for entry in catalog_records
            ] == expected_catalog_labels, "catalog source order or coverage changed"
            assert len({entry["selector"] for entry in catalog_records}) == len(
                expected_catalog_labels
            ), "catalog duplicate or missing selector"
            state["native_one_based_offsets_verified"] = True
            state["catalog_source_order_complete_without_duplicates"] = True
            state["catalog_actual_click_count"] = len(clicks)
            assert len(clicks) == 1 and clicks[0].status == "completed", state
            assert clicks[0].request["selector"] == observed_target["selector"], state
            assert state.get("catalog_result_verified") and not state["unknown"], state
            if bounded_catalog:
                checks = state["guard_checks"]
                assert all(check["allowed"] for check in checks), state
                read_window = [
                    check
                    for check in checks
                    if check["has_plan"]
                    and check["tool_responses_before_turn"]
                    in ({2, 3} if batched_catalog else {2, 3, 4})
                ]
                assert (
                    len(read_window) == (2 if batched_catalog else 3)
                    and len({check["progress_units"] for check in read_window}) == 1
                ), state
                assert (
                    checks[-1]["progress_units"] > read_window[-1]["progress_units"]
                ), state
                state["guard_scope"] = (
                    "Actual CodingProgressGuard on each protocol model request; not full CodingRuntime/BudgetGate"
                )
                state["native_file_reads_did_not_reset_progress"] = True
        if name == "unsupported_selector":
            with worker.service.store.engine.connect() as connection:
                rows = connection.execute(
                    select(actions.c.request, actions.c.status)
                    .where(actions.c.session_id == session["session_id"])
                    .order_by(actions.c.sequence)
                ).all()
            clicks = [row for row in rows if row.request["type"] == "click"]
            state["actual_action_receipts"] = [
                {"operation": row.request["type"], "status": row.status} for row in rows
            ]
            feedback = state.get("selector_feedback", {})
            assert feedback.get("business_action_admitted") is False, state
            assert feedback.get("actual_clicks_at_rejection") == 0, state
            assert (
                observed_target["sequence"]
                > feedback["observation_sequence_at_rejection"]
            ), state
            assert len(clicks) == 1 and clicks[0].status == "completed", state
            assert clicks[0].request["selector"] == observed_target["selector"], state
            assert state["result_verified"] and state["current_observed_ref_used"], (
                state
            )
            assert state["server_rejections"] == 1 and not state["unknown"], state
        if controlled:
            with worker.service.store.engine.connect() as connection:
                rows = connection.execute(
                    select(actions.c.request, actions.c.status)
                    .where(actions.c.session_id == session["session_id"])
                    .order_by(actions.c.sequence)
                ).all()
            state["actual_action_receipts"] = [
                {"operation": row.request["type"], "status": row.status} for row in rows
            ]
            feedback = state.get("preflight_feedback", {})
            assert feedback.get("business_action_admitted") is False, state
            assert feedback.get("actual_fill_rows_at_rejection") == 0, state
            assert feedback.get("category") == "target_missing", state
            assert (
                state["corrected_value_verified"] and state["server_rejections"] == 1
            ), state
            fills = [row for row in rows if row.request["type"] == "fill"]
            assert len(fills) == 1 and fills[0].status == "completed", state
            assert not state["unknown"], state
        return state
    finally:
        if running and not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        try:
            assert await container.stop()
        finally:
            try:
                await browser.cancel_session(session["session_id"])
            finally:
                model.shutdown()
                model.server_close()


async def main(args):
    binary = args.opencode_binary.resolve(strict=True)
    version = await asyncio.create_subprocess_exec(
        str(binary), "--version", stdout=asyncio.subprocess.PIPE
    )
    output, _ = await version.communicate()
    assert output.decode().strip() == "1.19.5"
    root = Path(tempfile.mkdtemp(prefix="tap-opencode-protocol-"))
    engine = create_engine(f"sqlite:///{root / 'browser.db'}")
    metadata.create_all(engine)
    store = BrowserStore(engine)
    objects = BrowserObjectStore(root / "objects")
    service = BrowserService(store, objects)
    principal = AccessPrincipal(
        project_id="p",
        principal_id="owned",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        enabled=True,
        actions=frozenset(
            {
                "browser.sessions.create",
                "browser.sessions.read",
                "browser.sessions.cancel",
                "browser.actions.create",
                "browser.actions.read",
            }
        ),
    )
    app = FastAPI()
    app.include_router(create_browser_router(service, principal, "a" * 32))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0.01)
    site = http_server(Site)
    runtime = LocalBrowserRuntime(
        evidence_sink=BrowserEvidenceSink(store, objects),
        project_id="p",
        test_target=f"http://127.0.0.1:{site.server_port}",
    )
    worker = BrowserWorker(service, runtime, "p", "owned-protocol-worker")
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await worker.tick()
            await asyncio.sleep(0.02)

    pumping = asyncio.create_task(pump())
    report = {
        "opencode_version": "1.19.5",
        "provider": "owned_synthetic",
        "paid_calls": 0,
        "browser_chain": "HTTP/SQLite/BrowserWorker/LocalBrowserRuntime/Chromium",
        "model_forwarding": "test handler to owned HTTP synthetic provider; not CodingRuntime/BudgetGate",
        "financial_guard_tested": False,
        "cases": {},
    }
    try:
        client = BrowserApiClient(f"http://127.0.0.1:{port}", "p", "a" * 32)
        for name in args.cases:
            report["cases"][name] = await scenario(name, root, client, worker, binary)
            args.evidence.write_text(json.dumps(report, indent=2))
            print(json.dumps({"case": name, **report["cases"][name]}), flush=True)
    finally:
        stop.set()
        await pumping
        await worker.shutdown()
        server.should_exit = True
        await serving
        site.shutdown()
        site.server_close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode-binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--cases", nargs="+", choices=SCENARIOS, default=list(SCENARIOS)
    )
    asyncio.run(main(parser.parse_args()))
