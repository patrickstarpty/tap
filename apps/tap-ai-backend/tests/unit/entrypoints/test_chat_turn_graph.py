"""The chat generation worker runs on `tapper-chat-v2` (`ChatGraph`), not the
old single-`execute`-node `InteractionGraph`; behavior (events, payloads,
outcomes) must be unchanged."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy.exc
from langgraph.checkpoint.memory import InMemorySaver

from tap.entrypoints.tapper_chat_turn import ChatTurnNodes
from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.interaction_graph import InteractionGraph
from tap.modules.ai.domain.graph_runs import GraphCheckpointRetryable
from tap.modules.chat.application.conversations import ClaimBatch
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.chat.domain.conversations import TurnInput, TurnInputSnapshot
from tap.modules.knowledge.application.evidence_bundle import EvidenceBundleTooLarge
from tap.modules.knowledge.ports.answers import AnswerSnapshotUnavailable
from tap.modules.knowledge.ports.redaction import RedactionUnavailable
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
        return ClaimBatch((("conversation-1", self._turn),))


class _Conversations:
    def __init__(self, turn) -> None:
        self.repository = _Repository(turn)
        self.completed: list[tuple] = []
        self.emitted: list[tuple] = []

    async def complete_evidence(self, conversation_id, turn_id, evidence, **kwargs):
        self.completed.append((conversation_id, turn_id, evidence, kwargs))

    async def emit(self, conversation_id, turn_id, event_type, payload, *, lease_token=None):
        self.emitted.append((conversation_id, turn_id, event_type, payload, lease_token))
        return SimpleNamespace(sequence=len(self.emitted))


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
        self, step, frozen_input, *, answer_plan=None, authorize=None, on_text=None
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

    async def refuse_out_of_scope(self, frozen_input, *, answer_plan):
        self.refuse_calls = getattr(self, "refuse_calls", 0) + 1
        return SimpleNamespace(
            answer="",
            citations=(),
            abstained=True,
            trace_id=answer_plan.plan_id,
            model_dump=lambda **_: {
                "answer": "",
                "citations": [],
                "abstained": True,
                "abstentionReason": "out_of_scope",
            },
        )


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
async def test_output_redaction_failure_fails_turn() -> None:
    """`RedactionUnavailable` from `finalize_conversation_answer`'s output
    redaction step must propagate out of the graph instead of being
    swallowed, and -- being fail-closed and non-retryable -- must land the
    turn on `turn.failed`, the same terminal shape as any other
    deterministic answer failure (`test_oversized_bundle_fails_turn`)."""
    from tap.modules.knowledge.ports.redaction import RedactionUnavailable

    class _FinalizeRedactionFailsKnowledge(_StepsKnowledge):
        async def finalize_conversation_answer(
            self, step, frozen_input, *, answer_plan=None, authorize=None
        ):
            raise RedactionUnavailable("output redaction rejected the final answer")

    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = _FinalizeRedactionFailsKnowledge()
    worker = GenerationWorker(conversations, knowledge, checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "failed"
    event_type, _payload = kwargs["terminal_event"]
    assert event_type == "turn.failed"


class _RecordingSpan:
    """A minimal `set_attribute`-recording double standing in for the real
    OTEL span `guard_in` writes `tap.guard.redaction.*` attributes onto."""

    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


def _guard_in_nodes(*, knowledge: object, turn_span: _RecordingSpan) -> ChatTurnNodes:
    turn = _turn()
    conversations = _Conversations(turn)

    async def require_authorized() -> None:
        return None

    return ChatTurnNodes(
        conversation_id="conversation-1",
        turn=turn,
        conversations=conversations,
        steps=_StepsKnowledge(),
        planner=None,
        knowledge=knowledge,
        insights_explanation=None,
        require_authorized=require_authorized,
        turn_span=turn_span,
    )


@pytest.mark.asyncio
async def test_guard_in_records_input_redaction_hits() -> None:
    class _ReportingKnowledge:
        async def redaction_report(self, _text: str) -> dict[str, int]:
            return {"phone": 1}

    span = _RecordingSpan()
    nodes = _guard_in_nodes(knowledge=_ReportingKnowledge(), turn_span=span)

    result = await nodes.guard_in({})

    assert result == {"route": "chat", "reasoning_mode": "direct"}
    assert span.attributes["tap.guard.redaction.input_hits"] == json.dumps({"phone": 1})
    assert "tap.guard.redaction.input_hits_unavailable" not in span.attributes


@pytest.mark.asyncio
async def test_guard_in_input_redaction_failure_does_not_fail_turn() -> None:
    """A message the redactor cannot parse (over its bound, an unterminated
    credential-looking value, ...) on *any* route must never fail the turn
    over a trace-only attribute: `guard_in` swallows the exception and
    records `tap.guard.redaction.input_hits_unavailable` instead."""

    class _FailingKnowledge:
        async def redaction_report(self, _text: str) -> dict[str, int]:
            raise RuntimeError("redactor exploded")

    span = _RecordingSpan()
    nodes = _guard_in_nodes(knowledge=_FailingKnowledge(), turn_span=span)

    result = await nodes.guard_in({})

    assert result == {"route": "chat", "reasoning_mode": "direct"}
    assert span.attributes.get("tap.guard.redaction.input_hits_unavailable") is True
    assert "tap.guard.redaction.input_hits" not in span.attributes


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


def test_rebased_plan_deadline_is_capped_at_the_turn_wall_clock_end() -> None:
    """I2: a fresh attempt's rebased plan deadline (normally
    `attempt_started_at + remaining_seconds`, e.g. +30s) must never reach
    past the turn's own `budget.startedAtMs/1000 + budget.maxSeconds` --
    otherwise a turn whose 180s budget is nearly exhausted would still hand
    an answer phase a full fresh 30s, making the turn budget's own
    wall-clock exhaustion effectively unreachable."""
    plan = _plan(deadline_at=time.time() - 60, remaining_seconds=30)
    turn = _turn(attempt=2)
    attempt_started_at = time.time()
    turn_started_at = attempt_started_at - 175
    nodes = _nodes(turn=turn, attempt_started_at=attempt_started_at)

    state = {
        "answer_plan": plan.to_dict(),
        "plan_attempt": 1,
        "budget": {"startedAtMs": int(turn_started_at * 1000), "maxSeconds": 180},
    }
    active_plan = nodes._active_plan(state)

    assert active_plan is not None
    turn_end = turn_started_at + 180
    assert active_plan.deadline_at <= turn_end + 0.01
    assert active_plan.deadline_at < attempt_started_at + 30 - 0.01


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


class _PlanDeadlineExpiredKnowledge(_StepsKnowledge):
    """`generate_conversation_answer` raises `AnswerBudgetExhausted` the way
    `KnowledgeHttpService._plan_deadline` does when the plan's persisted
    deadline has already passed at phase start -- a plan-deadline exhaustion
    found at the very start of a phase, not a mid-phase timeout."""

    async def generate_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None, on_text=None
    ):
        from tap.interfaces.http.knowledge_service import AnswerBudgetExhausted

        raise AnswerBudgetExhausted("persisted answer budget exhausted")


@pytest.mark.asyncio
async def test_plan_deadline_expired_at_phase_start_emits_budget_problem() -> None:
    """I2: `AnswerBudgetExhausted` raised because the plan deadline had
    already passed at phase start must map to the same `budget-exhausted`
    problem as `TurnBudgetExhausted` -- not `answer-unavailable` -- since it
    genuinely means the turn ran out of budget, not an ordinary generation
    failure."""
    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = _PlanDeadlineExpiredKnowledge()
    worker = GenerationWorker(conversations, knowledge, checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1

    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "failed"
    event_type, payload = kwargs["terminal_event"]
    assert event_type == "turn.failed"
    assert payload["problem"]["type"].endswith("budget-exhausted")


def _frozen_turn_for_planning(
    *, turn_id: str = "turn-oos", message: str = "What's the weather today?", **turn_input_kwargs
):
    frozen = TurnInput(message, "actor-a", "validation", "qwen-plus", **turn_input_kwargs)
    snapshot = TurnInputSnapshot.create(
        snapshot_id=f"snapshot-{turn_id}",
        project_id="project-a",
        turn_id=turn_id,
        value=frozen,
        now=datetime.now(UTC),
    )
    return SimpleNamespace(
        turn_id=turn_id,
        lease_token=f"lease-{turn_id}",
        attempt=1,
        input_snapshot=snapshot,
    )


def _out_of_scope_plan() -> AnswerPlan:
    return AnswerPlan(
        plan_id="plan-oos",
        project_id="project-a",
        turn_id="turn-oos",
        input_digest=_digest("1"),
        acl_digest=_digest("2"),
        policy_digest=_digest("3"),
        model_alias="qwen-plus",
        original_question="What's the weather today?",
        intent="out_of_scope",
        route="direct",
        confidence=0.9,
        standalone_query="What's the weather today?",
        constraints=(),
        missing=(),
        queries=(),
        source_ids=(),
        template_id="template-1",
        template_version="v1",
        template_digest=_digest("4"),
        deadline_at=time.time() + 30,
        remaining_seconds=30,
    )


class _OutOfScopePlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, _value, *, previous_plan_id=None):
        self.calls += 1
        return _out_of_scope_plan()


@pytest.mark.asyncio
async def test_out_of_scope_turn_abstains_without_retrieval_or_generation() -> None:
    turn = _frozen_turn_for_planning()
    conversations = _Conversations(turn)
    knowledge = _StepsKnowledge()
    knowledge.answer_planner = _OutOfScopePlanner()
    worker = GenerationWorker(conversations, knowledge, checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1

    assert knowledge.prepare_calls == 0
    assert knowledge.generate_calls == 0
    assert knowledge.refuse_calls == 1
    assert knowledge.answer_planner.calls == 1
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, evidence, kwargs = conversations.completed[0]
    assert evidence.outcome == "abstained"
    terminal_type, terminal_payload = kwargs["terminal_event"]
    assert terminal_type == "turn.abstained"
    assert terminal_payload["answer"]["abstentionReason"] == "out_of_scope"
    stage_payload = next(
        payload
        for event_type, payload in kwargs["stream_events"]
        if event_type == "stage.completed"
    )
    assert stage_payload["durationMs"] < 60_000
    assert [event_type for event_type, _payload in kwargs["stream_events"]].count(
        "retrieval.hits_ready"
    ) == 0


@pytest.mark.asyncio
async def test_insights_route_never_out_of_scope() -> None:
    turn = _frozen_turn_for_planning(
        turn_id="turn-insights",
        insights_query_id="insights-1",
        insights_report_refs=("report-1",),
    )
    conversations = _Conversations(turn)
    planner = _OutOfScopePlanner()

    async def require_authorized() -> None:
        return None

    nodes = ChatTurnNodes(
        conversation_id="conversation-1",
        turn=turn,
        conversations=conversations,
        steps=_StepsKnowledge(),
        planner=planner,
        knowledge=object(),
        insights_explanation=None,
        require_authorized=require_authorized,
    )

    result = await nodes.guard_in({})

    assert result == {"route": "insights", "reasoning_mode": "workflow"}
    assert planner.calls == 0


class _HitsReadyKnowledge(_StepsKnowledge):
    """`prepare_conversation_answer` returns a `"prepared"` step carrying a
    `retrieval_hits` payload the way the real `KnowledgeHttpService` does;
    `generate_conversation_answer` asserts the `seed_search` node already
    committed it through `conversations.emit` before generation starts."""

    def __init__(self, *, conversations) -> None:
        super().__init__()
        self._conversations = conversations
        self.hits_ready_committed_before_generate = False

    async def prepare_conversation_answer(
        self, request, frozen_input, *, answer_plan=None, authorize=None
    ):
        self.prepare_calls += 1
        return {
            "kind": "prepared",
            "bundle": {},
            "retrieval_hits": {
                "traceId": "trace-1",
                "authorizedHitCount": 2,
                "labels": [{"label": "S1", "title": "doc-a", "location": None}],
            },
        }

    async def generate_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None, on_text=None
    ):
        self.generate_calls += 1
        self.hits_ready_committed_before_generate = any(
            event_type == "retrieval.hits_ready"
            for _c, _t, event_type, _payload, _lease in self._conversations.emitted
        )
        if on_text is not None:
            await on_text("grounded answer")
        return {"kind": "generated", "bundle": step["bundle"], "generation": {}}


@pytest.mark.asyncio
async def test_hits_ready_is_committed_before_generation_starts() -> None:
    turn = _turn()
    conversations = _Conversations(turn)
    knowledge = _HitsReadyKnowledge(conversations=conversations)
    worker = GenerationWorker(conversations, knowledge, checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1

    assert knowledge.hits_ready_committed_before_generate is True
    assert [event_type for *_rest, event_type, _payload, _lease in conversations.emitted] == [
        "retrieval.hits_ready",
        "stage.started",
    ]
    _c, _t, event_type, payload, lease_token = conversations.emitted[0]
    assert event_type == "retrieval.hits_ready"
    assert payload["traceId"] == "trace-1"
    assert payload["authorizedHitCount"] == 2
    assert lease_token == turn.lease_token
    # `finalize`'s own batch must not repeat an event already committed early.
    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, _evidence, kwargs = conversations.completed[0]
    assert [event_type for event_type, _payload in kwargs["stream_events"]].count(
        "retrieval.hits_ready"
    ) == 0


@pytest.mark.asyncio
async def test_retrieval_hits_ready_labels_are_redacted_before_emit() -> None:
    """M-6: `labels`' `title`/`location` come straight from document
    structure (never screened by the model), so they must still pass
    through the configured v2 chat-egress redactor before `retrieval.hits_ready`
    is emitted -- the same as every other model-bound/egress text."""

    class _LabeledKnowledge(_StepsKnowledge):
        async def prepare_conversation_answer(
            self, request, frozen_input, *, answer_plan=None, authorize=None
        ):
            self.prepare_calls += 1
            return {
                "kind": "prepared",
                "bundle": {},
                "retrieval_hits": {
                    "traceId": "trace-1",
                    "authorizedHitCount": 1,
                    "labels": [
                        {
                            "label": "S1",
                            "title": "contact leaker@example.com",
                            "location": "附录 / leaker@example.com",
                        }
                    ],
                },
            }

    async def _redact(text: str) -> str:
        return text.replace("leaker@example.com", "[REDACTED_EMAIL]")

    turn = _turn()
    conversations = _Conversations(turn)
    worker = GenerationWorker(
        conversations, _LabeledKnowledge(), checkpointer=InMemorySaver(), redact=_redact
    )

    assert await worker.run_once(limit=1) == 1

    _c, _t, event_type, payload, _lease = conversations.emitted[0]
    assert event_type == "retrieval.hits_ready"
    label = payload["labels"][0]
    assert "leaker@example.com" not in label["title"]
    assert "leaker@example.com" not in label["location"]
    assert label["title"] == "contact [REDACTED_EMAIL]"
    assert label["location"] == "附录 / [REDACTED_EMAIL]"


@pytest.mark.asyncio
async def test_retrieval_hits_ready_drops_a_label_whose_title_redaction_is_unavailable() -> None:
    """A label's `title`/`location` come straight from document structure
    and can look like a half-written credential or private-key block to the
    redactor (e.g. a heading literally titled "Account / Forgot password:"),
    raising `RedactionUnavailable`. These are display labels, not the
    authoritative answer -- that must never fail the Turn. A label whose
    `title` cannot be redacted is dropped entirely; one whose `location`
    (but not `title`) fails keeps its title and gets a `None` location."""

    class _LabeledKnowledge(_StepsKnowledge):
        async def prepare_conversation_answer(
            self, request, frozen_input, *, answer_plan=None, authorize=None
        ):
            self.prepare_calls += 1
            return {
                "kind": "prepared",
                "bundle": {},
                "retrieval_hits": {
                    "traceId": "trace-1",
                    "authorizedHitCount": 2,
                    "labels": [
                        {
                            "label": "S1",
                            "title": "Account / Forgot password:",
                            "location": "附录 1",
                        },
                        {
                            "label": "S2",
                            "title": "normal title",
                            "location": "Account / Forgot password:",
                        },
                    ],
                },
            }

    async def _redact(text: str) -> str:
        if "password" in text:
            raise RedactionUnavailable("half-written credential")
        return text

    turn = _turn()
    conversations = _Conversations(turn)
    worker = GenerationWorker(
        conversations, _LabeledKnowledge(), checkpointer=InMemorySaver(), redact=_redact
    )

    assert await worker.run_once(limit=1) == 1

    _c, _t, event_type, payload, _lease = conversations.emitted[0]
    assert event_type == "retrieval.hits_ready"
    labels = payload["labels"]
    # S1's title failed to redact -> the whole label is dropped.
    assert all(label["label"] != "S1" for label in labels)
    # S2's title redacted fine; its location failed -> location becomes None.
    s2 = next(label for label in labels if label["label"] == "S2")
    assert s2["title"] == "normal title"
    assert s2["location"] is None


@pytest.mark.asyncio
async def test_verify_stage_started_is_committed_before_verify_runs() -> None:
    turn = _turn()
    conversations = _Conversations(turn)

    class _VerifyOrderKnowledge(_StepsKnowledge):
        async def verify_conversation_answer(
            self, step, frozen_input, *, answer_plan=None, authorize=None
        ):
            assert any(
                event_type == "stage.started" and payload == {"stage": "verify"}
                for _c, _t, event_type, payload, _lease in conversations.emitted
            )
            return await super().verify_conversation_answer(
                step, frozen_input, answer_plan=answer_plan, authorize=authorize
            )

    worker = GenerationWorker(conversations, _VerifyOrderKnowledge(), checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1


@pytest.mark.asyncio
async def test_no_answer_delta_event_is_emitted() -> None:
    turn = _turn()
    conversations = _Conversations(turn)
    worker = GenerationWorker(conversations, _StepsKnowledge(), checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1

    assert len(conversations.completed) == 1
    _conversation_id, _turn_id, _evidence, kwargs = conversations.completed[0]
    event_types = [event_type for event_type, _payload in kwargs["stream_events"]]
    assert "answer.delta" not in event_types
    emitted_types = [event_type for *_rest, event_type, _payload, _lease in conversations.emitted]
    assert "answer.delta" not in emitted_types


@pytest.mark.asyncio
async def test_model_node_streams_through_draft_batcher() -> None:
    """The `model` node must feed every text chunk the knowledge gateway
    hands it (via `on_text`) into a `DraftBatcher` writing to the configured
    `DraftStream`, and redact it with the configured redactor."""
    from tap.modules.chat.application.ports import DraftChunk

    class _FakeDrafts:
        def __init__(self) -> None:
            self.appended: list[DraftChunk] = []

        async def append(self, turn_id, *, attempt, seq, text, reset=False):
            self.appended.append(DraftChunk(attempt, seq, text, reset))

        async def replay(self, turn_id, *, attempt):  # pragma: no cover - unused
            raise NotImplementedError

        async def read(self, keys, *, block_ms):  # pragma: no cover - unused
            raise NotImplementedError

        async def partial_text(self, turn_id):  # pragma: no cover - unused
            raise NotImplementedError

        async def request_cancel(self, turn_id):  # pragma: no cover - unused
            raise NotImplementedError

        async def cancel_requested(self, turn_id):
            # Task 7: the worker's node-boundary poll reads this before every
            # node; this scenario never cancels, so it is always `False`.
            return False

    class _StreamingKnowledge(_StepsKnowledge):
        async def generate_conversation_answer(
            self, step, frozen_input, *, answer_plan=None, authorize=None, on_text=None
        ):
            self.generate_calls += 1
            assert on_text is not None
            for chunk in ("hello ", "world"):
                await on_text(chunk)
            return {"kind": "generated", "bundle": step["bundle"], "generation": {}}

    redacted_calls: list[str] = []

    async def _recording_redact(text: str) -> str:
        redacted_calls.append(text)
        return text.upper()

    turn = _turn()
    conversations = _Conversations(turn)
    drafts = _FakeDrafts()
    worker = GenerationWorker(
        conversations,
        _StreamingKnowledge(),
        checkpointer=InMemorySaver(),
        drafts=drafts,
        redact=_recording_redact,
    )

    assert await worker.run_once(limit=1) == 1

    assert drafts.appended
    assert "".join(chunk.text for chunk in drafts.appended if not chunk.reset) == "HELLO WORLD"
    assert redacted_calls


def test_drafts_without_redact_fails_closed_at_construction() -> None:
    """M-7: a real `DraftStream` wired without the real redactor must never
    silently fall back to `_identity_redact` -- that would stream every
    draft to Redis completely unredacted. Fail closed at construction."""

    async def require_authorized() -> None:
        return None

    with pytest.raises(ValueError, match="redact"):
        ChatTurnNodes(
            conversation_id="conversation-1",
            turn=_turn(),
            conversations=_Conversations(_turn()),
            steps=_StepsKnowledge(),
            planner=None,
            knowledge=object(),
            insights_explanation=None,
            require_authorized=require_authorized,
            drafts=object(),
            redact=None,
        )
