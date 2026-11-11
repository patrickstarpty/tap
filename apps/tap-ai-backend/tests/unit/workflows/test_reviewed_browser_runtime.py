"""Finish is independently gated even when an agent reports successful completion."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.workflows.adapters.browser_client import canonical_json_bytes
from tap.modules.workflows.adapters.direct_browser_runtime import DirectBrowserRuntime
from tap.modules.workflows.adapters.opencode_runtime import CodingConfig
from tap.modules.workflows.application.direct_browser import DirectBrowserResult

from .test_acceptance_contract import contract_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "pass",
        "omitted",
        "changed",
        "unknown",
        "stale",
        "forged_finish",
        "foreign_session",
        "foreign_project",
        "foreign_task",
        "foreign_finish",
        "foreign_finish_observation",
    ],
)
async def test_reviewed_contract_gates_live_runtime_without_a_model(mode):
    inputs = {"acceptance_contract": contract_data()}
    digest = hashlib.sha256(canonical_json_bytes(inputs)).hexdigest()

    class Store:
        async def graph_lineage(self, *_args):
            return "run", 0

        async def append_event(self, *_args):
            pass

    class Browser:
        def __init__(self):
            self.sequence = 0
            self.page = "https://owned.test/form"
            self.outputs = {}
            self.provenance = {}
            self.closed = False
            self.bundled = None

        async def session(self, *_args):
            return {
                "status": "active",
                "session_id": "session",
                "project_id": "wrong" if mode == "foreign_project" else VALIDATION_SCOPE.project_id,
                "task_id": "wrong" if mode == "foreign_task" else "task",
            }

        async def action(self, session_id, action, *_args, **_kwargs):
            self.sequence += 1
            if action["type"] == "navigate":
                self.page = action["url"]
            if action["type"] == "click":
                self.page = "https://owned.test/result"
            observation = {
                "session_id": session_id,
                "sequence": self.sequence,
                "url": self.page,
                "bounded_snapshot": "Owned form",
                "documents": [{"frame_id": "main", "document_id": self.page, "url": self.page}],
            }
            if action["type"] == "read":
                value = (
                    "31"
                    if mode == "changed" and action["name"] == "age"
                    else "30"
                    if action["name"] == "age"
                    else "42"
                )
                self.outputs[action["name"]] = value
                self.provenance[action["name"]] = {
                    "source_url": self.page,
                    "source_frame_id": "main",
                    "source_document_id": self.page,
                    "observation_sequence": self.sequence,
                }
                observation["extracted"] = {
                    "name": action["name"],
                    "value": value,
                    **self.provenance[action["name"]],
                }
                if mode == "stale" and action["name"] == "age":
                    observation["extracted"]["observation_sequence"] = 0
            if action["type"] == "finish":
                if mode == "foreign_finish_observation":
                    observation["session_id"] = "foreign"
                observation["result"] = {
                    "outputs": [{**self.outputs, "provenance": self.provenance}]
                }
                if mode == "forged_finish":
                    observation["result"]["outputs"][0]["age"] = "31"
            return {
                "status": "unknown"
                if mode == "unknown" and action["type"] == "read"
                else "completed",
                "action_id": f"receipt-{self.sequence}",
                "session_id": "wrong"
                if mode == "foreign_session"
                or (mode == "foreign_finish" and action["type"] == "finish")
                else session_id,
                "observation": observation,
            }

        async def bundle(self, task_id, script, input_data, *_args):
            self.bundled = (script, input_data)
            assert input_data == inputs
            assert "reviewed input proof missing" in script
            return {"bundle_id": "bundle", "input_sha256": digest}

        async def request(self, *_args):
            return {
                "status": "passed",
                "execution_id": "execution",
                "bundle_id": "bundle",
                "input_sha256": digest,
                "result": {"outputs": [self.outputs]},
            }

        async def cancel_session(self, *_args):
            self.closed = True
            return {"stop_receipt": {"stopped": True}}

    class Runtime(DirectBrowserRuntime):
        async def _run_agent(self, loop, context):
            actions = [
                {"type": "navigate", "url": "https://owned.test/form", "step": "open"},
                {"type": "fill", "selector": "#age", "value": "30", "step": "set"},
                {
                    "type": "read",
                    "selector": "#age",
                    "name": "age",
                    "attribute": "value",
                    "equals": "30",
                    "target_texts": ["Age"],
                    "step": "check",
                },
                {"type": "click", "selector": "#next", "step": "next"},
                {
                    "type": "read",
                    "selector": "#quote",
                    "name": "quote",
                    "positive": True,
                    "step": "result",
                },
            ]
            steps = [
                {
                    "id": a["step"],
                    "keyword": "Then"
                    if a["type"] == "read"
                    else "Given"
                    if a["type"] == "navigate"
                    else "When",
                    "title": a["step"],
                    **({"verification_kind": "input"} if a["step"] == "check" else {}),
                }
                for a in actions
            ]
            await loop.progress.handle({"type": "plan", "steps": steps}, "plan", context["execute"])
            for index, action in enumerate(actions):
                if mode == "omitted" and action["step"] == "check":
                    continue
                await loop.progress.handle(action, f"action-{index}", context["execute"])
            return DirectBrowserResult(
                "passed", {}, list(loop.progress.recorded_actions), loop.progress.steps
            )

    runtime = Runtime(CodingConfig.for_test(), store=Store(), gateway_factory=lambda *_args: None)
    runtime.browser = Browser()
    request = {
        "kind": "browser",
        "goal": "Use age 30 and get quote",
        "model_alias": "qwen-plus",
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        "budget_limit": {"model_calls": 10, "cost_micros": 1000},
        "browser": {
            "start_url": "https://owned.test/form",
            "input_data": inputs,
            "input_sha256": digest,
            "allowed_origins": ["https://owned.test"],
        },
    }
    outcome = await runtime.run(
        "task", request, {"budget_used": {}}, VALIDATION_SCOPE, "lease", "node", 1
    )
    assert runtime.browser.closed
    assert outcome.status == ("passed" if mode == "pass" else "failed")
    assert (runtime.browser.bundled is not None) is (mode == "pass")
