"""Workflow HTTP routes and public DTO shape."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tap.interfaces.http.app import create_app
from tap.modules.workflows.application.queries import WorkflowQueries
from tap.modules.workflows.domain.models import WorkflowEvent


def test_seven_workflow_operations_are_registered() -> None:
    paths = create_app().openapi()["paths"]
    base = "/api/v1/projects/{project_id}/workflows"
    assert "post" in paths[base]
    assert "get" in paths[base]
    assert "get" in paths[f"{base}/{{task_id}}"]
    assert "get" in paths[f"{base}/{{task_id}}/events"]
    assert "post" in paths[f"{base}/{{task_id}}/commands"]
    assert "get" in paths[f"{base}/{{task_id}}/nodes/{{node_id}}"]
    assert "get" in paths[f"{base}/{{task_id}}/nodes/{{node_id}}/logs"]


def test_browser_evidence_success_contract_lists_actual_file_types() -> None:
    paths = create_app().openapi()["paths"]
    operation = paths[
        "/api/v1/projects/{project_id}/workflows/{task_id}/browser-evidence/{evidence_id}"
    ]["get"]
    success = operation["responses"]["200"]["content"]
    assert set(success) == {"image/png", "application/zip", "application/json"}
    assert all(
        value["schema"] == {"type": "string", "format": "binary"} for value in success.values()
    )


def test_workflow_public_times_use_utc_for_mysql_naive_and_offset_values() -> None:
    from tap.modules.workflows.application.queries import _node, _task_view

    naive_utc = datetime(2026, 11, 6, 0, 0)
    offset_time = datetime(2026, 11, 6, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    node = {
        "node_id": "node-1",
        "parent_node_id": None,
        "role": "qae",
        "kind": "agent",
        "agent_revision_id": "rev-1",
        "status": "running",
        "attempt": 1,
        "plan_revision": 1,
        "runtime": "native",
        "created_at": offset_time,
    }
    task = _task_view(
        {
            "task_id": "task-1",
            "project_id": "project-1",
            "conversation_id": None,
            "status": "running",
            "plan_revision": 1,
            "request_json": {},
            "created_at": naive_utc,
            "recent_event": {"kind": "node.started", "occurred_at": offset_time},
        },
        (node,),
    )
    detail = _node(
        node,
        {},
        ({"node_id": "node-1", "attempt": 1, "status": "running", "created_at": naive_utc},),
    )
    assert task["createdAt"].isoformat() == "2026-11-06T00:00:00+00:00"
    assert task["recentEvent"]["occurredAt"].isoformat() == "2026-11-06T00:00:00+00:00"
    assert task["matchingRuns"][0]["createdAt"].isoformat() == "2026-11-06T00:00:00+00:00"
    assert detail["attempts"][0]["createdAt"].isoformat() == "2026-11-06T00:00:00+00:00"


def test_observed_child_unknown_is_a_node_read_status_only() -> None:
    from pydantic import ValidationError

    from tap.contracts.workflows import WorkflowMatchingRun, WorkflowNodeView, WorkflowTaskView

    node = WorkflowNodeView.model_validate(
        {
            "nodeId": "child",
            "parentNodeId": "root",
            "agentId": "relation-analysis",
            "agentRevisionId": None,
            "displayName": "Relation analysis",
            "kind": "agent",
            "status": "unknown",
            "attempt": 1,
            "planRevision": 1,
            "sourceRuntime": "relation_analysis",
            "attempts": [],
        }
    )
    run = WorkflowMatchingRun.model_validate(
        {
            "nodeId": "child",
            "agentId": "relation-analysis",
            "agentRevisionId": None,
            "attempt": 1,
            "status": "unknown",
            "createdAt": "2026-11-06T00:00:00Z",
        }
    )
    assert node.status == run.status == "unknown"
    with pytest.raises(ValidationError):
        WorkflowTaskView.model_validate(
            {
                "taskId": "task",
                "projectId": "project",
                "conversationId": None,
                "status": "unknown",
                "planRevision": 1,
                "lastSequence": 1,
                "origin": "observed",
                "availableActions": [],
                "displayName": "Agent",
                "goalSummary": "",
                "createdAt": "2026-11-06T00:00:00Z",
            }
        )


def test_runtime_mode_contract_exposes_default_false_workflow_capability() -> None:
    from tap.contracts.http import RuntimeMode

    mode = RuntimeMode(
        mode="validation", project_id="demo", actor_id="actor", identity_mode="validation"
    )
    assert mode.model_dump(by_alias=True)["workflowEnabled"] is False


async def _snapshot_view(
    *,
    origin="native",
    task_status="waiting_dependency",
    node_status="paused",
    native_controls_enabled=True,
):
    now = datetime(2026, 11, 6, tzinfo=UTC)

    class Store:
        async def snapshot(self, _scope, _task_id):
            return {
                "task_id": "task-1",
                "project_id": "tapper-demo",
                "conversation_id": None,
                "status": task_status,
                "plan_revision": 1,
                "last_sequence": 3,
                "created_at": now,
                "budget_limit": {"model_calls": 10},
                "budget_used": {},
                "request_json": {
                    "origin": origin,
                    "goal": "Write tests",
                    "root": {"agent_id": "orchestrator", "name": "Tapper"},
                    "candidates": [{"agent_id": "qae", "name": "Quality Engineer"}],
                    "resolved_resources": [
                        {
                            "source_id": "source-1",
                            "revision_id": "rev-1",
                            "source_content_hash": "sha256:abc",
                        }
                    ],
                },
                "nodes": (
                    {
                        "node_id": "root",
                        "parent_node_id": None,
                        "agent_revision_id": "root-rev",
                        "role": "root",
                        "kind": "agent",
                        "runtime": "native",
                        "status": "waiting_dependency",
                        "attempt": 1,
                        "plan_revision": 1,
                        "created_at": now,
                    },
                    {
                        "node_id": "child",
                        "parent_node_id": "root",
                        "agent_revision_id": "qae-rev",
                        "role": "qae",
                        "kind": "agent",
                        "runtime": "native",
                        "status": node_status,
                        "attempt": 2,
                        "plan_revision": 1,
                        "created_at": now,
                    },
                ),
                "attempts": (
                    {"node_id": "child", "attempt": 1, "status": "failed", "created_at": now},
                    {"node_id": "child", "attempt": 2, "status": node_status, "created_at": now},
                ),
                "edges": (),
                "commands": (),
                "artifacts": (
                    {
                        "artifact_id": "artifact-1",
                        "node_id": "child",
                        "attempt": 1,
                        "content_digest": "sha256:def",
                        "version_ref": "version-1",
                        "object_ref": "/private/secret/path",
                    },
                ),
                "events": (
                    WorkflowEvent(
                        "e1", "task-1", 1, "child", 1, "node.created", {"node_id": "child"}, now
                    ),
                    WorkflowEvent(
                        "e2", "task-1", 2, "child", 1, "node.created", {"node_id": "child"}, now
                    ),
                    WorkflowEvent(
                        "e3",
                        "task-1",
                        3,
                        "child",
                        2,
                        "action.settled",
                        {"result": {"output": "private"}},
                        now,
                    ),
                ),
            }

    return await WorkflowQueries(Store(), native_controls_enabled=native_controls_enabled).snapshot(
        None, "task-1"
    )


@pytest.mark.asyncio
async def test_snapshot_keeps_one_node_and_historical_attempts_without_private_refs() -> None:
    view = await _snapshot_view()
    assert len(view["nodes"]) == 2
    child = view["nodes"][1]
    assert child["agentId"] == "qae"
    assert child["displayName"] == "Quality Engineer"
    assert [item["attempt"] for item in child["attempts"]] == [1, 2]
    assert view["artifacts"][0]["contentDigest"] == "sha256:def"
    assert "/private/secret/path" not in str(view)
    assert "private" not in str(view)


@pytest.mark.asyncio
async def test_paused_child_can_resume_while_task_waits_and_observed_has_no_controls() -> None:
    native = await _snapshot_view()
    observed = await _snapshot_view(origin="observed")
    assert "resume" in native["availableActions"]
    assert observed["availableActions"] == []


@pytest.mark.asyncio
async def test_flag_off_reads_both_origins_without_offering_unavailable_controls() -> None:
    native = await _snapshot_view(native_controls_enabled=False)
    observed = await _snapshot_view(origin="observed", native_controls_enabled=False)
    assert native["origin"] == "native"
    assert observed["origin"] == "observed"
    assert native["availableActions"] == observed["availableActions"] == []


@pytest.mark.asyncio
async def test_flag_off_command_rejects_before_store_receipt() -> None:
    from types import SimpleNamespace

    from tap.contracts.workflows import WorkflowCommandRequest
    from tap.interfaces.http.workflow_service import WorkflowControlUnavailable, WorkflowHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE

    class Store:
        async def submit_command(self, *_args):
            pytest.fail("flag-off control must not reach the store")

    service = WorkflowHttpService(SimpleNamespace(workflow_enabled=False), Store(), None, None)
    with pytest.raises(WorkflowControlUnavailable):
        await service.command(
            VALIDATION_SCOPE,
            "native-task",
            WorkflowCommandRequest(kind="pause", expectedPlanRevision=1),
            "cmd-flag-off",
        )


@pytest.mark.asyncio
async def test_failed_child_can_retry_while_parent_waits() -> None:
    native = await _snapshot_view(node_status="failed")
    assert "retry" in native["availableActions"]


@pytest.mark.asyncio
async def test_filtered_agent_list_has_typed_parallel_run_summaries() -> None:
    now = datetime(2026, 11, 6, tzinfo=UTC)

    class Store:
        async def list_tasks(self, _scope, limit, cursor, agent_id):
            assert (limit, cursor, agent_id) == (20, None, "qae")
            return (
                (
                    {
                        "task_id": "task-1",
                        "project_id": "tapper-demo",
                        "conversation_id": None,
                        "status": "running",
                        "plan_revision": 2,
                        "last_sequence": 5,
                        "created_at": now,
                        "request_json": {"origin": "native", "goal": "Review"},
                        "recent_event": {"kind": "node.finished", "occurred_at": now},
                        "nodes": (
                            {
                                "node_id": "n1",
                                "role": "qae",
                                "kind": "agent",
                                "agent_revision_id": "r1",
                                "status": "succeeded",
                                "attempt": 1,
                                "created_at": now,
                            },
                            {
                                "node_id": "n2",
                                "role": "qae",
                                "kind": "agent",
                                "agent_revision_id": "r1",
                                "status": "running",
                                "attempt": 2,
                                "created_at": now,
                            },
                            {
                                "node_id": "n3",
                                "role": "developer",
                                "kind": "agent",
                                "agent_revision_id": "r2",
                                "status": "paused",
                                "attempt": 1,
                                "created_at": now,
                            },
                        ),
                    },
                ),
                None,
            )

    class Redactor:
        async def redact_text(self, text):
            return text

    page = await WorkflowQueries(Store(), redactor=Redactor()).list(None, 20, None, "qae")
    assert page["items"][0]["goalSummary"] == "Review"
    assert page["items"][0]["recentEvent"] == {"kind": "node.finished", "occurredAt": now}
    runs = page["items"][0]["matchingRuns"]
    assert "resume" in page["items"][0]["availableActions"]
    assert [(run["nodeId"], run["agentId"], run["attempt"]) for run in runs] == [
        ("n1", "qae", 1),
        ("n2", "qae", 2),
    ]


@pytest.mark.asyncio
async def test_stale_command_returns_conflict_after_durable_receipt() -> None:
    from tap.contracts.workflows import WorkflowCommandRequest
    from tap.interfaces.http.workflow_service import WorkflowHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE

    class Store:
        async def command(self, *_args):
            return None

        async def submit_command(self, *_args):
            return {
                "status": "stale",
                "reason": "plan revision changed",
                "applied_plan_revision": None,
            }

    class Queries:
        async def snapshot(self, *_args):
            return {"origin": "native", "lastSequence": 3}

    service = WorkflowHttpService(None, Store(), None, Queries())
    with pytest.raises(ValueError, match="stale"):
        await service.command(
            VALIDATION_SCOPE,
            "task-1",
            WorkflowCommandRequest(kind="pause", expectedPlanRevision=1),
            "cmd-1",
        )


@pytest.mark.asyncio
async def test_standalone_replay_uses_frozen_task_before_mutable_catalogs() -> None:
    from types import SimpleNamespace

    from tap.contracts.workflows import WorkflowCreateRequest
    from tap.interfaces.http.workflow_service import WorkflowHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE

    class Admission:
        async def existing_standalone(self, _scope, request, key):
            assert request["goal"] == "Review"
            assert key == "create-1"
            return SimpleNamespace(task_id="existing-task")

    class Queries:
        async def snapshot(self, _scope, task_id):
            assert task_id == "existing-task"
            return {"taskId": task_id}

    service = WorkflowHttpService(
        SimpleNamespace(asset_catalog=None, model_catalog=None), None, Admission(), Queries()
    )
    result = await service.create(
        VALIDATION_SCOPE,
        WorkflowCreateRequest(goal="Review", modelAlias="qwen-plus"),
        "create-1",
    )
    assert result == {"taskId": "existing-task"}


def test_public_node_summary_requires_matching_finished_attempt() -> None:
    from tap.modules.workflows.application.queries import attach_node_summaries

    nodes = [{"nodeId": "child", "attempt": 2}]
    events = [
        {"kind": "node.finished", "nodeId": "child", "attempt": 1, "payload": {"summary": "old"}},
        {"kind": "activity", "nodeId": "child", "attempt": 2, "payload": {}},
    ]
    assert attach_node_summaries(nodes, events)[0]["publicSummary"] is None
    events.append(
        {"kind": "node.finished", "nodeId": "child", "attempt": 2, "payload": {"summary": "safe"}}
    )
    assert attach_node_summaries(nodes, events)[0]["publicSummary"] == "safe"


def test_real_tool_step_projection_exposes_only_safe_execution_fields() -> None:
    from tap.modules.workflows.application.queries import project_event

    now = datetime(2026, 11, 6, tzinfo=UTC)
    event = WorkflowEvent(
        "tool-event",
        "task-1",
        4,
        "node-1",
        2,
        "node.tool_finished",
        {
            "node_id": "node-1",
            "attempt": 2,
            "tool_id": "knowledge.search",
            "outcome": "succeeded",
            "arguments": {"query": "private"},
            "private_result": "secret",
            "action_id": "reservation",
        },
        now,
    )
    projected = project_event(event)
    assert projected["payload"] == {
        "node_id": "node-1",
        "attempt": 2,
        "tool_id": "knowledge.search",
        "outcome": "succeeded",
    }
    assert "private" not in str(projected)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_body", [True, False])
async def test_concurrent_standalone_create_reconciles_only_same_public_request(same_body):
    import asyncio
    from types import SimpleNamespace

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowStatus

    class RacingStore:
        def __init__(self):
            self.lookups = 0
            self.ready = asyncio.Event()
            self.winner = None
            self.winner_request = None
            self.insert_requests = []
            self.lock = asyncio.Lock()

        async def task_by_key(self, _scope, _key):
            self.lookups += 1
            if self.lookups <= 2:
                if self.lookups == 2:
                    self.ready.set()
                await self.ready.wait()
                return None
            return self.winner

        async def task_request(self, _scope, _task_id):
            return self.winner_request

        async def create_task(self, _scope, request, _key):
            async with self.lock:
                self.insert_requests.append(request)
                if self.winner is not None:
                    raise ValueError("idempotency key payload conflict")
                self.winner_request = request
                self.winner = TaskSnapshot(
                    "winner",
                    VALIDATION_SCOPE.project_id,
                    None,
                    1,
                    WorkflowStatus.QUEUED,
                    1,
                )
                return self.winner

    store = RacingStore()
    admission = WorkflowAdmissionCoordinator(SimpleNamespace(scope=VALIDATION_SCOPE), None, store)
    counter = 0

    def different_limits(_now):
        nonlocal counter
        counter += 1
        return {"deadline_at": f"deadline-{counter}", "budget_limit": {"model_calls": counter}}

    admission._limits = different_limits
    first = {
        "origin": "native",
        "goal": "Review",
        "root": {"revision_id": "root-v1"},
        "public_request": {"goal": "Review"},
    }
    second = {
        "origin": "native",
        "goal": "Review",
        "root": {"revision_id": "root-v2"},
        "public_request": {"goal": "Review" if same_body else "Changed"},
    }
    results = await asyncio.gather(
        admission.create_standalone(VALIDATION_SCOPE, first, "same-key"),
        admission.create_standalone(VALIDATION_SCOPE, second, "same-key"),
        return_exceptions=True,
    )
    successes = [item for item in results if isinstance(item, TaskSnapshot)]
    conflicts = [item for item in results if isinstance(item, ValueError)]
    assert all(item.task_id == "winner" for item in successes)
    assert len(successes) == (2 if same_body else 1)
    assert len(conflicts) == (0 if same_body else 1)
    assert store.winner_request == store.insert_requests[0]
    assert store.winner_request["deadline_at"] in ("deadline-1", "deadline-2")
    assert store.winner_request["root"] in (first["root"], second["root"])
    assert len(store.insert_requests) == 2
