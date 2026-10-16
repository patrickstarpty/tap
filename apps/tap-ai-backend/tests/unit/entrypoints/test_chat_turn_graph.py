"""The chat generation worker runs on `tapper-chat-v2` (`ChatGraph`), not the
old single-`execute`-node `InteractionGraph`; behavior (events, payloads,
outcomes) must be unchanged."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
import sqlalchemy.exc
from langgraph.checkpoint.memory import InMemorySaver

from tap.entrypoints.tapper_chat_turn import ChatTurnNodes
from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.interaction_graph import InteractionGraph
from tap.modules.ai.domain.graph_runs import GraphCheckpointRetryable
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.knowledge.application.evidence_bundle import EvidenceBundleTooLarge
from tap.modules.knowledge.ports.answers import AnswerSnapshotUnavailable
from tap.platform.budget import TurnBudget, current_meter


def _turn(*, turn_id: str = "turn-1", lease_token: str = "lease-1", attempt: int = 1):
    return SimpleNamespace(
        turn_id=turn_id,
        lease_token=lease_token,
        attempt=attempt,
        input_snapshot=SimpleNamespace(
            snapshot_id="snapshot-1",
            value=SimpleNamespace(message="question", resolved_resources=()),
        ),
    )


class _Repository:
    def __init__(self, turn) -> None:
        self._turn = turn

    async def claim_queued(self, *, limit):
        return (("conversation-1", self._turn),)


class _Conversations:
    def __init__(self, turn) -> None:
        self.repository = _Repository(turn)
        self.completed: list[tuple] = []

    async def complete_evidence(self, conversation_id, turn_id, evidence, **kwargs):
        self.completed.append((conversation_id, turn_id, evidence, kwargs))


def _answer(*, answer: str = "grounded") -> SimpleNamespace:
    return SimpleNamespace(
        answer=answer,
        citations=(),
        abstained=False,
        trace_id="trace-1",
        model_dump=lambda **_: {"answer": answer, "citations": []},
    )


class _StepsKnowledge:
    """Implements the full resumable `ConversationAnswerSteps` protocol."""

    def __init__(self, *, fail_generate_once: bool = False) -> None:
        self.prepare_calls = 0
        self.generate_calls = 0
        self._generate_failed_once = not fail_generate_once
        self._prepare_error: Exception | None = None

    async def prepare_conversation_answer(
        self, request, frozen_input, *, answer_plan=None, authorize=None
    ):
        self.prepare_calls += 1
        if self._prepare_error is not None:
            raise self._prepare_error
        return {"kind": "prepared", "bundle": {}}

    async def generate_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ):
        self.generate_calls += 1
        if not self._generate_failed_once:
            self._generate_failed_once = True
            raise GraphCheckpointRetryable("connection lost after durable task write")
        return {"kind": "generated", "bundle": step["bundle"], "generation": {}}

    async def verify_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ):
        return {"kind": "verified", "response": {}}

    async def finalize_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ):
        return _answer()


@pytest.mark.asyncio
async def test_model_failure_resumes_without_repeating_retrieval() -> None:
    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = _StepsKnowledge(fail_generate_once=True)
    checkpointer = InMemorySaver()
    worker = GenerationWorker(conversations, knowledge, checkpointer=checkpointer)

    assert await worker.run_once(limit=1) == 1
    assert conversations.completed == []
    assert knowledge.generate_calls == 1

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, _kwargs = conversations.completed[0]
    assert evidence.outcome == "completed"
    assert knowledge.prepare_calls == 1
    assert knowledge.generate_calls == 2


@pytest.mark.asyncio
async def test_legacy_fast_chat_checkpoint_restarts_on_new_graph() -> None:
    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = _StepsKnowledge()
    checkpointer = InMemorySaver()

    async def authorized() -> bool:
        return True

    async def classify(_state):
        return {"reasoning_mode": "direct"}

    async def admit(_state):
        return {"admitted": True}

    async def execute(_state):
        return {"result": {"evidence": "stale"}}

    await InteractionGraph(
        graph_version="fast-chat-v1",
        state_schema_version=1,
        checkpointer=checkpointer,
        classify=classify,
        admit=admit,
        execute=execute,
        authorize=authorized,
    ).start(run_id=turn.turn_id, payload={"turnId": turn.turn_id}, execution_mode="inline")

    worker = GenerationWorker(conversations, knowledge, checkpointer=checkpointer)
    assert await worker.run_once(limit=1) == 1

    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "completed"
    assert kwargs["terminal_event"][0] == "turn.completed"
    assert [event_type for event_type, _payload in kwargs["stream_events"]] == [
        "context.assembled",
        "stage.completed",
        "retrieval.hits_ready",
        "graph.context_ready",
        "answer.delta",
    ]
    assert knowledge.prepare_calls == 1


@pytest.mark.asyncio
async def test_oversized_bundle_fails_turn() -> None:
    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = _StepsKnowledge()
    knowledge._prepare_error = EvidenceBundleTooLarge("evidence bundle is too large")
    worker = GenerationWorker(conversations, knowledge, checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "failed"
    event_type, payload = kwargs["terminal_event"]
    assert event_type == "turn.failed"
    assert payload["problem"]["type"].endswith("answer-unavailable")


@pytest.mark.asyncio
async def test_slow_prepare_is_reflected_in_stage_completed_duration() -> None:
    """`stage.completed`'s `durationMs` must cover retrieval (the `prepare`
    phase), not just the time after it: on the `SingleShotAnswerSteps` path,
    `prepare` makes the whole answer call, so a slow `prepare` must show up
    as a correspondingly large `durationMs`, matching the old code, which
    timed the entire `TurnProcessor.process` call."""

    class SlowSingleShotKnowledge:
        async def answer(self, _request):
            await asyncio.sleep(0.05)
            return _answer()

    turn = _turn()
    conversations = _Conversations(turn)
    worker = GenerationWorker(
        conversations, SlowSingleShotKnowledge(), checkpointer=InMemorySaver()
    )

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "completed"
    stage_payload = next(
        payload
        for event_type, payload in kwargs["stream_events"]
        if event_type == "stage.completed"
    )
    assert stage_payload["durationMs"] >= 40


@pytest.mark.asyncio
async def test_single_shot_knowledge_still_answers() -> None:
    class SingleShotKnowledge:
        def __init__(self) -> None:
            self.calls = 0

        async def answer(self, _request):
            self.calls += 1
            return _answer()

    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = SingleShotKnowledge()
    worker = GenerationWorker(conversations, knowledge, checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "completed"
    assert kwargs["terminal_event"][0] == "turn.completed"
    assert [event_type for event_type, _payload in kwargs["stream_events"]] == [
        "context.assembled",
        "stage.completed",
        "retrieval.hits_ready",
        "graph.context_ready",
        "answer.delta",
    ]
    assert knowledge.calls == 1


def _digest(label: str) -> str:
    return "sha256:" + label.rjust(64, "0")


def _plan(*, deadline_at: float, remaining_seconds: float) -> AnswerPlan:
    return AnswerPlan(
        plan_id="plan-1",
        project_id="project-1",
        turn_id="turn-1",
        input_digest=_digest("1"),
        acl_digest=_digest("2"),
        policy_digest=_digest("3"),
        model_alias="gpt-test",
        original_question="hello",
        intent="general_chat",
        route="direct",
        confidence=0.9,
        standalone_query="hello",
        constraints=(),
        missing=(),
        queries=(),
        source_ids=(),
        template_id="template-1",
        template_version="v1",
        template_digest=_digest("4"),
        deadline_at=deadline_at,
        remaining_seconds=remaining_seconds,
    )


def _nodes(*, turn, attempt_started_at: float | None) -> ChatTurnNodes:
    conversations = _Conversations(turn)

    async def require_authorized() -> None:
        return None

    return ChatTurnNodes(
        conversation_id="conversation-1",
        turn=turn,
        conversations=conversations,
        steps=_StepsKnowledge(),
        planner=object(),
        knowledge=object(),
        insights_explanation=None,
        require_authorized=require_authorized,
        attempt_started_at=attempt_started_at,
    )


def test_new_attempt_rebases_plan_deadline() -> None:
    plan = _plan(deadline_at=time.time() - 60, remaining_seconds=30)
    turn = _turn(attempt=2)
    attempt_started_at = time.time()
    nodes = _nodes(turn=turn, attempt_started_at=attempt_started_at)

    state = {"answer_plan": plan.to_dict(), "plan_attempt": 1}
    active_plan = nodes._active_plan(state)

    assert active_plan is not None
    assert active_plan.deadline_at >= attempt_started_at + 30 - 0.01


def test_same_attempt_keeps_persisted_plan_deadline() -> None:
    deadline_at = time.time() - 60
    plan = _plan(deadline_at=deadline_at, remaining_seconds=30)
    turn = _turn(attempt=1)
    attempt_started_at = time.time()
    nodes = _nodes(turn=turn, attempt_started_at=attempt_started_at)

    state = {"answer_plan": plan.to_dict(), "plan_attempt": 1}
    active_plan = nodes._active_plan(state)

    assert active_plan is not None
    assert active_plan.deadline_at == deadline_at


class _DeferCapturingRepository(_Repository):
    def __init__(self, turn) -> None:
        super().__init__(turn)
        self.defers: list[tuple] = []

    async def defer_turn(self, conversation_id, turn_id, lease_token, *, next_attempt_at, failure):
        self.defers.append((conversation_id, turn_id, lease_token, next_attempt_at, failure))


class _DeferCapturingConversations(_Conversations):
    def __init__(self, turn) -> None:
        super().__init__(turn)
        self.repository = _DeferCapturingRepository(turn)


@pytest.mark.asyncio
async def test_transient_finalize_failure_defers_and_resumes_without_terminal_event() -> None:
    """A transient fault inside the knowledge finalize step (e.g. the answer
    snapshot store is briefly unavailable) must escape `TurnProcessor`
    instead of being swallowed into a permanent `turn.failed`: the worker
    defers the Turn and the next attempt resumes `finalize` from the
    checkpoint without repeating `generate`/`verify`."""

    class FlakyFinalizeKnowledge(_StepsKnowledge):
        def __init__(self) -> None:
            super().__init__()
            self.finalize_calls = 0

        async def finalize_conversation_answer(
            self, step, frozen_input, *, answer_plan=None, authorize=None
        ):
            self.finalize_calls += 1
            if self.finalize_calls == 1:
                raise AnswerSnapshotUnavailable("snapshot store unavailable")
            return _answer()

    turn = _turn()
    conversations = _DeferCapturingConversations(turn)
    knowledge = FlakyFinalizeKnowledge()
    checkpointer = InMemorySaver()
    worker = GenerationWorker(conversations, knowledge, checkpointer=checkpointer)

    assert await worker.run_once(limit=1) == 1
    assert conversations.completed == []
    defers = conversations.repository.defers
    assert len(defers) == 1
    assert defers[0][4] == "transient:AnswerSnapshotUnavailable"
    assert knowledge.generate_calls == 1

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, _kwargs = conversations.completed[0]
    assert evidence.outcome == "completed"
    assert knowledge.prepare_calls == 1
    assert knowledge.generate_calls == 1
    assert knowledge.finalize_calls == 2


@pytest.mark.asyncio
async def test_transient_post_graph_reauthorization_failure_defers() -> None:
    """`authorize_completed_answer` wraps every exception from its checkpoint
    reauthorization recheck as `AuthorizationDenied`; when the wrapped cause
    is itself transient (e.g. the policy store's `OperationalError`), the
    Turn must defer and retry the recheck, not fail permanently."""

    class ReauthorizingKnowledge(_StepsKnowledge):
        def __init__(self) -> None:
            super().__init__()
            self.authorize_calls = 0

        async def authorize_completed_answer(self, _snapshot, _evidence) -> None:
            self.authorize_calls += 1
            if self.authorize_calls == 1:
                raise AuthorizationDenied("checkpoint selection authorization unavailable") from (
                    sqlalchemy.exc.OperationalError("x", None, Exception())
                )

    turn = _turn()
    conversations = _DeferCapturingConversations(turn)
    knowledge = ReauthorizingKnowledge()
    checkpointer = InMemorySaver()
    worker = GenerationWorker(conversations, knowledge, checkpointer=checkpointer)

    assert await worker.run_once(limit=1) == 1
    assert conversations.completed == []
    defers = conversations.repository.defers
    assert len(defers) == 1
    assert defers[0][4] == "transient:AuthorizationDenied"

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, _kwargs = conversations.completed[0]
    assert evidence.outcome == "completed"
    assert knowledge.authorize_calls == 2


@pytest.mark.asyncio
async def test_plain_authorization_denial_after_graph_fails_turn() -> None:
    """A plain `AuthorizationDenied` (no wrapped transient cause) -- a real
    denial, e.g. the frozen selection changed -- must still fail the Turn
    immediately; only a transient-caused denial defers."""

    class DenyingKnowledge(_StepsKnowledge):
        async def authorize_completed_answer(self, _snapshot, _evidence) -> None:
            raise AuthorizationDenied("frozen revision selection changed")

    turn = _turn()
    conversations = _DeferCapturingConversations(turn)
    knowledge = DenyingKnowledge()
    checkpointer = InMemorySaver()
    worker = GenerationWorker(conversations, knowledge, checkpointer=checkpointer)

    assert await worker.run_once(limit=1) == 1
    assert conversations.repository.defers == []
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "failed"
    assert kwargs["terminal_event"][0] == "turn.failed"


class _BudgetSpendingKnowledge(_StepsKnowledge):
    """No `answer_planner` (so `guard_in` stays a no-op); `prepare_conversation_answer`
    (the `seed_search` node's body) spends the turn's one allowed model call, so the
    following `model` node must then hit the exhausted model-call budget before its
    own body runs."""

    async def prepare_conversation_answer(
        self, request, frozen_input, *, answer_plan=None, authorize=None
    ):
        meter = current_meter()
        assert meter is not None
        meter.before_call()
        meter.after_call(0)
        return await super().prepare_conversation_answer(
            request, frozen_input, answer_plan=answer_plan, authorize=authorize
        )


@pytest.mark.asyncio
async def test_budget_exhausted_turn_emits_budget_problem() -> None:
    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = _BudgetSpendingKnowledge()
    worker = GenerationWorker(
        conversations,
        knowledge,
        checkpointer=InMemorySaver(),
        turn_budget=TurnBudget(max_model_calls=1),
    )

    assert await worker.run_once(limit=1) == 1

    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "failed"
    event_type, payload = kwargs["terminal_event"]
    assert event_type == "turn.failed"
    assert payload["problem"]["type"].endswith("budget-exhausted")
