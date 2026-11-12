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


def test_browser_transport_error_preserves_application_read_contract() -> None:
    from tap.modules.workflows.adapters.browser_client import BrowserApiError
    from tap.modules.workflows.ports.browser import BrowserReadError

    error = BrowserApiError(503, "unavailable")
    assert isinstance(error, BrowserReadError)
    assert isinstance(error, RuntimeError)
    assert error.status == 503
    assert error.detail == "unavailable"
    assert str(error) == "browser API 503: unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_kind", ["browser.action_finished", "browser.discovery_finished"])
async def test_lca_projection_uses_authorized_snapshot_and_server_step_mapping(event_kind):
    step_id = "tap_discovery" if event_kind == "browser.discovery_finished" else "open"
    events = [
        WorkflowEvent(
            "plan",
            "task",
            1,
            "node",
            1,
            "browser.plan_created",
            {"steps": [{"id": step_id, "keyword": "Given", "title": "Open"}]},
            datetime.now(UTC),
        ),
        WorkflowEvent(
            "action",
            "task",
            2,
            "node",
            1,
            event_kind,
            {"action_id": "logical-a1", "step_id": step_id, "observation_sequence": 3},
            datetime.now(UTC),
        ),
    ]
    snapshot = {"events": events}

    class Store:
        async def snapshot(self, scope, task):
            assert scope == VALIDATION_SCOPE and task == "task"
            return snapshot

    class Client:
        project_id = VALIDATION_SCOPE.project_id

        async def request(self, method, path, body):
            assert method == "POST" and path == "/executions/run/lca-draft"
            assert body["action_steps"] == {"observation:3": step_id}
            assert body["plan"] == events[0].payload["steps"]
            return {
                "source": {
                    "task_id": "task",
                    "execution_id": "run",
                    "bundle_id": "bundle",
                    "input_sha256": "a" * 64,
                },
                "complete": False,
                "issues": [],
            }

    adapter = BrowserArtifactAdapter(Store(), Client())

    async def project(scope, task, saved):
        assert saved is snapshot
        return {
            "status": "passed",
            "executionId": "run",
            "bundleId": "bundle",
            "inputSha256": "a" * 64,
        }

    adapter.project = project
    result = await adapter.lca_draft(VALIDATION_SCOPE, "task")
    assert result["source"]["task_id"] == "task"
