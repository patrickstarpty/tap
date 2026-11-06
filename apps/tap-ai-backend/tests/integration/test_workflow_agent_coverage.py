"""Actual invocation projection stays source-owned and retry-idempotent."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.mysql_observations import MysqlAgentObservations
from tests.owned_mysql import owned_project_database_url


@pytest_asyncio.fixture
async def observations(owned_project_mysql):
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield MysqlAgentObservations(sessions, MysqlWorkflowStore(sessions)), sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_one_observed_instance_survives_model_loop_and_retry(observations) -> None:
    observations, _ = observations
    scope = VALIDATION_SCOPE
    first = await observations.start(
        scope,
        source_kind="chat",
        source_key="turn-1",
        invocation_key="turn-1:orchestrator",
        agent_key="orchestrator",
        agent_revision_id=None,
        display_name="Tapper",
        conversation_id="conversation-1",
        source_attempt=1,
        source_claim_key="lease-1",
    )
    await observations.start(
        scope,
        source_kind="chat",
        source_key="turn-1",
        invocation_key="turn-1:orchestrator",
        agent_key="orchestrator",
        agent_revision_id=None,
        display_name="Tapper",
        conversation_id="conversation-1",
        source_attempt=1,
        source_claim_key="lease-1",
    )
    repeated = await observations.start(
        scope,
        source_kind="chat",
        source_key="turn-1",
        invocation_key="turn-1:orchestrator",
        agent_key="orchestrator",
        agent_revision_id=None,
        display_name="Tapper",
        conversation_id="conversation-1",
        source_attempt=2,
        source_claim_key="lease-2",
    )
    await observations.start(
        scope,
        source_kind="chat",
        source_key="turn-1",
        invocation_key="turn-1:orchestrator",
        agent_key="orchestrator",
        agent_revision_id=None,
        display_name="Tapper",
        conversation_id="conversation-1",
        source_attempt=1,
        source_claim_key="lease-1",
    )
    assert repeated == first
    snapshot = await observations.snapshot(scope, first)
    assert snapshot["status"] == "running"
    assert len(snapshot["nodes"]) == 1
    assert snapshot["nodes"][0]["status"] == "running"
    assert snapshot["nodes"][0]["attempt"] == 2
    assert [(item["attempt"], item["status"]) for item in snapshot["attempts"]] == [
        (1, "unknown"),
        (2, "running"),
    ]
    assert [event.kind for event in snapshot["events"]].count("node.started") == 2
    assert await observations.finish(scope, first, "completed") is True
    assert await observations.finish(scope, first, "completed") is False
    done = await observations.snapshot(scope, first)
    assert done["status"] == "succeeded"
    assert [(item["attempt"], item["status"]) for item in done["attempts"]] == [
        (1, "unknown"),
        (2, "succeeded"),
    ]
    assert [event.kind for event in done["events"]].count("task.completed") == 1


@pytest.mark.asyncio
async def test_failed_actual_start_rolls_back_empty_observed_task(observations) -> None:
    from sqlalchemy.exc import IntegrityError

    observations, _ = observations
    with pytest.raises(IntegrityError):
        await observations.start(
            VALIDATION_SCOPE,
            source_kind="chat",
            source_key="turn-invalid-revision",
            invocation_key="turn-invalid-revision:missing",
            agent_key="custom-agent",
            agent_revision_id="missing-revision",
            display_name="Custom",
            conversation_id="conversation-1",
        )
    assert await observations.pending_source(VALIDATION_SCOPE, "chat", 10) == ()


@pytest.mark.asyncio
async def test_chat_relations_are_distinct_child_invocations_and_not_source_terminal(
    observations,
) -> None:
    from tap.modules.workflows.adapters.observation_bindings import (
        ChatObservationBinding,
        RelationObservationBinding,
    )

    observer, _ = observations
    root_task = await observer.start(
        VALIDATION_SCOPE,
        source_kind="chat",
        source_key="turn-relations",
        invocation_key="turn-relations:legacy-default",
        agent_key="orchestrator",
        agent_revision_id=None,
        display_name="Tapper",
        conversation_id="conversation-relations",
    )

    class Conversations:
        async def observed_task_id(self, conversation_id, turn_id):
            assert (conversation_id, turn_id) == ("conversation-relations", "turn-relations")
            return root_task

    chat = ChatObservationBinding(observer, object(), Conversations(), VALIDATION_SCOPE)
    relation = RelationObservationBinding(observer, VALIDATION_SCOPE)
    async with chat.bind_relation("conversation-relations", "turn-relations"):
        async with relation.bind("turn-relations", "relations:turn-relations:call-1"):
            pass
        async with relation.bind("turn-relations", "relations:turn-relations:call-2"):
            pass
        async with relation.bind("turn-relations", "relations:turn-relations:call-1"):
            pass
    snapshot = await observer.snapshot(VALIDATION_SCOPE, root_task)
    assert snapshot["status"] == "running"
    assert len(snapshot["nodes"]) == 3
    root = next(node for node in snapshot["nodes"] if node["parent_node_id"] is None)
    children = [node for node in snapshot["nodes"] if node["parent_node_id"] == root["node_id"]]
    assert len(children) == 2
    assert {node["status"] for node in children} == {"succeeded"}
    assert [event.kind for event in snapshot["events"]].count("node.started") == 3
    assert [event.kind for event in snapshot["events"]].count("task.completed") == 0


@pytest.mark.asyncio
async def test_source_terminal_keeps_unsettled_observed_child_outcome_unknown(observations) -> None:
    observer, _ = observations
    task_id = await observer.start(
        VALIDATION_SCOPE,
        source_kind="chat",
        source_key="turn-child-crash",
        invocation_key="turn-child-crash:legacy-default",
        agent_key="orchestrator",
        agent_revision_id=None,
        display_name="Tapper",
        conversation_id="conversation-child-crash",
    )
    root = next(
        node
        for node in (await observer.snapshot(VALIDATION_SCOPE, task_id))["nodes"]
        if node["parent_node_id"] is None
    )
    async with observer.record_agent_invocation(
        VALIDATION_SCOPE, task_id, "relation-analysis", root["node_id"], "relation:unsettled"
    ) as unknown_child:
        pass  # Source process crashed before it recorded the operation result.
    async with observer.record_agent_invocation(
        VALIDATION_SCOPE, task_id, "relation-analysis", root["node_id"], "relation:settled"
    ) as settled_child:
        pass
    assert await observer.finish_node(VALIDATION_SCOPE, task_id, settled_child, succeeded=True)
    assert await observer.finish(VALIDATION_SCOPE, task_id, "completed")
    assert not await observer.finish(VALIDATION_SCOPE, task_id, "completed")
    snapshot = await observer.snapshot(VALIDATION_SCOPE, task_id)
    nodes = {node["node_id"]: node for node in snapshot["nodes"]}
    assert snapshot["status"] == "succeeded"
    assert nodes[unknown_child]["status"] == "unknown"
    assert nodes[settled_child]["status"] == "succeeded"
    attempts = {row["node_id"]: row for row in snapshot["attempts"]}
    assert attempts[unknown_child]["status"] == "unknown"
    assert attempts[settled_child]["status"] == "succeeded"
    assert [event.kind for event in snapshot["events"]].count("node.outcome_unknown") == 1


@pytest.mark.asyncio
async def test_bounded_chat_source_repair_reaches_terminal_after_running_prefix(
    observations,
) -> None:
    from tap.modules.workflows.adapters.observation_bindings import ChatObservationBinding

    observer, _ = observations
    task_ids = {}
    for index in range(4):
        turn_id = f"repair-turn-{index}"
        task_ids[turn_id] = await observer.start(
            VALIDATION_SCOPE,
            source_kind="chat",
            source_key=turn_id,
            invocation_key=f"{turn_id}:legacy-default",
            agent_key="orchestrator",
            agent_revision_id=None,
            display_name="Tapper",
            conversation_id="repair-conversation",
        )
    terminal = max(task_ids, key=task_ids.get)

    class Conversations:
        async def observation_turn_state(self, conversation_id, turn_id):
            return "completed" if turn_id == terminal else "running"

        async def link_observed_task(self, conversation_id, turn_id, task_id):
            assert task_ids[turn_id] == task_id

    binding = ChatObservationBinding(observer, object(), Conversations(), VALIDATION_SCOPE)
    assert await binding.repair(limit=2) == 0
    assert await binding.repair(limit=2) == 1
    assert (await observer.snapshot(VALIDATION_SCOPE, task_ids[terminal]))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_standalone_relation_operation_uses_query_plan_identity(observations) -> None:
    from tap.modules.workflows.adapters.observation_bindings import RelationObservationBinding

    observer, _ = observations
    binding = RelationObservationBinding(observer, VALIDATION_SCOPE)
    async with binding.bind(None, "relations:query-plan-1"):
        pass
    async with binding.bind(None, "relations:query-plan-1"):
        pass
    task_id = observer.task_id_for(VALIDATION_SCOPE, "relation_analysis", "relations:query-plan-1")
    snapshot = await observer.snapshot(VALIDATION_SCOPE, task_id)
    assert snapshot["status"] == "succeeded"
    assert len(snapshot["nodes"]) == 1
    assert [event.kind for event in snapshot["events"]].count("task.completed") == 1


@pytest.mark.asyncio
async def test_test_design_source_fact_repairs_post_cas_crash(observations) -> None:
    from dataclasses import replace
    from datetime import timedelta

    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository
    from tap.modules.workflows.adapters.observation_bindings import TestDesignObservationBinding
    from tests.integration.plan_generation_support import (
        generation_request,
        plan_repository,
        seed_completed_turn,
        seed_test_design_authority,
    )
    from tests.integration.test_knowledge_publication import NOW
    from tests.integration.test_test_plan_publish import _draft

    observer, sessions = observations
    publication = await seed_test_design_authority(sessions)
    await seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
    jobs: MysqlTestPlanRepository = plan_repository(sessions)
    request = generation_request(publication)
    now = NOW
    await jobs.request_generation(VALIDATION_SCOPE, request, now=now)
    (claim,) = await jobs.claim_generation_jobs(
        VALIDATION_SCOPE,
        worker_id="observe-design",
        now=now + timedelta(seconds=1),
        lease_duration=timedelta(minutes=1),
        limit=1,
    )
    binding = TestDesignObservationBinding(
        observer,
        MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE),
        jobs,
        VALIDATION_SCOPE,
    )
    await binding.before_generate(claim)
    task_id = observer.task_id_for(
        VALIDATION_SCOPE, "test_design", f"{request.job_id}:{request.agent_revision_id}"
    )
    assert (await observer.snapshot(VALIDATION_SCOPE, task_id))["status"] == "running"
    draft = replace(
        _draft(),
        test_plan_id=request.test_plan_id,
        revision_id=request.revision_id,
    ).with_recomputed_digest()
    await jobs.complete_generation(VALIDATION_SCOPE, claim, draft, now=now + timedelta(seconds=2))
    assert (await observer.snapshot(VALIDATION_SCOPE, task_id))["status"] == "running"
    assert await binding.repair(limit=1) == 1
    assert (await observer.snapshot(VALIDATION_SCOPE, task_id))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_suggestion_source_cas_and_observation_finish_share_transaction(observations) -> None:
    from datetime import UTC, datetime, timedelta

    from tap.modules.chat.adapters.mysql_suggestions import MysqlSuggestionStore
    from tap.modules.chat.domain.suggestions import RefreshReason, SuggestionKey
    from tap.modules.workflows.adapters.observation_bindings import SuggestionObservationBinding

    observer, sessions = observations
    binding = SuggestionObservationBinding(observer, VALIDATION_SCOPE)
    store = MysqlSuggestionStore(
        sessions, scope=VALIDATION_SCOPE, on_complete=binding.accepted_complete
    )
    key = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="en")
    now = datetime(2026, 11, 6, tzinfo=UTC)
    await store.request_refresh(key, RefreshReason.MISSING, now=now)
    first = (
        await store.claim_due(
            worker_id="first",
            now=now + timedelta(days=1),
            limit=1,
            lease_duration=timedelta(minutes=1),
        )
    )[0]
    await binding.before_generate(first)
    task_id = observer.task_id_for(VALIDATION_SCOPE, "suggestions", first.refresh_cycle_id)
    second = (
        await store.claim_due(
            worker_id="second",
            now=now + timedelta(days=1, minutes=2),
            limit=1,
            lease_duration=timedelta(minutes=1),
        )
    )[0]
    await binding.before_generate(second)
    await binding.before_generate(first)
    active = await observer.snapshot(VALIDATION_SCOPE, task_id)
    assert active["nodes"][0]["attempt"] == 2
    assert [(item["attempt"], item["status"]) for item in active["attempts"]] == [
        (1, "unknown"),
        (2, "running"),
    ]
    assert await store.complete_refresh(first, (), now=now + timedelta(days=1, minutes=2)) is False
    assert (await observer.snapshot(VALIDATION_SCOPE, task_id))["status"] == "running"
    assert await store.complete_refresh(second, (), now=now + timedelta(days=1, minutes=3)) is True
    assert (await observer.snapshot(VALIDATION_SCOPE, task_id))["status"] == "succeeded"
    assert [
        (item["attempt"], item["status"])
        for item in (await observer.snapshot(VALIDATION_SCOPE, task_id))["attempts"]
    ] == [(1, "unknown"), (2, "succeeded")]


@pytest.mark.asyncio
async def test_suggestion_source_transaction_rollback_keeps_observation_running(
    observations,
) -> None:
    from datetime import UTC, datetime, timedelta

    from tap.modules.chat.adapters.mysql_suggestions import MysqlSuggestionStore
    from tap.modules.chat.domain.suggestions import RefreshReason, SuggestionKey
    from tap.modules.workflows.adapters.observation_bindings import SuggestionObservationBinding

    observer, sessions = observations
    binding = SuggestionObservationBinding(observer, VALIDATION_SCOPE)

    async def rollback_after_projection(session, claim):
        await binding.accepted_complete(session, claim)
        raise RuntimeError("source transaction rolled back")

    store = MysqlSuggestionStore(
        sessions, scope=VALIDATION_SCOPE, on_complete=rollback_after_projection
    )
    key = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="en")
    now = datetime(2026, 11, 6, tzinfo=UTC)
    await store.request_refresh(key, RefreshReason.MISSING, now=now)
    (claim,) = await store.claim_due(
        worker_id="rollback",
        now=now + timedelta(days=1),
        limit=1,
        lease_duration=timedelta(minutes=1),
    )
    await binding.before_generate(claim)
    task_id = observer.task_id_for(VALIDATION_SCOPE, "suggestions", claim.refresh_cycle_id)
    with pytest.raises(RuntimeError, match="rolled back"):
        await store.complete_refresh(claim, (), now=now + timedelta(days=1, seconds=1))
    assert (await observer.snapshot(VALIDATION_SCOPE, task_id))["status"] == "running"


@pytest.mark.asyncio
async def test_suggestion_late_old_model_attempt_never_receives_winner_terminal(
    observations,
) -> None:
    from datetime import UTC, datetime, timedelta

    from tap.modules.chat.adapters.mysql_suggestions import MysqlSuggestionStore
    from tap.modules.chat.domain.suggestions import RefreshReason, SuggestionKey
    from tap.modules.workflows.adapters.observation_bindings import SuggestionObservationBinding

    observer, sessions = observations
    binding = SuggestionObservationBinding(observer, VALIDATION_SCOPE)
    store = MysqlSuggestionStore(
        sessions, scope=VALIDATION_SCOPE, on_complete=binding.accepted_complete
    )
    key = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="en")
    now = datetime(2026, 11, 6, tzinfo=UTC)
    await store.request_refresh(key, RefreshReason.MISSING, now=now)
    (old,) = await store.claim_due(
        worker_id="old",
        now=now + timedelta(days=1),
        limit=1,
        lease_duration=timedelta(minutes=1),
    )
    (winner,) = await store.claim_due(
        worker_id="winner",
        now=now + timedelta(days=1, minutes=2),
        limit=1,
        lease_duration=timedelta(minutes=1),
    )
    await binding.before_generate(winner)
    await binding.before_generate(old)
    task_id = observer.task_id_for(VALIDATION_SCOPE, "suggestions", winner.refresh_cycle_id)
    assert (await observer.snapshot(VALIDATION_SCOPE, task_id))["nodes"][0]["attempt"] == 2
    assert await store.complete_refresh(old, (), now=now + timedelta(days=1, minutes=3)) is False
    assert await store.complete_refresh(winner, (), now=now + timedelta(days=1, minutes=3)) is True
    snapshot = await observer.snapshot(VALIDATION_SCOPE, task_id)
    assert snapshot["status"] == "succeeded"
    assert snapshot["nodes"][0]["attempt"] == 1
    assert [(item["attempt"], item["status"]) for item in snapshot["attempts"]] == [
        (1, "succeeded"),
        (2, "unknown"),
    ]
