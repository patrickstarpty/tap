import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import ModelGatewayUnavailable, ModelToolCall
from tap.modules.workflows.adapters.direct_browser_runtime import DirectBrowserRuntime
from tap.modules.workflows.adapters.opencode_runtime import CodingConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "cancel",
        "control_race",
        "bundle_failure",
        "session_unknown",
        "model_unavailable",
        "slow_receipt",
    ],
)
async def test_default_live_runtime_has_no_coding_container_or_automatic_replay(mode):
    stopping = False
    decisions = iter(
        [
            {
                "type": "plan",
                "steps": [
                    {"id": "open", "keyword": "Given", "title": "Open"},
                    {"id": "read", "keyword": "Then", "title": "Read quote"},
                ],
            },
            {"type": "navigate", "url": "https://example.com", "step": "open"},
            {
                "type": "read",
                "target": "1:0",
                "name": "quote",
                "contains": "/month",
                "step": "read",
            },
            {"type": "finish"},
        ]
    )
    model_requests = []

    class Gateway:
        async def chat(self, request):
            nonlocal stopping
            model_requests.append(request)
            if mode == "model_unavailable":
                raise ModelGatewayUnavailable()
            if len(model_requests) == 2:
                assert request.messages[0].role == "assistant"
                assert request.messages[0].tool_calls[0].name == "browser_step"
                assert request.messages[1].role == "tool"
                assert request.messages[1].tool_call_id == request.messages[0].tool_calls[0].call_id
                assert "planned" in request.messages[1].content
            if mode == "control_race" and len(model_requests) == 3:
                from tap.modules.workflows.domain.commands import WorkflowDispatchBlocked

                raise WorkflowDispatchBlocked("workflow control blocks new dispatch")
            if mode == "cancel" and len(model_requests) == 3:
                stopping = True
                await asyncio.sleep(0.2)
            return SimpleNamespace(
                tool_calls=(ModelToolCall("call", "browser_step", json.dumps(next(decisions))),)
            )

    class Store:
        async def graph_lineage(self, *args):
            return ("r1", 0)

        async def append_event(self, *args):
            pass

    class Browser:
        def __init__(self):
            self.stopped = False
            self.calls = []

        async def session(self, *args):
            if mode == "session_unknown":
                return {"status": "unknown", "session_id": "s1", "stop_receipt": None}
            return {"status": "active", "session_id": "s1"}

        async def action(self, sid, action, key, **kwargs):
            if mode == "slow_receipt" and kwargs["deadline_seconds"] < 45:
                raise TimeoutError("receipt arrives after 45 seconds")
            self.calls.append(action)
            o = {
                "sequence": len(self.calls),
                "url": "https://example.com",
                "bounded_snapshot": "Quote",
                "fields": [{"selector": "#quote", "label": "Quote"}],
            }
            if action["type"] == "read":
                o["extracted"] = {"name": "quote", "value": "10.62/month"}
            if action["type"] == "finish":
                o["result"] = {"outputs": [{"quote": "10.62/month"}]}
            return {
                "status": "completed",
                "action_id": "a" + str(len(self.calls)),
                "observation": o,
            }

        async def bundle(self, *args):
            if mode == "bundle_failure":
                raise ValueError("storage unavailable")
            assert "10.62/month" not in args[1]
            return {"bundle_id": "b1", "input_sha256": sha}

        async def request(self, method, path, body, key):
            assert body["live_session_id"] == "s1"
            return {
                "status": "passed",
                "execution_id": "e1",
                "result": {"outputs": [{"quote": "10.62/month"}]},
            }

        async def cancel_session(self, *args, **kwargs):
            self.stopped = True
            return {"stop_receipt": {"stopped": True}, "status": "canceled"}

    inputs = {"goal": "quote"}
    sha = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    runtime = DirectBrowserRuntime(
        CodingConfig.for_test(), store=Store(), gateway_factory=lambda *args: Gateway()
    )
    runtime.browser = Browser()
    request = {
        "kind": "browser",
        "model_alias": "qwen-plus",
        "goal": "quote",
        "deadline_at": (
            datetime.now(UTC) + timedelta(seconds=180 if mode == "slow_receipt" else 60)
        ).isoformat(),
        "budget_limit": {"model_calls": 10, "cost_micros": 100000},
        "browser": {
            "start_url": "https://example.com",
            "allowed_origins": ["https://example.com"],
            "input_data": inputs,
            "input_sha256": sha,
        },
    }

    async def stop_requested():
        return stopping

    result = await runtime.run(
        "t1",
        request,
        {"budget_used": {}},
        VALIDATION_SCOPE,
        "lease",
        "node",
        1,
        stop_requested=stop_requested,
    )
    if mode == "session_unknown":
        assert result.status == "unknown"
        assert not model_requests
        return
    assert runtime.browser.stopped
    if mode == "model_unavailable":
        assert result.status == "failed"
        assert not runtime.browser.calls
        return
    if mode in {"cancel", "control_race"}:
        assert result.status == "canceled"
        assert [a["type"] for a in runtime.browser.calls] == ["navigate"]
        return
    assert result.status == "passed"
    if mode == "bundle_failure":
        assert result.terminal_evidence["script_status"] == "failed"
        assert result.terminal_evidence["result"]["outputs"][0]["quote"] == "10.62/month"
    assert [a["type"] for a in runtime.browser.calls] == ["navigate", "read", "finish"]
    assert len(model_requests) == 4
    assert all([t.wire_name for t in r.tools] == ["browser_step"] for r in model_requests)
