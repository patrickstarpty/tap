"""Durable native workflow ownership against an owned MySQL database."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tests.owned_mysql import owned_project_database_url


async def _trusted_test_authorize(scope, task_id):
    """Direct native-runtime fixtures explicitly authorize their owned task."""
    assert scope == VALIDATION_SCOPE and task_id
    return True


@pytest.mark.asyncio
async def test_browser_stop_proof_rejects_foreign_lease_and_stale_attempt_after_settlement(
    owned_project_mysql,
):
    """A settled model action must not mask an invalid browser stop proof."""
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import workflow_asset_seed
    from tap.modules.workflows.adapters.native_state import NativeStateStore

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        seed = workflow_asset_seed(VALIDATION_SCOPE)
        await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(seed)
        store = MysqlWorkflowStore(sessions)
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "kind": "browser",
                "goal": "Inspect owned page",
                "root": {
                    "agent_id": seed.agents[0].asset_id,
                    "revision_id": seed.agents[0].revision_id,
                    "description": seed.agents[0].description,
                    "instruction": seed.agents[0].system_instruction,
                },
                "budget_limit": {"model_calls": 2, "cost_micros": 10_000},
            },
            "browser-proof-after-settlement",
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker")
        state = NativeStateStore(sessions)
        node_id = await state.ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, node_id)
        assert (
            await store.reserve_action(
                VALIDATION_SCOPE,
                task.task_id,
                token,
                "settled-model",
                "model",
                node_id=node_id,
                attempt=1,
            )
            == "reserved"
        )
        await store.settle_action(
            VALIDATION_SCOPE,
            task.task_id,
            token,
            "settled-model",
            {"status": "completed", "cost_usd": 0.001},
            node_id=node_id,
            attempt=1,
        )
        for event_id, attempt, proof_token in (
            ("foreign-proof", 1, "foreign-lease"),
            ("stale-proof", 2, token),
        ):
            await store.append_event(
                VALIDATION_SCOPE,
                task.task_id,
                token,
                event_id,
                "browser.stop_confirmed",
                {
                    "node_id": node_id,
                    "attempt": attempt,
                    "execution_token": proof_token,
                    "stop_confirmed": True,
                },
            )
            assert not await store.stop_evidence(VALIDATION_SCOPE, task.task_id, token, node_id)
        await store.append_event(
            VALIDATION_SCOPE,
            task.task_id,
            token,
            "current-proof",
            "browser.stop_confirmed",
            {
                "node_id": node_id,
                "attempt": 1,
                "execution_token": token,
                "stop_confirmed": True,
            },
        )
        assert await store.stop_evidence(VALIDATION_SCOPE, task.task_id, token, node_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chat_origin_task_cannot_be_claimed_before_link_activation(owned_project_mysql):
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        store = MysqlWorkflowStore(async_sessionmaker(engine, expire_on_commit=False))
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "conversation_id": "conversation-1", "origin_turn_id": "turn-1"},
            "origin:turn-1",
        )
        assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker") is None
        await store.activate_task(VALIDATION_SCOPE, task.task_id, "turn-1")
        assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workflow_turn_is_never_claimed_by_legacy_worker_and_cancel_waits_for_ack(
    owned_project_mysql,
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update

    from tap.modules.chat.adapters.mysql import chat_turn
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        bridge = MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE)
        root = AgentSnapshot("orchestrator", "root-v1", "Tapper", "", "help", (), None, ())
        await conversations.create(
            "conversation-1",
            "turn-1",
            "request-1",
            TurnInput(
                "Review", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus", workflow_root=root
            ),
        )
        assert not await repository.claim_queued(limit=1)
        async with sessions.begin() as session:
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-1")
                .values(
                    state="running",
                    processing_lease_expires_at=datetime.now(UTC).replace(tzinfo=None)
                    - timedelta(seconds=1),
                )
            )
        assert not await repository.claim_queued(limit=1)
        await bridge.request_cancel("conversation-1", "turn-1")
        assert not await bridge.authorize_dispatch("conversation-1", "turn-1", "task-1")
        async with sessions() as session:
            row = (
                (await session.execute(select(chat_turn).where(chat_turn.c.turn_id == "turn-1")))
                .mappings()
                .one()
            )
            assert row["state"] == "running"
            assert row["cancel_requested_at"] is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admission_repairs_created_task_and_links_one_origin_turn(owned_project_mysql):
    from sqlalchemy import select

    from tap.modules.chat.adapters.mysql import chat_turn
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
        )
        bridge = MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE)
        store = MysqlWorkflowStore(sessions)
        admission = WorkflowAdmissionCoordinator(
            conversations,
            bridge,
            store,
            max_parallel_agents=2,
        )
        root = AgentSnapshot("orchestrator", "root-v1", "Tapper", "", "help", (), None, ())
        frozen = TurnInput(
            "Review", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus", workflow_root=root
        )
        turn = await admission.create("conversation-1", "turn-1", "request-1", frozen)
        assert turn.turn_id == "turn-1"
        await admission.repair(limit=10)
        async with sessions() as session:
            tasks = (await session.execute(select(workflow_task))).mappings().all()
            linked = (
                await session.execute(
                    select(chat_turn.c.workflow_task_id).where(chat_turn.c.turn_id == "turn-1")
                )
            ).scalar_one()
            assert len(tasks) == 1
            assert tasks[0]["task_id"] == linked
            assert tasks[0]["admission_ready"]
            assert tasks[0]["request_json"]["max_parallel_agents"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_crash_after_task_create_then_cancel_before_link_never_dispatches_model(
    owned_project_mysql,
):
    from sqlalchemy import select

    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.adapters.schema import workflow_command, workflow_task
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    class RecordingWakeups:
        def __init__(self):
            self.notified = []

        async def notify(self, scope, task_id, sequence):
            self.notified.append((task_id, sequence))

    class CrashLink(MysqlWorkflowChatBridge):
        async def link_task(self, conversation_id, turn_id, task_id):
            raise RuntimeError("crash after task creation")

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
        )
        store = MysqlWorkflowStore(sessions)
        root = AgentSnapshot("orchestrator", "root-v1", "Tapper", "", "help", (), None, ())
        frozen = TurnInput(
            "Review", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus", workflow_root=root
        )
        wakeups = RecordingWakeups()
        broken = WorkflowAdmissionCoordinator(
            conversations,
            CrashLink(sessions, scope=VALIDATION_SCOPE),
            store,
            wakeups=wakeups,
        )
        with pytest.raises(RuntimeError, match="crash after task creation"):
            await broken.create("conversation-1", "turn-1", "request-1", frozen)
        assert wakeups.notified == []
        bridge = MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE)
        await bridge.request_cancel("conversation-1", "turn-1")
        healthy = WorkflowAdmissionCoordinator(conversations, bridge, store, wakeups=wakeups)
        await healthy.repair(limit=10)
        async with sessions() as session:
            task = (await session.execute(select(workflow_task))).mappings().one()
            commands = (await session.execute(select(workflow_command))).mappings().all()
        assert task["admission_ready"] is False
        assert len(commands) == 1 and commands[0]["kind"] == "cancel"
        assert wakeups.notified
        assert await store.claim_task(VALIDATION_SCOPE, task["task_id"], "worker") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_parallel_model_reservations_share_one_atomic_task_budget(owned_project_mysql):
    import asyncio

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "budget_limit": {"model_calls": 1}},
            "budget-race",
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker")
        assert token
        results = await asyncio.gather(
            store.reserve_action(VALIDATION_SCOPE, task.task_id, token, "model-a", "model"),
            store.reserve_action(VALIDATION_SCOPE, task.task_id, token, "model-b", "model"),
            return_exceptions=True,
        )
        assert sum(value == "reserved" for value in results) == 1
        assert sum(isinstance(value, ValueError) for value in results) == 1
        assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["budget_used"][
            "model_calls"
        ] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delegation_call_id_is_durable_and_wait_releases_task_owner(owned_project_mysql):
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.domain.delegation import DelegationRequest

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        state = NativeStateStore(sessions)
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "candidates": [{"agent_id": "ba", "revision_id": None}]},
            "delegation-identity",
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker")
        root = await state.ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, root)
        request = DelegationRequest("ba", "Review rules", (), "summary", ())
        first = await state.delegate(VALIDATION_SCOPE, task.task_id, token, root, request, "call-a")
        assert first == await state.delegate(
            VALIDATION_SCOPE, task.task_id, token, root, request, "call-a"
        )
        with pytest.raises(ValueError, match="call id conflict"):
            await state.delegate(
                VALIDATION_SCOPE,
                task.task_id,
                token,
                root,
                DelegationRequest("ba", "Different objective", (), "summary", ()),
                "call-a",
            )
        with pytest.raises(ValueError, match="authorized"):
            await state.delegate(
                VALIDATION_SCOPE,
                task.task_id,
                token,
                root,
                DelegationRequest("ba", "Review rules", ("secret-source",), "summary", ()),
                "call-unauthorized",
            )
        assert await store.ready_nodes(VALIDATION_SCOPE, task.task_id) == (first,)
        await state.release_task(VALIDATION_SCOPE, task.task_id, token, "waiting_dependency")
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "waiting_dependency"
        assert snapshot["nodes"][0]["node_id"] == root
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_waiting_task_reclaims_children_then_resumes_root_without_retry(owned_project_mysql):
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.delegation import DelegationRequest

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state = MysqlWorkflowStore(sessions), NativeStateStore(sessions)
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "candidates": [{"agent_id": "ba", "revision_id": None}]},
            "wait-and-resume",
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
        root = await state.ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, root)
        child = await state.delegate(
            VALIDATION_SCOPE,
            task.task_id,
            token,
            root,
            DelegationRequest("ba", "Review", (), "summary", ()),
            "call-a",
        )
        await state.wait_node(VALIDATION_SCOPE, task.task_id, token, root)
        await state.release_task(VALIDATION_SCOPE, task.task_id, token, "waiting_dependency")
        next_token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-b")
        assert next_token and next_token != token
        assert await store.ready_nodes(VALIDATION_SCOPE, task.task_id) == (child,)
        await state.start_node(VALIDATION_SCOPE, task.task_id, next_token, child)
        full_result = "Detailed specialist evidence. " * 200
        await state.finish_node(VALIDATION_SCOPE, task.task_id, next_token, child, full_result)
        assert await state.unblock_parents(VALIDATION_SCOPE, task.task_id, next_token) == (root,)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        finished = next(
            event
            for event in snapshot["events"]
            if event.kind == "node.finished" and event.payload["node_id"] == child
        )
        assert finished.payload["private_result"] == full_result
        assert full_result not in finished.payload["summary"]
        parent_results = NativeWorkflowRuntime._child_results(snapshot["events"], root)
        assert len(parent_results[0]["summary"]) < 600
        assert full_result not in parent_results[0]["summary"]
        assert next(node for node in snapshot["nodes"] if node["node_id"] == root)["attempt"] == 1
        assert await store.ready_nodes(VALIDATION_SCOPE, task.task_id) == (root,)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_settled_model_result_replays_before_checkpoint_without_second_call(
    owned_project_mysql,
):
    from dataclasses import replace

    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelRequest,
        ModelResult,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway

    class Gateway:
        def __init__(self):
            self.calls = 0

        async def chat(self, request):
            self.calls += 1
            usage = ModelUsage(3, 4)
            return ModelResult(
                output='{"answer":"Persisted"}',
                actual_model="fixture",
                usage=usage,
                actual_provider="fixture",
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, gateway = MysqlWorkflowStore(sessions), Gateway()
        task = await store.create_task(VALIDATION_SCOPE, {"goal": "Answer"}, "settled-replay")
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker")
        from tap.modules.workflows.adapters.native_state import NativeStateStore

        root = await NativeStateStore(sessions).ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await NativeStateStore(sessions).start_node(VALIDATION_SCOPE, task.task_id, token, root)
        graph_run_id, _ = await store.graph_lineage(VALIDATION_SCOPE, task.task_id, root, 1)
        request = ModelRequest(
            VALIDATION_SCOPE,
            "qwen-plus",
            ModelOperation.CHAT,
            "Answer",
            text_digest("Answer"),
            "Question",
            30,
            f"{task.task_id}:{root}:graph:{graph_run_id}:model:1",
        )
        first = await JournaledModelGateway(gateway, store, task.task_id, token, root, 1).chat(
            request
        )
        # A fresh gateway after settlement replays the same graph action before checkpoint.
        second = await JournaledModelGateway(gateway, store, task.task_id, token, root, 1).chat(
            request
        )
        assert second.output == first.output
        assert gateway.calls == 1
        assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["budget_used"][
            "model_calls"
        ] == 1
        changed = replace(request, context="A new instruction")
        await JournaledModelGateway(gateway, store, task.task_id, token, root, 1).chat(changed)
        assert gateway.calls == 2
        assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["budget_used"][
            "model_calls"
        ] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_waiting_task_with_no_ready_node_is_not_reclaimed(owned_project_mysql):
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.domain.delegation import DelegationRequest

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state = MysqlWorkflowStore(sessions), NativeStateStore(sessions)
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "candidates": [{"agent_id": "ba", "revision_id": None}]},
            "waiting-no-ready",
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
        root = await state.ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, root)
        child = await state.delegate(
            VALIDATION_SCOPE,
            task.task_id,
            token,
            root,
            DelegationRequest("ba", "Review", (), "summary", ()),
            "call-a",
        )
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, child)
        await state.wait_node(VALIDATION_SCOPE, task.task_id, token, child)
        await state.wait_node(VALIDATION_SCOPE, task.task_id, token, root)
        await state.release_task(VALIDATION_SCOPE, task.task_id, token, "waiting_dependency")
        assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-b") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_runtime_runs_two_children_at_a_real_async_barrier(owned_project_mysql):
    import asyncio
    import json

    from langgraph.checkpoint.memory import MemorySaver

    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelToolCall,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime

    class BarrierGateway:
        def __init__(self):
            self.children = 0
            self.barrier = asyncio.Event()
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            if request.context == "Review rules and tests":
                root_round = sum(call.context == request.context for call in self.calls)
                if root_round == 1:
                    calls = tuple(
                        ModelToolCall(
                            f"call-{agent}",
                            "workflow_delegate",
                            json.dumps(
                                {
                                    "agentId": agent,
                                    "objective": f"Review {agent}",
                                    "inputRefs": [],
                                    "expectedOutput": "brief findings",
                                    "dependsOn": [],
                                }
                            ),
                        )
                        for agent in ("ba", "qae")
                    )
                    output = json.dumps(
                        {
                            "route": {
                                "kind": "native",
                                "reason": "review",
                                "confidence": 0.9,
                                "required_capabilities": [],
                            }
                        }
                    )
                else:
                    calls, output = (), "Combined findings"
            else:
                self.children += 1
                if self.children == 2:
                    self.barrier.set()
                await asyncio.wait_for(self.barrier.wait(), 5)
                calls, output = (), f"Findings: {request.context}"
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=output,
                actual_model="fixture",
                usage=usage,
                actual_provider="fixture",
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
                tool_calls=calls,
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        gateway = BarrierGateway()
        checkpoint = MemorySaver()
        runtime = NativeWorkflowRuntime(
            store,
            NativeStateStore(sessions),
            gateway,
            checkpointer_factory=lambda scope, task_id, token: checkpoint,
            action_gateway_factory=lambda scope, task_id, token, node_id, attempt, authorize: (
                JournaledModelGateway(gateway, store, task_id, token, node_id, attempt, authorize)
            ),
            max_parallel_agents=2,
            owner="test-worker",
            standalone_authorize=_trusted_test_authorize,
        )
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Review rules and tests",
                "model_alias": "qwen-plus",
                "candidates": [
                    {"agent_id": "ba", "revision_id": None},
                    {"agent_id": "qae", "revision_id": None},
                ],
                "budget_limit": {"model_calls": 4},
                "max_attempts": 3,
            },
            "parallel-barrier",
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))[
            "status"
        ] == "waiting_dependency"
        await asyncio.wait_for(runtime.start(VALIDATION_SCOPE, task.task_id), 8)
        assert gateway.children == 2
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "succeeded"
        assert snapshot["budget_used"]["model_calls"] == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_runtime_respects_frozen_parallel_limit_after_worker_rebuild(
    owned_project_mysql,
):
    import asyncio
    import json

    from langgraph.checkpoint.memory import MemorySaver

    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelToolCall,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime

    class BarrierGateway:
        def __init__(self):
            self.children = 0
            self.barrier = asyncio.Event()
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            if request.context == "Review rules and tests":
                root_round = sum(call.context == request.context for call in self.calls)
                if root_round == 1:
                    calls = tuple(
                        ModelToolCall(
                            f"call-{agent}",
                            "workflow_delegate",
                            json.dumps(
                                {
                                    "agentId": agent,
                                    "objective": f"Review {agent}",
                                    "inputRefs": [],
                                    "expectedOutput": "brief findings",
                                    "dependsOn": [],
                                }
                            ),
                        )
                        for agent in ("ba", "qae", "req")
                    )
                    output = json.dumps(
                        {
                            "route": {
                                "kind": "native",
                                "reason": "review",
                                "confidence": 0.9,
                                "required_capabilities": [],
                            }
                        }
                    )
                else:
                    calls, output = (), "Combined findings"
            else:
                self.children += 1
                if self.children == 2:
                    self.barrier.set()
                await asyncio.wait_for(self.barrier.wait(), 5)
                calls, output = (), f"Findings: {request.context}"
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=output,
                actual_model="fixture",
                usage=usage,
                actual_provider="fixture",
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
                tool_calls=calls,
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        gateway = BarrierGateway()
        checkpoint = MemorySaver()
        runtime = NativeWorkflowRuntime(
            store,
            NativeStateStore(sessions),
            gateway,
            checkpointer_factory=lambda scope, task_id, token: checkpoint,
            action_gateway_factory=lambda scope, task_id, token, node_id, attempt, authorize: (
                JournaledModelGateway(gateway, store, task_id, token, node_id, attempt, authorize)
            ),
            max_parallel_agents=3,
            owner="test-worker",
            standalone_authorize=_trusted_test_authorize,
        )
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Review rules and tests",
                "model_alias": "qwen-plus",
                "candidates": [
                    {"agent_id": "ba", "revision_id": None},
                    {"agent_id": "qae", "revision_id": None},
                    {"agent_id": "req", "revision_id": None},
                ],
                "budget_limit": {"model_calls": 5},
                "max_attempts": 3,
                "max_parallel_agents": 2,
            },
            "parallel-barrier",
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))[
            "status"
        ] == "waiting_dependency"
        await asyncio.wait_for(runtime.start(VALIDATION_SCOPE, task.task_id), 8)
        assert gateway.children == 2
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        assert gateway.children == 3
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "succeeded"
        assert snapshot["budget_used"]["model_calls"] == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_committed_workflow_facts_project_one_terminal_chat_answer(owned_project_mysql):
    from sqlalchemy import select

    from tap.modules.chat.adapters.mysql import chat_event, chat_turn
    from tap.modules.chat.adapters.mysql_conversations import (
        MysqlConversationRepository,
        turn_answer_evidence_snapshot,
    )
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.application.dispatch import WorkflowProjection
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        bridge, store = (
            MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE),
            MysqlWorkflowStore(sessions),
        )
        admission = WorkflowAdmissionCoordinator(conversations, bridge, store)
        root_config = AgentSnapshot("orchestrator", None, "Tapper", "", "help", (), None, ())
        turn = await admission.create(
            "conversation-1",
            "turn-1",
            "request-1",
            TurnInput(
                "Answer",
                VALIDATION_SCOPE.actor_id,
                "validation",
                "qwen-plus",
                workflow_root=root_config,
            ),
        )
        async with sessions() as session:
            task_id = (
                await session.execute(
                    select(chat_turn.c.workflow_task_id).where(
                        chat_turn.c.turn_id == turn.turn_id,
                    )
                )
            ).scalar_one()
        token = await store.claim_task(VALIDATION_SCOPE, task_id, "worker")
        state = NativeStateStore(sessions)
        root_id = await state.ensure_root(VALIDATION_SCOPE, task_id, token)
        await state.start_node(VALIDATION_SCOPE, task_id, token, root_id)
        await state.finish_node(VALIDATION_SCOPE, task_id, token, root_id, "An answer")
        await state.finish_task(VALIDATION_SCOPE, task_id, token, "An answer", "completed")
        projection = WorkflowProjection(store, bridge)
        await projection.project(VALIDATION_SCOPE, "conversation-1", turn.turn_id, task_id)
        await projection.project(VALIDATION_SCOPE, "conversation-1", turn.turn_id, task_id)
        loaded = await conversations.load("conversation-1")
        assert loaded.turns[0].answer_snapshot.value.answer == "An answer"
        assert loaded.turns[0].state == "completed"
        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(chat_turn).where(chat_turn.c.turn_id == turn.turn_id)
                    )
                )
                .mappings()
                .one()
            )
            assert (
                row["workflow_projection_sequence"]
                == (await store.snapshot(VALIDATION_SCOPE, task_id))["last_sequence"]
            )
            assert len((await session.execute(select(turn_answer_evidence_snapshot))).all()) == 1
            completed_events = (
                (
                    await session.execute(
                        select(chat_event).where(chat_event.c.event_type == "turn.completed")
                    )
                )
                .mappings()
                .all()
            )
            assert len(completed_events) == 1
            assert completed_events[0]["payload"]["workflowResult"] == {
                "taskId": task_id,
                "answer": "An answer",
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_pending_after_link_is_acknowledged_before_any_model(owned_project_mysql):
    from langgraph.checkpoint.memory import MemorySaver
    from sqlalchemy import select

    from tap.modules.chat.adapters.mysql import chat_turn
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime, WorkflowProjection
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    class NoModel:
        async def chat(self, request):
            raise AssertionError("canceled task dispatched a model")

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        bridge, store = (
            MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE),
            MysqlWorkflowStore(sessions),
        )
        admission = WorkflowAdmissionCoordinator(conversations, bridge, store)
        turn = await admission.create(
            "conversation-1",
            "turn-1",
            "request-1",
            TurnInput(
                "Answer",
                VALIDATION_SCOPE.actor_id,
                "validation",
                "qwen-plus",
                workflow_root=AgentSnapshot(
                    "orchestrator", None, "Tapper", "", "help", (), None, ()
                ),
            ),
        )
        async with sessions() as session:
            task_id = (
                await session.execute(
                    select(chat_turn.c.workflow_task_id).where(
                        chat_turn.c.turn_id == turn.turn_id,
                    )
                )
            ).scalar_one()
        pending = await admission.request_cancel("conversation-1", turn.turn_id)
        assert pending.state == "queued"
        projection = WorkflowProjection(store, bridge)
        gateway = NoModel()
        runtime = NativeWorkflowRuntime(
            store,
            NativeStateStore(sessions),
            gateway,
            checkpointer_factory=lambda scope, task_id, token: MemorySaver(),
            action_gateway_factory=lambda scope,
            task_id,
            token,
            node_id,
            attempt,
            authorize: gateway,
            owner="test-worker",
            standalone_authorize=_trusted_test_authorize,
            chat=bridge,
            projection=projection,
        )
        await runtime.start(VALIDATION_SCOPE, task_id)
        assert (await store.snapshot(VALIDATION_SCOPE, task_id))["status"] == "canceled"
        assert (await conversations.load("conversation-1")).turns[0].state == "canceled"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_continuation_receipt_is_distinct_from_origin_final_answer(owned_project_mysql):
    from sqlalchemy import select

    from tap.modules.chat.adapters.mysql import chat_turn
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.application.dispatch import WorkflowProjection
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        bridge, store = (
            MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE),
            MysqlWorkflowStore(sessions),
        )
        admission = WorkflowAdmissionCoordinator(conversations, bridge, store)
        origin = await admission.create(
            "conversation-1",
            "turn-1",
            "request-1",
            TurnInput(
                "Answer",
                VALIDATION_SCOPE.actor_id,
                "validation",
                "qwen-plus",
                workflow_root=AgentSnapshot(
                    "orchestrator", None, "Tapper", "", "help", (), None, ()
                ),
            ),
        )
        async with sessions() as session:
            task_id = (
                await session.execute(
                    select(chat_turn.c.workflow_task_id).where(
                        chat_turn.c.turn_id == origin.turn_id,
                    )
                )
            ).scalar_one()
        token = await store.claim_task(VALIDATION_SCOPE, task_id, "worker")
        continuation = await admission.continue_task(
            "conversation-1",
            "turn-2",
            "request-2",
            TurnInput(
                "Please include evidence",
                VALIDATION_SCOPE.actor_id,
                "validation",
                "qwen-plus",
                workflow_task_id=task_id,
            ),
        )
        pending = await store.pending_commands(VALIDATION_SCOPE, task_id)
        assert len(pending) == 1
        receipt = await store.ack_command(
            VALIDATION_SCOPE, pending[0].command_id, token, {"status": "applied"}
        )
        assert receipt["status"] == "applied"
        projection = WorkflowProjection(store, bridge)
        await projection.project(VALIDATION_SCOPE, "conversation-1", continuation.turn_id, task_id)
        await projection.project(VALIDATION_SCOPE, "conversation-1", origin.turn_id, task_id)
        loaded = await conversations.load("conversation-1")
        assert loaded.turns[0].state == "running"
        assert loaded.turns[1].state == "completed"
        assert "已应用" in loaded.turns[1].answer_snapshot.value.answer
        state = NativeStateStore(sessions)
        root_id = await state.ensure_root(VALIDATION_SCOPE, task_id, token)
        await state.start_node(VALIDATION_SCOPE, task_id, token, root_id)
        await state.finish_node(VALIDATION_SCOPE, task_id, token, root_id, "The final answer")
        await state.finish_task(VALIDATION_SCOPE, task_id, token, "The final answer", "completed")
        await projection.project(VALIDATION_SCOPE, "conversation-1", origin.turn_id, task_id)
        loaded = await conversations.load("conversation-1")
        assert loaded.turns[0].answer_snapshot.value.answer == "The final answer"
        assert loaded.turns[1].answer_snapshot.value.answer != "The final answer"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_task_receipts_pending_continuation_as_rejected(owned_project_mysql):
    from sqlalchemy import select

    from tap.modules.chat.adapters.mysql import chat_turn
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.application.dispatch import WorkflowProjection
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        bridge, store = (
            MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE),
            MysqlWorkflowStore(sessions),
        )
        admission = WorkflowAdmissionCoordinator(conversations, bridge, store)
        origin = await admission.create(
            "conversation-1",
            "turn-1",
            "request-1",
            TurnInput(
                "Answer",
                VALIDATION_SCOPE.actor_id,
                "validation",
                "qwen-plus",
                workflow_root=AgentSnapshot(
                    "orchestrator", None, "Tapper", "", "help", (), None, ()
                ),
            ),
        )
        async with sessions() as session:
            task_id = (
                await session.execute(
                    select(chat_turn.c.workflow_task_id).where(
                        chat_turn.c.turn_id == origin.turn_id,
                    )
                )
            ).scalar_one()
        token = await store.claim_task(VALIDATION_SCOPE, task_id, "worker")
        continuation = await admission.continue_task(
            "conversation-1",
            "turn-2",
            "request-2",
            TurnInput(
                "Add a source",
                VALIDATION_SCOPE.actor_id,
                "validation",
                "qwen-plus",
                workflow_task_id=task_id,
            ),
        )
        command = (await store.pending_commands(VALIDATION_SCOPE, task_id))[0]
        state = NativeStateStore(sessions)
        root_id = await state.ensure_root(VALIDATION_SCOPE, task_id, token)
        await state.start_node(VALIDATION_SCOPE, task_id, token, root_id)
        await state.finish_node(VALIDATION_SCOPE, task_id, token, root_id, "Answer")
        await state.finish_task(VALIDATION_SCOPE, task_id, token, "Answer", "completed")
        assert (await store.command(VALIDATION_SCOPE, task_id, command.command_id)) is not None
        events = await store.events_after(VALIDATION_SCOPE, task_id, 0, 100)
        assert any(
            event.kind == "command.receipted"
            and event.payload["command_id"] == command.command_id
            and event.payload["receipt"]["status"] == "rejected"
            for event in events
        )
        await WorkflowProjection(store, bridge).project(
            VALIDATION_SCOPE,
            "conversation-1",
            continuation.turn_id,
            task_id,
        )
        result = (await conversations.load("conversation-1")).turns[1]
        assert result.state == "completed"
        assert "未应用" in result.answer_snapshot.value.answer
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_running_specialist_steer_is_in_next_model_request(owned_project_mysql):
    import json

    from langgraph.checkpoint.memory import MemorySaver

    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelToolCall,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.commands import ControlCommand
    from tap.modules.workflows.domain.delegation import DelegationRequest

    class Scripted:
        def __init__(self):
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            calls = (
                (ModelToolCall("tool-1", "knowledge.search", json.dumps({"query": "login"})),)
                if len(self.calls) == 1
                else ()
            )
            output = (
                json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "analysis",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        }
                    }
                )
                if calls
                else "Login-only findings"
            )
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=output,
                actual_model="fixture",
                actual_provider="fixture",
                usage=usage,
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
                tool_calls=calls,
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state, gateway = MysqlWorkflowStore(sessions), NativeStateStore(sessions), Scripted()
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Review",
                "model_alias": "qwen-plus",
                "budget_limit": {"model_calls": 4},
                "candidates": [
                    {
                        "agent_id": "ba",
                        "revision_id": None,
                        "model_alias": "qwen-plus",
                        "tool_ids": ["knowledge.search"],
                    }
                ],
            },
            "running-steer",
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker")
        root = await state.ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, root)
        child = await state.delegate(
            VALIDATION_SCOPE,
            task.task_id,
            token,
            root,
            DelegationRequest("ba", "Review flows", (), "findings", ()),
            "delegate-1",
        )
        await state.wait_node(VALIDATION_SCOPE, task.task_id, token, root)

        async def ordinary_tool(
            scope,
            task_id_arg,
            token_arg,
            node_id_arg,
            attempt_arg,
            name,
            arguments,
            call_id,
            authorize,
        ):
            receipt = await store.submit_command(
                scope,
                ControlCommand(
                    "steer-login",
                    task.task_id,
                    child,
                    "steer",
                    1,
                    "只处理登录场景",
                ),
            )
            assert receipt["status"] == "accepted"
            return "tool result"

        runtime = NativeWorkflowRuntime(
            store,
            state,
            gateway,
            checkpointer_factory=lambda scope, task_id, token: MemorySaver(),
            action_gateway_factory=lambda scope, task_id, token, node_id, attempt, authorize: (
                JournaledModelGateway(gateway, store, task_id, token, node_id, attempt, authorize)
            ),
            ordinary_tool=ordinary_tool,
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        await runtime.advance(VALIDATION_SCOPE, task.task_id, token)
        assert len(gateway.calls) == 2
        assert "只处理登录场景" in gateway.calls[1].context
        assert "只处理登录场景" not in gateway.calls[0].context
        assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["plan_revision"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pause_at_tool_boundary_preserves_attempt_for_resume(owned_project_mysql):
    import json

    from tap.modules.ai.adapters.mysql_checkpointer import GraphFence, MysqlGraphCheckpointer
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelToolCall,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.commands import ControlCommand

    class Scripted:
        def __init__(self):
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            calls = (
                (ModelToolCall("tool-1", "knowledge.search", json.dumps({"query": "login"})),)
                if len(self.calls) == 1
                else ()
            )
            output = (
                json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "analysis",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        }
                    }
                )
                if calls
                else "Finished"
            )
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=output,
                actual_model="fixture",
                actual_provider="fixture",
                usage=usage,
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
                tool_calls=calls,
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state, gateway = MysqlWorkflowStore(sessions), NativeStateStore(sessions), Scripted()
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Review",
                "model_alias": "qwen-plus",
                "root": {"tool_ids": ["knowledge.search"]},
                "budget_limit": {"model_calls": 3},
            },
            "pause-boundary",
        )

        def checkpoint_factory(scope, task_id, token):
            return MysqlGraphCheckpointer(
                sessions,
                scope=scope,
                fence=GraphFence(
                    table=workflow_task,
                    identity_column=workflow_task.c.task_id,
                    identity=task_id,
                    token_column=workflow_task.c.lease_token,
                    token=token,
                    lease_until_column=workflow_task.c.lease_until,
                    status_column=workflow_task.c.status,
                    running_status="running",
                    lease_owner="worker",
                ),
                defer_completion=True,
            )

        async def ordinary_tool(
            scope, task_id, token, node_id, attempt, name, arguments, call_id, authorize
        ):
            assert (
                await store.submit_command(
                    scope, ControlCommand("pause-1", task_id, None, "pause", 1, None)
                )
            )["status"] == "accepted"
            return "tool result"

        runtime = NativeWorkflowRuntime(
            store,
            state,
            gateway,
            checkpointer_factory=checkpoint_factory,
            action_gateway_factory=lambda scope,
            task_id,
            token,
            node_id,
            attempt,
            authorize: JournaledModelGateway(
                gateway, store, task_id, token, node_id, attempt, authorize
            ),
            ordinary_tool=ordinary_tool,
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        assert len(gateway.calls) == 1
        assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["status"] == "paused"
        assert (
            await store.submit_command(
                VALIDATION_SCOPE, ControlCommand("resume-1", task.task_id, None, "resume", 1, None)
            )
        )["status"] == "accepted"
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "succeeded"
        assert snapshot["nodes"][0]["attempt"] == 1
        assert len(gateway.calls) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exact_old_epoch_recovery_requires_checkpoint_and_no_unsettled_actions(
    owned_project_mysql,
):
    from datetime import UTC, datetime, timedelta
    from hashlib import sha256
    from socket import gethostname
    from uuid import uuid4

    from sqlalchemy import update

    from tap.modules.ai.adapters.mysql_checkpointer import graph_checkpoint, graph_run
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.platform.db.project_scope import scope_values

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        old_owner = f"workflow:{gethostname()}:99999999:{uuid4().hex}"
        task = await store.create_task(
            VALIDATION_SCOPE, {"goal": "Recover", "max_attempts": 3}, "exact-epoch"
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, old_owner)
        assert token
        node_id = await NativeStateStore(sessions).ensure_root(
            VALIDATION_SCOPE, task.task_id, token
        )
        await NativeStateStore(sessions).start_node(VALIDATION_SCOPE, task.task_id, token, node_id)
        action_id = uuid4().hex
        await store.reserve_action(
            VALIDATION_SCOPE, task.task_id, token, action_id, "model", node_id=node_id, attempt=1
        )
        await store.settle_action(
            VALIDATION_SCOPE,
            task.task_id,
            token,
            action_id,
            {"output": "persisted"},
            node_id=node_id,
            attempt=1,
        )
        async with sessions.begin() as session:
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task.task_id)
                .values(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
            )
        from tap.modules.workflows.ports.runtime import RecoveryEpisode

        runtime = NativeWorkflowRuntime(
            store,
            NativeStateStore(sessions),
            object(),
            checkpointer_factory=lambda *_: None,
            action_gateway_factory=lambda *_: None,
            owner="recovery-worker",
            standalone_authorize=_trusted_test_authorize,
        )
        episode = await store.mark_lease_lost(VALIDATION_SCOPE, task.task_id)
        assert isinstance(episode, RecoveryEpisode) and episode.execution_token == token
        missing = await runtime.inspect(VALIDATION_SCOPE, episode)
        assert missing.outcome == "stopped" and not missing.checkpoint_compatible
        assert await store.resolve_recovery(VALIDATION_SCOPE, task.task_id, missing) == "recovering"
        now = datetime.now(UTC).replace(tzinfo=None)
        run_id = sha256(f"{task.task_id}:{node_id}".encode()).hexdigest()
        checkpoint_id = str(uuid4())
        async with sessions.begin() as session:
            await session.execute(
                graph_run.insert().values(
                    run_id=run_id,
                    graph_version="agent-workflow-v1",
                    state_schema_version=1,
                    execution_mode="durable",
                    reasoning_mode="agentic",
                    status="RUNNING",
                    current_checkpoint_id=checkpoint_id,
                    attempt_count=1,
                    budget={},
                    created_at=now,
                    updated_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
            await session.execute(
                graph_checkpoint.insert().values(
                    run_id=run_id,
                    checkpoint_ns="",
                    checkpoint_id=checkpoint_id,
                    checkpoint_type="msgpack",
                    checkpoint_blob=b"fixture",
                    metadata_type="msgpack",
                    metadata_blob=b"fixture",
                    created_at=now - timedelta(seconds=2),
                    **scope_values(VALIDATION_SCOPE),
                )
            )
        inspection = await runtime.inspect(VALIDATION_SCOPE, episode)
        assert inspection.outcome == "stopped"
        assert inspection.checkpoint_compatible and inspection.side_effects == "clear"
        assert await store.resolve_recovery(VALIDATION_SCOPE, task.task_id, inspection) == "queued"
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["nodes"][0]["attempt"] == 2
        assert snapshot["budget_used"]["model_calls"] == 1
        new_token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "new-worker")
        assert new_token and new_token != token
        with pytest.raises(ValueError, match="stale workflow lease token"):
            await store.reserve_action(
                VALIDATION_SCOPE,
                task.task_id,
                token,
                uuid4().hex,
                "model",
                node_id=node_id,
                attempt=1,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_old_epoch_with_unsettled_action_remains_recovering(owned_project_mysql):
    from datetime import UTC, datetime, timedelta
    from hashlib import sha256
    from socket import gethostname
    from uuid import uuid4

    from sqlalchemy import update

    from tap.modules.ai.adapters.mysql_checkpointer import graph_checkpoint, graph_run
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.ports.runtime import RecoveryEpisode
    from tap.platform.db.project_scope import scope_values

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        owner = f"workflow:{gethostname()}:99999999:{uuid4().hex}"
        task = await store.create_task(VALIDATION_SCOPE, {"goal": "Recover"}, "unsettled-epoch")
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, owner)
        assert token
        node_id = await NativeStateStore(sessions).ensure_root(
            VALIDATION_SCOPE, task.task_id, token
        )
        await NativeStateStore(sessions).start_node(VALIDATION_SCOPE, task.task_id, token, node_id)
        await store.reserve_action(
            VALIDATION_SCOPE, task.task_id, token, uuid4().hex, "model", node_id=node_id, attempt=1
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        run_id, checkpoint_id = (
            sha256(f"{task.task_id}:{node_id}".encode()).hexdigest(),
            str(uuid4()),
        )
        async with sessions.begin() as session:
            await session.execute(
                graph_run.insert().values(
                    run_id=run_id,
                    graph_version="agent-workflow-v1",
                    state_schema_version=1,
                    execution_mode="durable",
                    reasoning_mode="agentic",
                    status="RUNNING",
                    current_checkpoint_id=checkpoint_id,
                    attempt_count=1,
                    budget={},
                    created_at=now,
                    updated_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
            await session.execute(
                graph_checkpoint.insert().values(
                    run_id=run_id,
                    checkpoint_ns="",
                    checkpoint_id=checkpoint_id,
                    checkpoint_type="msgpack",
                    checkpoint_blob=b"fixture",
                    metadata_type="msgpack",
                    metadata_blob=b"fixture",
                    created_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task.task_id)
                .values(lease_until=now - timedelta(seconds=1))
            )
        episode = await store.mark_lease_lost(VALIDATION_SCOPE, task.task_id)
        assert isinstance(episode, RecoveryEpisode)
        runtime = NativeWorkflowRuntime(
            store,
            NativeStateStore(sessions),
            object(),
            checkpointer_factory=lambda *_: None,
            action_gateway_factory=lambda *_: None,
            owner="recovery-worker",
            standalone_authorize=_trusted_test_authorize,
        )
        inspection = await runtime.inspect(VALIDATION_SCOPE, episode)
        assert inspection.side_effects != "clear"
        assert (
            await store.resolve_recovery(VALIDATION_SCOPE, task.task_id, inspection) == "recovering"
        )
        assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "new-worker") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_clarification_wait_is_durable_and_resume_reuses_attempt(owned_project_mysql):
    import json

    from tap.modules.ai.adapters.mysql_checkpointer import GraphFence, MysqlGraphCheckpointer
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.commands import ControlCommand

    class Gateway:
        def __init__(self):
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            output = (
                json.dumps(
                    {
                        "route": {
                            "kind": "coding",
                            "reason": "unclear",
                            "confidence": 0.2,
                            "required_capabilities": ["code.write"],
                        }
                    }
                )
                if len(self.calls) == 1
                else json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "clarified",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        },
                        "answer": "Analysis",
                    }
                )
            )
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=output,
                actual_model="fixture",
                actual_provider="fixture",
                usage=usage,
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state, gateway = MysqlWorkflowStore(sessions), NativeStateStore(sessions), Gateway()
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Fix it?", "model_alias": "qwen-plus", "budget_limit": {"model_calls": 3}},
            "clarify",
        )

        def checkpoint_factory(scope, task_id, token):
            return MysqlGraphCheckpointer(
                sessions,
                scope=scope,
                fence=GraphFence(
                    table=workflow_task,
                    identity_column=workflow_task.c.task_id,
                    identity=task_id,
                    token_column=workflow_task.c.lease_token,
                    token=token,
                    lease_until_column=workflow_task.c.lease_until,
                    status_column=workflow_task.c.status,
                    running_status="running",
                    lease_owner="worker",
                ),
                defer_completion=True,
            )

        runtime = NativeWorkflowRuntime(
            store,
            state,
            gateway,
            checkpointer_factory=checkpoint_factory,
            action_gateway_factory=lambda scope,
            task_id,
            token,
            node_id,
            attempt,
            authorize: JournaledModelGateway(
                gateway, store, task_id, token, node_id, attempt, authorize
            ),
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "awaiting_input"
        assert any(
            event.kind == "task.awaiting_input" and "确认" in event.payload["question"]
            for event in snapshot["events"]
        )
        assert (
            await store.submit_command(
                VALIDATION_SCOPE,
                ControlCommand("clarify-steer", task.task_id, None, "steer", 1, "只分析，不编码"),
            )
        )["status"] == "accepted"
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "succeeded"
        assert snapshot["nodes"][0]["attempt"] == 1
        assert len(gateway.calls) == 2
        assert "只分析，不编码" in gateway.calls[1].context
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_queued_workflow_fails_without_model_dispatch(owned_project_mysql):
    from datetime import UTC, datetime, timedelta

    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Expired",
                "deadline_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "budget_limit": {"model_calls": 1},
                "model_alias": "qwen-plus",
            },
            "expired-task",
        )
        runtime = NativeWorkflowRuntime(
            store,
            NativeStateStore(sessions),
            object(),
            checkpointer_factory=lambda *_: None,
            action_gateway_factory=lambda *_: None,
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "failed"
        assert any(
            event.kind == "task.failed" and event.payload["reason"] == "deadline exceeded"
            for event in snapshot["events"]
        )
        assert snapshot["budget_used"] == {}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exhausted_model_budget_fails_with_committed_reason(owned_project_mysql):
    from langgraph.checkpoint.memory import MemorySaver

    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime

    class NoCalls:
        def __init__(self):
            self.calls = 0

        async def chat(self, request):
            self.calls += 1
            raise AssertionError("model must not be called")

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, gateway = MysqlWorkflowStore(sessions), NoCalls()
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "No budget",
                "model_alias": "qwen-plus",
                "budget_limit": {"model_calls": 0},
            },
            "zero-budget",
        )
        runtime = NativeWorkflowRuntime(
            store,
            NativeStateStore(sessions),
            gateway,
            checkpointer_factory=lambda *_: MemorySaver(),
            action_gateway_factory=lambda scope,
            task_id,
            token,
            node_id,
            attempt,
            authorize: JournaledModelGateway(
                gateway, store, task_id, token, node_id, attempt, authorize
            ),
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert gateway.calls == 0
        assert snapshot["status"] == "failed"
        assert any(
            event.kind == "task.failed" and "budget" in event.payload["reason"]
            for event in snapshot["events"]
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_settled_before_checkpoint_real_restart_replays_without_new_model_call(
    owned_project_mysql,
):
    import json
    from datetime import UTC, datetime, timedelta
    from socket import gethostname
    from uuid import uuid4

    from sqlalchemy import update

    from tap.modules.ai.adapters.mysql_checkpointer import GraphFence, MysqlGraphCheckpointer
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.application.recovery import RecoveryService

    class Gateway:
        def __init__(self):
            self.calls = 0

        async def chat(self, request):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("settled model action was called twice")
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "fixture",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        },
                        "answer": "Recovered answer",
                    }
                ),
                actual_model="fixture",
                actual_provider="fixture",
                usage=usage,
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
            )

    class CrashAfterSettle(MysqlGraphCheckpointer):
        async def aput(self, config, checkpoint, metadata_value, new_versions):
            if checkpoint.get("channel_values", {}).get("model_round") == 1:
                raise RuntimeError("crash after model settlement before checkpoint")
            return await super().aput(config, checkpoint, metadata_value, new_versions)

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, gateway = MysqlWorkflowStore(sessions), Gateway()
        old_owner = f"workflow:{gethostname()}:99999999:{uuid4().hex}"
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Recover answer",
                "model_alias": "qwen-plus",
                "max_attempts": 3,
                "budget_limit": {"model_calls": 2},
            },
            "real-restart",
        )

        def runtime(owner, checkpoint_type):
            def checkpoint_factory(scope, task_id, token):
                return checkpoint_type(
                    sessions,
                    scope=scope,
                    fence=GraphFence(
                        table=workflow_task,
                        identity_column=workflow_task.c.task_id,
                        identity=task_id,
                        token_column=workflow_task.c.lease_token,
                        token=token,
                        lease_until_column=workflow_task.c.lease_until,
                        status_column=workflow_task.c.status,
                        running_status="running",
                        lease_owner=owner,
                    ),
                    defer_completion=True,
                )

            return NativeWorkflowRuntime(
                store,
                NativeStateStore(sessions),
                gateway,
                checkpointer_factory=checkpoint_factory,
                action_gateway_factory=lambda scope,
                task_id,
                token,
                node_id,
                attempt,
                authorize: JournaledModelGateway(
                    gateway, store, task_id, token, node_id, attempt, authorize
                ),
                owner=owner,
                standalone_authorize=_trusted_test_authorize,
            )

        old = runtime(old_owner, CrashAfterSettle)
        with pytest.raises(RuntimeError, match="crash after model settlement"):
            await old.start(VALIDATION_SCOPE, task.task_id)
        assert gateway.calls == 1
        async with sessions.begin() as session:
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task.task_id)
                .values(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
            )
        new = runtime("new-worker", MysqlGraphCheckpointer)
        assert (
            await RecoveryService(store, new).reconcile(VALIDATION_SCOPE, task.task_id) == "queued"
        )
        await new.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == "succeeded"
        assert gateway.calls == 1
        assert snapshot["nodes"][0]["attempt"] == 2
        assert snapshot["budget_used"]["model_calls"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_specialist_explicit_retry_starts_new_graph_lineage(owned_project_mysql):
    import json

    from sqlalchemy import select

    from tap.modules.ai.adapters.mysql_checkpointer import GraphFence, MysqlGraphCheckpointer
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelToolCall,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_attempt, workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.commands import ControlCommand

    class Scripted:
        def __init__(self):
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            if len(self.calls) == 1:
                output = json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "review",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        }
                    }
                )
                calls = (
                    ModelToolCall(
                        "delegate-1",
                        "workflow_delegate",
                        json.dumps(
                            {
                                "agentId": "ba",
                                "objective": "Review rules",
                                "inputRefs": [],
                                "expectedOutput": "findings",
                                "dependsOn": [],
                            }
                        ),
                    ),
                )
            elif len(self.calls) == 2:
                output, calls = "", ()
            elif len(self.calls) == 3:
                output, calls = "Valid specialist findings", ()
            else:
                output, calls = "Combined answer", ()
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=output,
                actual_model="fixture",
                actual_provider="fixture",
                usage=usage,
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
                tool_calls=calls,
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state, gateway = MysqlWorkflowStore(sessions), NativeStateStore(sessions), Scripted()

        def checkpoint_factory(scope, task_id, token):
            return MysqlGraphCheckpointer(
                sessions,
                scope=scope,
                fence=GraphFence(
                    table=workflow_task,
                    identity_column=workflow_task.c.task_id,
                    identity=task_id,
                    token_column=workflow_task.c.lease_token,
                    token=token,
                    lease_until_column=workflow_task.c.lease_until,
                    status_column=workflow_task.c.status,
                    running_status="running",
                    lease_owner="worker",
                ),
                defer_completion=True,
            )

        runtime = NativeWorkflowRuntime(
            store,
            state,
            gateway,
            checkpointer_factory=checkpoint_factory,
            action_gateway_factory=lambda scope,
            task_id,
            token,
            node_id,
            attempt,
            authorize: JournaledModelGateway(
                gateway, store, task_id, token, node_id, attempt, authorize
            ),
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Review",
                "model_alias": "qwen-plus",
                "budget_limit": {"model_calls": 4},
                "candidates": [{"agent_id": "ba", "revision_id": None, "model_alias": "qwen-plus"}],
                "max_attempts": 3,
            },
            "child-retry",
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        failed = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert failed["status"] == "waiting_dependency"
        child = next(node for node in failed["nodes"] if node["role"] == "ba")
        assert child["status"] == "failed" and child["attempt"] == 1
        assert (
            await store.submit_command(
                VALIDATION_SCOPE,
                ControlCommand(
                    "retry-child",
                    task.task_id,
                    child["node_id"],
                    "retry",
                    1,
                    None,
                ),
            )
        )["status"] == "accepted"
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        final = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert final["status"] == "succeeded"
        assert final["budget_used"]["model_calls"] == 4
        assert final["nodes"][0]["attempt"] == 1
        child = next(node for node in final["nodes"] if node["role"] == "ba")
        assert child["attempt"] == 2 and child["status"] == "succeeded"
        assert len(gateway.calls) == 4
        async with sessions() as session:
            attempts = (
                (
                    await session.execute(
                        select(workflow_attempt)
                        .where(
                            workflow_attempt.c.node_id == child["node_id"],
                        )
                        .order_by(workflow_attempt.c.attempt)
                    )
                )
                .mappings()
                .all()
            )
        assert [item["status"] for item in attempts] == ["failed", "succeeded"]
        assert attempts[0]["graph_run_id"] != attempts[1]["graph_run_id"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_waiting_dependency_task_can_pause_and_resume_without_new_attempt(
    owned_project_mysql,
):
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.commands import ControlCommand
    from tap.modules.workflows.domain.delegation import DelegationRequest

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state = MysqlWorkflowStore(sessions), NativeStateStore(sessions)
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Review",
                "candidates": [{"agent_id": "ba", "revision_id": None}],
            },
            "waiting-pause",
        )
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker")
        root = await state.ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, root)
        child = await state.delegate(
            VALIDATION_SCOPE,
            task.task_id,
            token,
            root,
            DelegationRequest("ba", "Review", (), "summary", ()),
            "call-1",
        )
        await state.wait_node(VALIDATION_SCOPE, task.task_id, token, root)
        await state.release_task(VALIDATION_SCOPE, task.task_id, token, "waiting_dependency")
        runtime = NativeWorkflowRuntime(
            store,
            state,
            object(),
            checkpointer_factory=lambda *_: None,
            action_gateway_factory=lambda *_: None,
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        assert (
            await store.submit_command(
                VALIDATION_SCOPE,
                ControlCommand(
                    "pause-waiting",
                    task.task_id,
                    None,
                    "pause",
                    1,
                    None,
                ),
            )
        )["status"] == "accepted"
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        paused = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert paused["status"] == "paused"
        assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "another-worker") is None
        assert (
            await store.submit_command(
                VALIDATION_SCOPE,
                ControlCommand(
                    "resume-waiting",
                    task.task_id,
                    None,
                    "resume",
                    1,
                    None,
                ),
            )
        )["status"] == "accepted"
        control_token = await store.claim_control(VALIDATION_SCOPE, task.task_id, "worker")
        assert control_token
        await runtime._apply_controls(VALIDATION_SCOPE, task.task_id, control_token)
        await store.release_control(VALIDATION_SCOPE, task.task_id, control_token)
        resumed = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert resumed["status"] == "queued"
        assert next(node for node in resumed["nodes"] if node["node_id"] == child)["attempt"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("safe_checkpoint", [True, False], ids=["safe", "unknown"])
async def test_node_pause_stops_only_target_after_checkpoint_while_sibling_finishes(
    owned_project_mysql, safe_checkpoint
):
    import asyncio
    import json

    from tap.modules.ai.adapters.mysql_checkpointer import GraphFence, MysqlGraphCheckpointer
    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelToolCall,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.commands import ControlCommand

    pause_sent = asyncio.Event()

    class Scripted:
        def __init__(self):
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            if request.context == "Review":
                root_round = sum(call.context == "Review" for call in self.calls)
                if root_round == 1:
                    output = json.dumps(
                        {
                            "route": {
                                "kind": "native",
                                "reason": "review",
                                "confidence": 0.9,
                                "required_capabilities": [],
                            }
                        }
                    )
                    calls = tuple(
                        ModelToolCall(
                            f"call-{agent}",
                            "workflow_delegate",
                            json.dumps(
                                {
                                    "agentId": agent,
                                    "objective": f"Review {agent}",
                                    "inputRefs": [],
                                    "expectedOutput": "findings",
                                    "dependsOn": [],
                                }
                            ),
                        )
                        for agent in ("ba", "qae")
                    )
                else:
                    output, calls = "Combined findings", ()
            elif request.context.startswith("Review ba"):
                ba_round = sum(call.context.startswith("Review ba") for call in self.calls)
                output = "BA findings" if ba_round > 1 else ""
                calls = (
                    ()
                    if ba_round > 1
                    else (
                        ModelToolCall(
                            "search-ba", "knowledge.search", json.dumps({"query": "login"})
                        ),
                    )
                )
            else:
                await asyncio.wait_for(pause_sent.wait(), 5)
                output, calls = "QA findings", ()
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=output,
                actual_model="fixture",
                actual_provider="fixture",
                usage=usage,
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
                tool_calls=calls,
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        class TargetEvidenceStore(MysqlWorkflowStore):
            async def stop_evidence(self, scope, task_id, lease_token, target_node_id):
                if not safe_checkpoint and target_node_id is not None:
                    return False
                return await super().stop_evidence(scope, task_id, lease_token, target_node_id)

        store, state, gateway = (
            TargetEvidenceStore(sessions),
            NativeStateStore(sessions),
            Scripted(),
        )
        task = await store.create_task(
            VALIDATION_SCOPE,
            {
                "goal": "Review",
                "model_alias": "qwen-plus",
                "budget_limit": {"model_calls": 5},
                "candidates": [
                    {
                        "agent_id": agent,
                        "revision_id": None,
                        "model_alias": "qwen-plus",
                        "tool_ids": ["knowledge.search"],
                    }
                    for agent in ("ba", "qae")
                ],
                "max_parallel_agents": 2,
            },
            "node-pause",
        )

        def checkpoint_factory(scope, task_id, token):
            return MysqlGraphCheckpointer(
                sessions,
                scope=scope,
                fence=GraphFence(
                    table=workflow_task,
                    identity_column=workflow_task.c.task_id,
                    identity=task_id,
                    token_column=workflow_task.c.lease_token,
                    token=token,
                    lease_until_column=workflow_task.c.lease_until,
                    status_column=workflow_task.c.status,
                    running_status="running",
                    lease_owner="worker",
                ),
                defer_completion=True,
            )

        async def ordinary_tool(
            scope, task_id, token, node_id, attempt, name, arguments, call_id, authorize
        ):
            assert (
                await store.submit_command(
                    scope,
                    ControlCommand(
                        "pause-ba",
                        task_id,
                        node_id,
                        "pause",
                        1,
                        None,
                    ),
                )
            )["status"] == "accepted"
            pause_sent.set()
            return "Search result"

        runtime = NativeWorkflowRuntime(
            store,
            state,
            gateway,
            checkpointer_factory=checkpoint_factory,
            action_gateway_factory=lambda scope,
            task_id,
            token,
            node_id,
            attempt,
            authorize: JournaledModelGateway(
                gateway, store, task_id, token, node_id, attempt, authorize
            ),
            ordinary_tool=ordinary_tool,
            max_parallel_agents=2,
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        by_role = {node["role"]: node for node in snapshot["nodes"]}
        assert by_role["qae"]["status"] == "succeeded"
        if not safe_checkpoint:
            assert snapshot["status"] == "recovering"
            assert by_role["ba"]["status"] == "running"
            assert any(event.kind == "task.dispatch_blocked" for event in snapshot["events"])
            return
        assert snapshot["status"] == "waiting_dependency"
        assert by_role["ba"]["status"] == "paused"
        assert (
            await store.submit_command(
                VALIDATION_SCOPE,
                ControlCommand(
                    "resume-ba",
                    task.task_id,
                    by_role["ba"]["node_id"],
                    "resume",
                    1,
                    None,
                ),
            )
        )["status"] == "accepted"
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        final = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert final["status"] == "succeeded"
        assert next(node for node in final["nodes"] if node["role"] == "ba")["attempt"] == 1
        assert sum(call.context.startswith("Review qae") for call in gateway.calls) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admission_repair_uses_original_task_limits_after_settings_change(
    owned_project_mysql,
):
    from sqlalchemy import select

    from tap.modules.chat.adapters.mysql import chat_turn
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    class CrashLink(MysqlWorkflowChatBridge):
        async def link_task(self, conversation_id, turn_id, task_id):
            raise RuntimeError("crash after task create")

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        store = MysqlWorkflowStore(sessions)
        frozen = TurnInput(
            "Review",
            VALIDATION_SCOPE.actor_id,
            "validation",
            "qwen-plus",
            workflow_root=AgentSnapshot(
                "orchestrator", "root-v1", "Tapper", "", "help", (), None, ()
            ),
        )
        broken = WorkflowAdmissionCoordinator(
            conversations,
            CrashLink(sessions, scope=VALIDATION_SCOPE),
            store,
            max_model_calls=2,
            max_cost_micros=7,
            max_seconds=120,
            max_attempts=2,
            max_parallel_agents=1,
        )
        with pytest.raises(RuntimeError, match="crash after task create"):
            await broken.create("conversation-1", "turn-1", "request-1", frozen)
        async with sessions() as session:
            original = (await session.execute(select(workflow_task))).mappings().one()
        bridge = MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE)
        rebuilt = WorkflowAdmissionCoordinator(
            conversations,
            bridge,
            store,
            max_model_calls=40,
            max_cost_micros=500,
            max_seconds=900,
            max_attempts=3,
            max_parallel_agents=3,
        )
        await rebuilt.repair(limit=10)
        async with sessions() as session:
            rows = (await session.execute(select(workflow_task))).mappings().all()
            linked = (
                await session.execute(
                    select(chat_turn.c.workflow_task_id).where(
                        chat_turn.c.turn_id == "turn-1",
                    )
                )
            ).scalar_one()
        assert len(rows) == 1 and linked == original["task_id"]
        assert rows[0]["request_json"] == original["request_json"]
        assert rows[0]["request_json"]["max_parallel_agents"] == 1
        assert rows[0]["request_json"]["budget_limit"] == {"model_calls": 2, "cost_micros": 7}
        assert rows[0]["admission_ready"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_known_cost_settles_once_and_unknown_cost_does_not_masquerade_as_zero(
    owned_project_mysql,
):
    from tap.modules.workflows.domain.commands import WorkflowLimitExceeded

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        store = MysqlWorkflowStore(async_sessionmaker(engine, expire_on_commit=False))
        known = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Known cost", "budget_limit": {"model_calls": 3, "cost_micros": 2}},
            "known-cost-cap",
        )
        token = await store.claim_task(VALIDATION_SCOPE, known.task_id, "worker")
        assert token
        assert (
            await store.reserve_action(VALIDATION_SCOPE, known.task_id, token, "priced-1", "model")
            == "reserved"
        )
        result = {"output": "Answer", "cost_usd": "0.000003"}
        assert (
            await store.settle_action(VALIDATION_SCOPE, known.task_id, token, "priced-1", result)
            == result
        )
        assert (
            await store.settle_action(VALIDATION_SCOPE, known.task_id, token, "priced-1", result)
            == result
        )
        assert (await store.snapshot(VALIDATION_SCOPE, known.task_id))["budget_used"] == {
            "model_calls": 1,
            "cost_micros": 3,
        }
        with pytest.raises(WorkflowLimitExceeded):
            await store.reserve_action(VALIDATION_SCOPE, known.task_id, token, "priced-2", "model")

        unknown = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Unknown cost", "budget_limit": {"model_calls": 2, "cost_micros": 2}},
            "unknown-cost-cap",
        )
        token = await store.claim_task(VALIDATION_SCOPE, unknown.task_id, "worker")
        assert token
        await store.reserve_action(VALIDATION_SCOPE, unknown.task_id, token, "unpriced-1", "model")
        await store.settle_action(
            VALIDATION_SCOPE,
            unknown.task_id,
            token,
            "unpriced-1",
            {"output": "Answer", "cost_usd": None},
        )
        assert (await store.snapshot(VALIDATION_SCOPE, unknown.task_id))["budget_used"] == {
            "model_calls": 1,
            "unknown_cost_actions": 1,
        }
        assert (
            await store.reserve_action(
                VALIDATION_SCOPE, unknown.task_id, token, "unpriced-2", "model"
            )
            == "reserved"
        )

        unlimited = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Unlimited cost", "budget_limit": {"model_calls": 3}},
            "unlimited-cost-observation",
        )
        token = await store.claim_task(VALIDATION_SCOPE, unlimited.task_id, "worker")
        assert token
        await store.reserve_action(
            VALIDATION_SCOPE, unlimited.task_id, token, "unlimited-known", "model"
        )
        await store.settle_action(
            VALIDATION_SCOPE, unlimited.task_id, token, "unlimited-known", {"cost_usd": "0.000004"}
        )
        await store.reserve_action(
            VALIDATION_SCOPE, unlimited.task_id, token, "unlimited-unknown", "model"
        )
        await store.settle_action(
            VALIDATION_SCOPE, unlimited.task_id, token, "unlimited-unknown", {"cost_usd": None}
        )
        await store.settle_action(
            VALIDATION_SCOPE, unlimited.task_id, token, "unlimited-unknown", {"cost_usd": None}
        )
        assert (await store.snapshot(VALIDATION_SCOPE, unlimited.task_id))["budget_used"] == {
            "model_calls": 2,
            "cost_micros": 4,
            "unknown_cost_actions": 1,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_linked_continuation_without_command_is_repaired_after_crash(
    owned_project_mysql, monkeypatch
):
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import TurnInput
    from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
    from tap.modules.workflows.application.selection import WorkflowSelection
    from tap.modules.workflows.ports.catalog import AgentSnapshot

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
        )
        bridge = MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE)
        store = MysqlWorkflowStore(sessions)
        admission = WorkflowAdmissionCoordinator(conversations, bridge, store)
        root = AgentSnapshot("orchestrator", "root-v1", "Tapper", "", "help", (), None, ())
        await admission.create(
            "conversation-1",
            "turn-origin",
            "request-origin",
            TurnInput(
                "Review", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus", workflow_root=root
            ),
        )
        original_task = await store.task_by_key(VALIDATION_SCOPE, admission._key("turn-origin"))
        assert original_task is not None
        task_id = original_task.task_id
        followup = TurnInput(
            "Only login",
            VALIDATION_SCOPE.actor_id,
            "validation",
            "qwen-plus",
            workflow_task_id=task_id,
        )

        async def crash(*args, **kwargs):
            raise RuntimeError("crash after link")

        monkeypatch.setattr(admission._selection, "follow_up", crash)
        with pytest.raises(RuntimeError, match="crash after link"):
            await admission.continue_task(
                "conversation-1", "turn-followup", "request-followup", followup
            )
        command_id = WorkflowSelection.command_id(
            VALIDATION_SCOPE, "conversation-1", task_id, "request-followup"
        )
        assert await store.command(VALIDATION_SCOPE, task_id, command_id) is None
        repaired = WorkflowAdmissionCoordinator(conversations, bridge, store)
        monkeypatch.setattr(repaired, "_notify", crash)
        with pytest.raises(RuntimeError, match="crash after link"):
            await repaired.repair(limit=10)
        command = await store.command(VALIDATION_SCOPE, task_id, command_id)
        assert command is not None and command.text == "Only login"
        healthy = WorkflowAdmissionCoordinator(conversations, bridge, store)
        await healthy.repair(limit=10)
        assert await store.command(VALIDATION_SCOPE, task_id, command_id) == command
        await bridge.request_cancel("conversation-1", "turn-followup")
        from sqlalchemy import update

        from tap.modules.chat.adapters.mysql import chat_turn

        async with sessions.begin() as session:
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-followup")
                .values(state="canceled")
            )
        candidates = await bridge.repair_candidates(10)
        assert all(turn_id != "turn-followup" for _, turn_id, _, _ in candidates)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan_filters_live_leases_before_limit_and_rotates_persistent_recovery(
    owned_project_mysql,
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from tap.modules.workflows.adapters.schema import workflow_task

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        tasks = [
            await store.create_task(VALIDATION_SCOPE, {"goal": f"Review {i}"}, f"scan-{i}")
            for i in range(4)
        ]
        for task in tasks[:2]:
            assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "live-worker")
        first = await store.scan_work(VALIDATION_SCOPE, 1)
        assert first and first[0][0] in {task.task_id for task in tasks[2:]}
        async with sessions.begin() as session:
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id.in_([task.task_id for task in tasks[:2]]))
                .values(
                    status="recovering",
                    lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
                )
            )
        seen = {row[0] for _ in range(8) for row in await store.scan_work(VALIDATION_SCOPE, 1)}
        assert seen == {task.task_id for task in tasks}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("last_node", ["root", "child"])
async def test_completed_node_handoff_crash_reconciles_without_model_recall(
    owned_project_mysql, last_node
):
    from datetime import UTC, datetime, timedelta
    from hashlib import sha256
    from socket import gethostname
    from uuid import uuid4

    from sqlalchemy import update

    from tap.modules.ai.adapters.mysql_checkpointer import graph_checkpoint, graph_run
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.application.recovery import RecoveryService
    from tap.modules.workflows.domain.delegation import DelegationRequest
    from tap.platform.db.project_scope import scope_values

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state = MysqlWorkflowStore(sessions), NativeStateStore(sessions)
        request = {"goal": "Review", "max_attempts": 3}
        if last_node == "child":
            request["candidates"] = [{"agent_id": "ba", "revision_id": None}]
        task = await store.create_task(VALIDATION_SCOPE, request, f"handoff-{last_node}")
        owner = f"workflow:{gethostname()}:99999999:{uuid4().hex}"
        token = await store.claim_task(VALIDATION_SCOPE, task.task_id, owner)
        assert token
        root = await state.ensure_root(VALIDATION_SCOPE, task.task_id, token)
        await state.start_node(VALIDATION_SCOPE, task.task_id, token, root)
        if last_node == "child":
            node_id = await state.delegate(
                VALIDATION_SCOPE,
                task.task_id,
                token,
                root,
                DelegationRequest("ba", "Review", (), "summary", ()),
                "call-child",
            )
            await state.wait_node(VALIDATION_SCOPE, task.task_id, token, root)
            await state.start_node(VALIDATION_SCOPE, task.task_id, token, node_id)
        else:
            node_id = root
        await state.finish_node(VALIDATION_SCOPE, task.task_id, token, node_id, "Private answer")
        now = datetime.now(UTC).replace(tzinfo=None)
        run_id = sha256(f"{task.task_id}:{node_id}".encode()).hexdigest()
        checkpoint_id = uuid4().hex
        async with sessions.begin() as session:
            await session.execute(
                graph_run.insert().values(
                    run_id=run_id,
                    graph_version="agent-workflow-v1",
                    state_schema_version=1,
                    execution_mode="durable",
                    reasoning_mode="agentic",
                    status="RUNNING",
                    current_checkpoint_id=checkpoint_id,
                    attempt_count=1,
                    budget={},
                    created_at=now,
                    updated_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
            await session.execute(
                graph_checkpoint.insert().values(
                    run_id=run_id,
                    checkpoint_ns="",
                    checkpoint_id=checkpoint_id,
                    checkpoint_type="msgpack",
                    checkpoint_blob=b"fixture",
                    metadata_type="msgpack",
                    metadata_blob=b"fixture",
                    created_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task.task_id)
                .values(lease_until=now - timedelta(seconds=1))
            )

        class NeverGateway:
            async def chat(self, request):
                raise AssertionError("completed node must not call model again")

        runtime = NativeWorkflowRuntime(
            store,
            state,
            NeverGateway(),
            checkpointer_factory=lambda *_: None,
            action_gateway_factory=lambda *_: NeverGateway(),
            owner="new-worker",
            standalone_authorize=_trusted_test_authorize,
        )
        assert (
            await RecoveryService(store, runtime).reconcile(VALIDATION_SCOPE, task.task_id)
            == "queued"
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        if last_node == "root":
            assert snapshot["status"] == "succeeded"
            assert any(event.kind == "task.completed" for event in snapshot["events"])
        else:
            assert snapshot["status"] == "queued"
            assert root in await store.ready_nodes(VALIDATION_SCOPE, task.task_id) or any(
                node["node_id"] == root and node["status"] == "queued" for node in snapshot["nodes"]
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "safe_checkpoint", "targeted"),
    [
        ("pause", True, False),
        ("cancel", True, False),
        ("pause", False, False),
        ("pause", False, True),
    ],
    ids=["pause", "cancel", "unknown-checkpoint", "targeted-root-unknown"],
)
async def test_root_control_between_model_boundary_and_reservation_is_acknowledged(
    owned_project_mysql, kind, safe_checkpoint, targeted
):
    from tap.modules.ai.adapters.mysql_checkpointer import GraphFence, MysqlGraphCheckpointer
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.adapters.schema import workflow_task
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.commands import ControlCommand

    class ControlBeforeReservation(MysqlWorkflowStore):
        async def stop_evidence(self, scope, task_id, lease_token, target_node_id):
            if not safe_checkpoint:
                return False
            return await super().stop_evidence(scope, task_id, lease_token, target_node_id)

        def __init__(self, sessions):
            super().__init__(sessions)
            self.injected = False

        async def reserve_action(self, scope, task_id, token, action_id, action_kind, **kwargs):
            if not self.injected:
                self.injected = True
                receipt = await self.submit_command(
                    scope,
                    ControlCommand(
                        f"{kind}-race",
                        task_id,
                        kwargs.get("node_id") if targeted else None,
                        kind,
                        1,
                        None,
                    ),
                )
                assert receipt["status"] == "accepted"
            return await super().reserve_action(
                scope, task_id, token, action_id, action_kind, **kwargs
            )

    class NeverGateway:
        async def chat(self, request):
            raise AssertionError("paused model must not dispatch")

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store, state, gateway = (
            ControlBeforeReservation(sessions),
            NativeStateStore(sessions),
            NeverGateway(),
        )
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "model_alias": "qwen-plus"},
            f"{kind}-reservation-race",
        )

        def checkpoint_factory(scope, task_id, token):
            return MysqlGraphCheckpointer(
                sessions,
                scope=scope,
                fence=GraphFence(
                    table=workflow_task,
                    identity_column=workflow_task.c.task_id,
                    identity=task_id,
                    token_column=workflow_task.c.lease_token,
                    token=token,
                    lease_until_column=workflow_task.c.lease_until,
                    status_column=workflow_task.c.status,
                    running_status="running",
                    lease_owner="worker",
                ),
                defer_completion=True,
            )

        runtime = NativeWorkflowRuntime(
            store,
            state,
            gateway,
            checkpointer_factory=checkpoint_factory,
            action_gateway_factory=lambda scope, task_id, token, node_id, attempt, authorize: (
                JournaledModelGateway(gateway, store, task_id, token, node_id, attempt, authorize)
            ),
            owner="worker",
            standalone_authorize=_trusted_test_authorize,
        )
        await runtime.start(VALIDATION_SCOPE, task.task_id)
        snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert snapshot["status"] == (
            "recovering" if not safe_checkpoint else ("paused" if kind == "pause" else "canceled")
        )
        async with sessions() as session:
            from sqlalchemy import select

            row = (
                await session.execute(
                    select(
                        workflow_task.c.lease_token,
                        workflow_task.c.lease_owner,
                        workflow_task.c.lease_until,
                    ).where(workflow_task.c.task_id == task.task_id)
                )
            ).one()
            assert row.lease_owner is None and row.lease_until is None
            assert (row.lease_token is not None) == (not safe_checkpoint)
        if safe_checkpoint:
            assert any(
                event.kind == "command.receipted"
                and event.payload["command_id"] == f"{kind}-race"
                and event.payload["receipt"]["status"] == "applied"
                for event in snapshot["events"]
            )
        else:
            assert any(event.kind == "task.dispatch_blocked" for event in snapshot["events"])
            if targeted:
                assert (
                    next(node for node in snapshot["nodes"] if node["role"] == "root")["status"]
                    == "running"
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_originless_dispatch_requires_explicit_current_authorization(owned_project_mysql):
    import json

    from langgraph.checkpoint.memory import MemorySaver

    from tap.modules.ai.domain.models import (
        ModelCallAudit,
        ModelOperation,
        ModelResult,
        ModelUsage,
        text_digest,
    )
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.adapters.native_state import NativeStateStore
    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime

    class Gateway:
        def __init__(self):
            self.calls = 0

        async def chat(self, request):
            self.calls += 1
            usage = ModelUsage(1, 1)
            return ModelResult(
                output=json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "ready",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        },
                        "answer": "Done",
                    }
                ),
                actual_model="fixture",
                actual_provider="fixture",
                usage=usage,
                audit=ModelCallAudit(
                    scope=request.scope,
                    alias=request.alias,
                    operation=ModelOperation.CHAT,
                    prompt_digest=request.prompt_digest,
                    schema_digest=None,
                    context_digest=text_digest(request.context),
                    idempotency_key=request.idempotency_key,
                    actual_provider="fixture",
                    actual_model="fixture",
                    usage=usage,
                ),
            )

    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        for mode in ("absent", "revoked", "allowed"):
            gateway = Gateway()
            task = await store.create_task(
                VALIDATION_SCOPE,
                {"goal": "Review", "model_alias": "qwen-plus", "budget_limit": {"model_calls": 2}},
                f"originless-{mode}",
            )
            decisions = []

            async def authorize(scope, task_id):
                decisions.append((scope, task_id))
                return mode == "allowed"

            runtime = NativeWorkflowRuntime(
                store,
                NativeStateStore(sessions),
                gateway,
                checkpointer_factory=lambda *_: MemorySaver(),
                action_gateway_factory=lambda scope, task_id, token, node_id, attempt, allowed: (
                    JournaledModelGateway(gateway, store, task_id, token, node_id, attempt, allowed)
                ),
                owner=f"worker-{mode}",
                standalone_authorize=None if mode == "absent" else authorize,
            )
            await runtime.start(VALIDATION_SCOPE, task.task_id)
            if mode == "allowed":
                assert decisions and gateway.calls > 0
            else:
                assert gateway.calls == 0
                assert decisions == ([] if mode == "absent" else [(VALIDATION_SCOPE, task.task_id)])
    finally:
        await engine.dispose()
