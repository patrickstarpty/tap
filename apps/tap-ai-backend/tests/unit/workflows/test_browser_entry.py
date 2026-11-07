"""Browser Chat input remains frozen through admission and control projection."""

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import _input_json
from tap.modules.chat.domain.conversations import (
    ConversationTurn,
    FrozenBrowserIntent,
    TurnInput,
    TurnInputSnapshot,
)
from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
from tap.modules.workflows.application.queries import _actions
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowStatus
from tap.modules.workflows.ports.catalog import AgentSnapshot


def _browser() -> FrozenBrowserIntent:
    data = {"notes": "Use customer Ada"}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return FrozenBrowserIntent(
        start_url="https://example.org/quotes",
        expected_result="Find Ada's quote",
        allowed_origins=("https://example.org",),
        input_data_json=canonical,
        input_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def test_browser_turn_input_is_durable_and_legacy_material_is_unchanged() -> None:
    base = dict(
        message="Find a quote",
        actor_id="actor",
        identity_mode="validation",
        model_alias="qwen-plus",
    )
    legacy = TurnInput(**base)
    root = AgentSnapshot("orchestrator", "orchestrator-v1", "Tapper", "", "", (), "qwen-plus", ())
    browser = TurnInput(**base, browser=_browser(), workflow_root=root)
    assert "browser" not in legacy.material(project_id="p", turn_id="t")
    assert (
        browser.material(project_id="p", turn_id="t")["browser"]["input_sha256"]
        == _browser().input_sha256
    )
    assert FrozenBrowserIntent(**_input_json(browser)["browser"]) == _browser()


def test_browser_actions_are_cancel_or_frozen_replay_only() -> None:
    assert _actions("running", "native", kind="browser") == ["cancel"]
    assert _actions("canceling", "native", kind="browser") == []
    assert _actions("awaiting_input", "native", kind="browser") == []
    assert _actions("succeeded", "native", kind="browser", replay_ready=False) == []
    assert _actions("failed", "native", kind="browser", replay_ready=True) == ["replay"]


@pytest.mark.asyncio
async def test_chat_admission_preserves_browser_intent_and_positive_cost() -> None:
    root = AgentSnapshot("orchestrator", "orchestrator-v1", "Tapper", "", "", (), "qwen-plus", ())
    sdet = AgentSnapshot("sdet", "sdet-v1", "SDET", "", "", (), "qwen-plus", ())
    value = TurnInput(
        message="Find Ada's quote",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        workflow_root=root,
        workflow_agents=(sdet,),
        delegate_ids=("sdet",),
        browser=_browser(),
    )
    turn = ConversationTurn(
        "turn-browser",
        "request-browser",
        1,
        "queued",
        TurnInputSnapshot.create(
            snapshot_id="snapshot-browser",
            project_id=VALIDATION_SCOPE.project_id,
            turn_id="turn-browser",
            value=value,
            now=datetime.now(UTC),
        ),
    )

    class Store:
        request = None

        async def task_by_key(self, _scope, _key):
            return None

        async def create_task(self, scope, request, _key):
            self.request = request
            return TaskSnapshot(
                "task-browser", scope.project_id, "chat-browser", 1, WorkflowStatus.QUEUED, 1
            )

        async def task_request(self, _scope, _task_id):
            return self.request

        async def activate_task(self, _scope, _task_id, _turn_id):
            return None

    class Chat:
        async def link_task(self, _conversation_id, _turn_id, _task_id):
            return False

    store = Store()
    coordinator = WorkflowAdmissionCoordinator(
        SimpleNamespace(scope=VALIDATION_SCOPE), Chat(), store, max_cost_micros=1000
    )
    await coordinator._ensure_task("chat-browser", turn)
    assert store.request["kind"] == "browser"
    assert store.request["browser"]["input_data"] == {"notes": "Use customer Ada"}
    assert store.request["browser"]["input_sha256"] == _browser().input_sha256
    assert store.request["budget_limit"]["cost_micros"] == 1000


def test_browser_request_defaults_expected_result_to_goal_and_rejects_bad_url() -> None:
    from pydantic import ValidationError

    from tap.contracts.http import ConversationCreateRequest

    body = ConversationCreateRequest.model_validate(
        {
            "message": "Find Ada's quote",
            "modelAlias": "qwen-plus",
            "browser": {"startUrl": "https://example.org/quotes", "inputNotes": "Customer Ada"},
        }
    )
    assert body.browser is not None
    assert body.browser.start_url == "https://example.org/quotes"
    upper_host = ConversationCreateRequest.model_validate(
        {
            "message": "Find Ada's quote",
            "modelAlias": "qwen-plus",
            "browser": {"startUrl": "https://EXAMPLE.org/quotes"},
        }
    )
    assert upper_host.browser.start_url == "https://example.org/quotes"
    assert (
        ConversationCreateRequest.model_validate(
            {
                "message": "Continue",
                "modelAlias": "qwen-plus",
                "browser": {"startUrl": "https://example.org", "sourceTaskId": "task-previous"},
            }
        ).browser.source_task_id
        == "task-previous"
    )
    with pytest.raises(ValidationError):
        ConversationCreateRequest.model_validate(
            {
                "message": "Find",
                "modelAlias": "qwen-plus",
                "browser": {"startUrl": "file:///etc/passwd"},
            }
        )


@pytest.mark.asyncio
async def test_browser_service_rejects_pause_before_command_persistence() -> None:
    from tap.contracts.workflows import WorkflowCommandRequest
    from tap.interfaces.http.workflow_service import WorkflowControlUnavailable, WorkflowHttpService

    class Queries:
        async def snapshot(self, _scope, _task_id):
            return {
                "origin": "native",
                "taskKind": "browser",
                "status": "running",
                "availableActions": ["cancel"],
            }

    class Store:
        async def command(self, *_args):
            raise AssertionError("browser pause reached the store")

    service = WorkflowHttpService(SimpleNamespace(workflow_enabled=True), Store(), None, Queries())
    with pytest.raises(WorkflowControlUnavailable):
        await service.command(
            VALIDATION_SCOPE,
            "task-browser",
            WorkflowCommandRequest(kind="pause", expected_plan_revision=1),
            "pause-key",
        )


def test_browser_replay_requires_a_recorded_bundle_and_execution() -> None:
    from tap.modules.workflows.adapters.mysql import _frozen_browser_replay
    from tap.modules.workflows.domain.models import WorkflowEvent

    browser = _browser()
    request = {
        "kind": "browser",
        "browser": {
            "start_url": browser.start_url,
            "expected_result": browser.expected_result,
            "allowed_origins": list(browser.allowed_origins),
            "input_data": json.loads(browser.input_data_json),
            "input_sha256": browser.input_sha256,
        },
    }
    event = WorkflowEvent(
        "event",
        "task-browser",
        1,
        "node",
        1,
        "browser.coding_result",
        {"bundle_ref": "bundle-1", "execution_ref": "execution-1"},
        datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="frozen bundle"):
        _frozen_browser_replay(request, ())
    replay = _frozen_browser_replay(request, (event,))
    assert replay["browser"]["replay_execution_id"] == "execution-1"
    assert replay["browser"]["bundle_ref"] == "bundle-1"
    assert replay["browser"]["input_sha256"] == browser.input_sha256


@pytest.mark.asyncio
async def test_browser_replay_service_requires_terminal_action() -> None:
    from tap.interfaces.http.workflow_service import WorkflowControlUnavailable, WorkflowHttpService

    class Queries:
        async def snapshot(self, _scope, _task_id):
            return {
                "origin": "native",
                "taskKind": "browser",
                "status": "failed",
                "availableActions": [],
            }

    class Store:
        async def rerun_task(self, *_args):
            raise AssertionError("replay reached store without bundle")

    service = WorkflowHttpService(SimpleNamespace(workflow_enabled=True), Store(), None, Queries())
    with pytest.raises(WorkflowControlUnavailable):
        await service.replay(VALIDATION_SCOPE, "task-browser", "replay-key")


@pytest.mark.asyncio
async def test_browser_replay_rejects_a_different_configured_project() -> None:
    from tap.interfaces.http.workflow_service import WorkflowControlUnavailable, WorkflowHttpService

    class Queries:
        async def snapshot(self, _scope, _task_id):
            raise AssertionError("different project reached the task store")

    service = WorkflowHttpService(
        SimpleNamespace(
            browser_enabled=True,
            browser_client=SimpleNamespace(project_id="different-project"),
        ),
        None,
        None,
        Queries(),
    )
    with pytest.raises(WorkflowControlUnavailable):
        await service.replay(VALIDATION_SCOPE, "task-browser", "replay-key")
