"""Browser receipts and bytes are only exposed through matching workflow scope."""

from datetime import UTC, datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.workflows.application.browser_artifacts import BrowserArtifactAdapter
from tap.modules.workflows.domain.models import WorkflowEvent


@pytest.mark.asyncio
async def test_browser_artifact_adapter_projects_real_receipt_and_scoped_bytes() -> None:
    task_id = "task-1"
    event = WorkflowEvent(
        "event-1",
        task_id,
        1,
        "node-1",
        1,
        "browser.coding_result",
        {
            "bundle_ref": "bundle-1",
            "execution_ref": "execution-1",
            "status": "passed",
            "replay_model_calls": 0,
        },
        datetime.now(UTC),
    )
    snapshot = {
        "request_json": {"kind": "browser", "browser": {"input_sha256": "a" * 64}},
        "events": (event,),
    }

    class Store:
        async def snapshot(self, _scope, requested):
            assert requested == task_id
            return snapshot

    class Client:
        project_id = VALIDATION_SCOPE.project_id

        async def request(self, _method, path):
            assert path == "/executions/execution-1"
            return {
                "project_id": self.project_id,
                "task_id": task_id,
                "execution_id": "execution-1",
                "bundle_id": "bundle-1",
                "input_sha256": "a" * 64,
                "status": "passed",
                "steps": [{"title": "Read quote", "status": "passed", "duration": 21}],
                "result": {"outputs": [{"customer": "Ada", "quote": "FOUND"}]},
                "evidence": [
                    {
                        "kind": "screenshot",
                        "object_ref": "evidence-1",
                        "sha256": "b" * 64,
                        "size_bytes": 3,
                    }
                ],
            }

        async def read_evidence(self, evidence_id):
            assert evidence_id == "evidence-1"
            return b"png", "image/png"

    adapter = BrowserArtifactAdapter(Store(), Client())
    view = await adapter.project(VALIDATION_SCOPE, task_id, snapshot)
    assert view["steps"] == [{"title": "Read quote", "status": "passed", "durationMs": 21}]
    assert view["outputs"] == [{"customer": "Ada", "quote": "FOUND"}]
    assert view["evidence"][0]["url"].endswith("/task-1/browser-evidence/evidence-1")
    assert await adapter.read(VALIDATION_SCOPE, task_id, "evidence-1") == (b"png", "image/png")
    with pytest.raises(LookupError):
        await adapter.read(VALIDATION_SCOPE, task_id, "foreign-evidence")


@pytest.mark.asyncio
async def test_browser_artifact_adapter_rejects_receipt_bound_to_other_task() -> None:
    class Client:
        project_id = VALIDATION_SCOPE.project_id

        async def request(self, *_args):
            return {"project_id": self.project_id, "task_id": "other-task"}

    event = WorkflowEvent(
        "event",
        "task-1",
        1,
        "node",
        1,
        "browser.coding_result",
        {"bundle_ref": "bundle-1", "execution_ref": "execution-1"},
        datetime.now(UTC),
    )
    snapshot = {
        "request_json": {"kind": "browser", "browser": {"input_sha256": "a" * 64}},
        "events": (event,),
    }
    adapter = BrowserArtifactAdapter(None, Client())
    with pytest.raises(LookupError):
        await adapter.project(VALIDATION_SCOPE, "task-1", snapshot)
