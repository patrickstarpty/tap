import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.ai.domain import graph_runs
from tap.modules.ai.domain.graph_runs import GraphCheckpointUnavailable
from tap.modules.chat.application.conversations import ConversationConflict
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.conversations import CitationEvidence, GraphContextStatus
from tap.modules.chat.domain.suggestions import RefreshReason


def test_graph_snapshot_is_legal_only_when_graph_context_was_applied():
    with pytest.raises(ValueError, match="graph snapshot"):
        ProviderResult(
            answer="x", graph_context_status=GraphContextStatus.UNAVAILABLE, graph_snapshot_id="g"
        )
    with pytest.raises(ValueError, match="requires"):
        ProviderResult(answer="x", graph_context_status=GraphContextStatus.APPLIED)


@pytest.mark.asyncio
async def test_failure_produces_closed_answer_evidence_and_unknown_provider_events_are_redacted():
    async def provider(_snapshot):
        raise RuntimeError("credential sk-secret should never escape")

    captured = []
    processor = TurnProcessor(provider=provider, complete=captured.append)
    result = await processor.process(
        object(), provider_events=[{"type": "future", "token": "secret"}]
    )
    assert result.outcome == "failed"
    assert result.retrieval_summary.status == "failed"
    assert result.graph_context_status is GraphContextStatus.FAILED
    assert result.citations == ()
    assert result.diagnostics == ("unknown-provider-event",)
    assert "secret" not in repr(result)


@pytest.mark.asyncio
async def test_generation_worker_emits_recoverable_delta_then_closes_the_turn():
    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=SimpleNamespace(message="question")),
                    ),
                ),
            )

    class Conversations:
        repository = Repository()
        events = []
        completed = []

        async def complete_evidence(
            self, conversation_id, turn_id, evidence, *, stream_events=(), **_
        ):
            self.events.extend(
                (conversation_id, turn_id, event_type, payload)
                for event_type, payload in stream_events
            )
            self.completed.append((conversation_id, turn_id, evidence))

    class Knowledge:
        requests = []

        async def answer(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                answer="grounded",
                citations=[],
                abstained=False,
                trace_id="trace-1",
                model_dump=lambda **_: {
                    "traceId": "trace-1",
                    "queryPlanId": "plan-1",
                    "contextSnapshotId": "context-1",
                    "corpusVersion": "v1",
                    "retrievalProfileId": "quick",
                    "degradedMode": False,
                    "answer": "grounded",
                    "abstained": False,
                    "abstentionReason": None,
                    "claims": [],
                    "citations": [],
                },
            )

    conversations = Conversations()
    knowledge = Knowledge()
    turn = conversations.repository.claim_queued
    original = await turn(limit=1)
    value = original[0][1].input_snapshot.value
    value.source_revision_ids = ("revision-1",)
    value.resolved_resources = (
        SimpleNamespace(source_id="src_" + "1" * 32, revision_id="revision-1"),
    )
    conversations.repository.claim_queued = lambda **_: _async_value(original)
    checkpointer = InMemorySaver()
    assert (
        await GenerationWorker(conversations, knowledge, checkpointer=checkpointer).run_once(
            limit=1
        )
        == 1
    )
    checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": "turn-1"}})
    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["graph_version"] == "fast-chat-v1"
    assert knowledge.requests[0].resource_refs[0].source_id == "src_" + "1" * 32
    assert knowledge.requests[0].resource_refs[0].mode.value == "scope"
    assert conversations.events == [
        ("conversation-1", "turn-1", "context.assembled", {"sourceCount": 1}),
        (
            "conversation-1",
            "turn-1",
            "stage.completed",
            {"stage": "knowledge.answer", "outcome": "completed"},
        ),
        ("conversation-1", "turn-1", "retrieval.hits_ready", {"authorizedHitCount": 0}),
        ("conversation-1", "turn-1", "answer.delta", {"text": "grounded"}),
    ]
    assert conversations.completed[0][2].outcome == "completed"


@pytest.mark.asyncio
async def test_generation_worker_fences_delta_with_the_claimed_lease():
    frozen = SimpleNamespace(
        message="question",
        model_alias="qwen-plus",
        resolved_resources=(SimpleNamespace(source_id="src_" + "1" * 32),),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=frozen),
                    ),
                ),
            )

        async def resolve_citations(self, *_args):
            return ()

    class Conversations:
        repository = Repository()
        completion = None

        async def complete_evidence(self, *_args, **kwargs):
            self.completion = kwargs

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="delta",
                citations=(),
                abstained=False,
                trace_id="t",
                model_dump=lambda **_: {
                    "traceId": "t",
                    "queryPlanId": "p",
                    "contextSnapshotId": "c",
                    "corpusVersion": "v1",
                    "retrievalProfileId": "quick",
                    "degradedMode": False,
                    "answer": "delta",
                    "abstained": False,
                    "claims": [],
                    "citations": [],
                },
            )

    conversations = Conversations()
    await GenerationWorker(conversations, Knowledge()).run_once(limit=1)
    assert conversations.completion["lease_token"] == "lease-1"
    assert conversations.completion["stream_events"][-1] == (
        "answer.delta",
        {"text": "delta"},
    )


class _FakeSuggestionStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, RefreshReason]] = []
        self._fail = fail

    async def request_refresh_for_actor(self, actor_id, reason, *, now):
        if self._fail:
            raise RuntimeError("suggestion store unavailable")
        self.calls.append((actor_id, reason))
        return 1


def _completed_turn_fixtures(*, abstained: bool):  # type: ignore[no-untyped-def]
    frozen = SimpleNamespace(message="question", actor_id="actor-1", resolved_resources=())

    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=frozen),
                    ),
                ),
            )

        async def resolve_citations(self, *_args):
            return ()

    class Conversations:
        repository = Repository()
        completion = None

        async def complete_evidence(self, *_args, **kwargs):
            self.completion = kwargs

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="",
                citations=(),
                abstained=abstained,
                trace_id="t",
                model_dump=lambda **_: {
                    "traceId": "t",
                    "queryPlanId": "p",
                    "contextSnapshotId": "c",
                    "corpusVersion": "v1",
                    "retrievalProfileId": "quick",
                    "degradedMode": False,
                    "answer": "",
                    "abstained": abstained,
                    "claims": [],
                    "citations": [],
                },
            )

    return Conversations(), Knowledge()


@pytest.mark.asyncio
async def test_completed_turn_requests_actor_refresh():
    conversations, knowledge = _completed_turn_fixtures(abstained=False)
    suggestions = _FakeSuggestionStore()

    await GenerationWorker(conversations, knowledge, suggestion_refresh=suggestions).run_once(
        limit=1
    )

    assert suggestions.calls == [("actor-1", RefreshReason.TURN_COMPLETED)]


@pytest.mark.asyncio
async def test_abstained_turn_does_not_request_actor_refresh():
    conversations, knowledge = _completed_turn_fixtures(abstained=True)
    suggestions = _FakeSuggestionStore()

    await GenerationWorker(conversations, knowledge, suggestion_refresh=suggestions).run_once(
        limit=1
    )

    assert suggestions.calls == []


@pytest.mark.asyncio
async def test_refresh_request_failure_does_not_fail_the_turn(caplog):
    conversations, knowledge = _completed_turn_fixtures(abstained=False)
    suggestions = _FakeSuggestionStore(fail=True)

    with caplog.at_level("WARNING"):
        processed = await GenerationWorker(
            conversations, knowledge, suggestion_refresh=suggestions
        ).run_once(limit=1)

    assert processed == 1
    assert conversations.completion is not None
    assert conversations.completion["lease_token"] == "lease-1"
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "prompt suggestion refresh request failed" in warnings[0].message
    assert "RuntimeError" in warnings[0].message
    assert "actor-1" not in warnings[0].message
    assert "question" not in warnings[0].message


@pytest.mark.asyncio
async def test_generation_worker_emits_public_failure_and_closes_evidence():
    frozen = SimpleNamespace(message="question", resolved_resources=())

    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=frozen),
                    ),
                ),
            )

    class Conversations:
        repository = Repository()
        emitted = []
        completed = []

        async def emit(self, *args, **kwargs):
            self.emitted.append((args, kwargs))

        async def complete_evidence(self, *args, **kwargs):
            self.completed.append((args, kwargs))

    class Knowledge:
        async def answer(self, _request):
            raise RuntimeError("provider credential must stay private")

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=1) == 1
    assert conversations.emitted == []
    completion_args, completion_kwargs = conversations.completed[0]
    assert completion_args[2].outcome == "failed"
    event_type, payload = completion_kwargs["terminal_event"]
    assert event_type == "turn.failed"
    ChatEventEnvelope.model_validate(
        {
            "eventId": "event-1",
            "sequence": 1,
            "chatId": "conversation-1",
            "turnId": "turn-1",
            "occurredAt": "2026-09-09T00:00:00Z",
            "schemaVersion": 1,
            "event": {"type": event_type, "payload": payload},
        }
    )


@pytest.mark.asyncio
async def test_generation_worker_continues_after_cancel_wins_completion_race():
    turns = tuple(
        (
            "conversation-1",
            SimpleNamespace(
                turn_id=f"turn-{number}",
                attempt=1,
                lease_token=f"lease-{number}",
                input_snapshot=SimpleNamespace(
                    value=SimpleNamespace(message="question", resolved_resources=())
                ),
            ),
        )
        for number in (1, 2)
    )

    class Repository:
        async def claim_queued(self, *, limit):
            return turns

    class Conversations:
        repository = Repository()
        completed = []

        async def complete_evidence(self, _conversation_id, turn_id, *_args, **_kwargs):
            if turn_id == "turn-1":
                raise ConversationConflict("generation lease lost")
            self.completed.append(turn_id)

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=2) == 2
    assert conversations.completed == ["turn-2"]


@pytest.mark.asyncio
async def test_generation_worker_commits_terminal_stream_event_with_evidence_atomically():
    frozen = SimpleNamespace(message="question", resolved_resources=())

    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(value=frozen),
                    ),
                ),
            )

    class Conversations:
        repository = Repository()
        stream_events = None
        terminal_event = None

        async def complete_evidence(self, *_args, terminal_event=None, stream_events=(), **_kwargs):
            self.terminal_event = terminal_event
            self.stream_events = stream_events

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    conversations = Conversations()
    await GenerationWorker(conversations, Knowledge()).run_once(limit=1)
    assert [event_type for event_type, _payload in conversations.stream_events] == [
        "context.assembled",
        "stage.completed",
        "retrieval.hits_ready",
        "answer.delta",
    ]
    assert conversations.terminal_event == (
        "turn.completed",
        {"answer": {"answer": "grounded", "citations": []}},
    )


@pytest.mark.asyncio
async def test_generation_worker_renews_turn_lease_while_provider_is_running():
    renewed = asyncio.Event()

    class Repository:
        renewals = 0

        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(
                            value=SimpleNamespace(message="question", resolved_resources=())
                        ),
                    ),
                ),
            )

        async def renew_processing_lease(
            self, conversation_id, turn_id, lease_token, *, lease_duration
        ):
            assert conversation_id == "conversation-1"
            assert turn_id == "turn-1"
            assert lease_token == "lease-1"
            assert lease_duration == timedelta(milliseconds=100)
            self.renewals += 1
            if self.renewals >= 4:
                renewed.set()

    class Conversations:
        repository = Repository()

        async def emit(self, *_args, **_kwargs):
            pass

        async def complete_evidence(self, *_args, **_kwargs):
            pass

    class Knowledge:
        async def answer(self, _request):
            await asyncio.wait_for(renewed.wait(), timeout=1)
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    worker = GenerationWorker(
        Conversations(),
        Knowledge(),
        lease_duration=timedelta(milliseconds=100),
        renew_interval_seconds=0.01,
    )

    assert await worker.run_once(limit=1) == 1
    assert renewed.is_set()
    assert worker.conversations.repository.renewals >= 4


@pytest.mark.asyncio
async def test_generation_worker_uses_turn_scoped_persistent_checkpointer_factory():
    checkpointer = InMemorySaver()

    class Repository:
        checkpoint_claims = []

        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(
                            value=SimpleNamespace(message="question", resolved_resources=())
                        ),
                    ),
                ),
            )

        def graph_checkpointer(self, turn):
            self.checkpoint_claims.append((turn.turn_id, turn.lease_token))
            return checkpointer

    class Conversations:
        repository = Repository()

        async def emit(self, *_args, **_kwargs):
            pass

        async def complete_evidence(self, *_args, **_kwargs):
            pass

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=1) == 1
    assert conversations.repository.checkpoint_claims == [("turn-1", "lease-1")]


@pytest.mark.asyncio
async def test_generation_worker_recovers_after_one_transient_checkpoint_failure():
    class RetryOnceCheckpointer(InMemorySaver):
        failed = False

        async def aput_writes(self, config, writes, task_id, task_path=""):
            if not self.failed and any(channel == "result" for channel, _value in writes):
                self.failed = True
                raise graph_runs.GraphCheckpointRetryable("checkpoint database unavailable")
            return await super().aput_writes(config, writes, task_id, task_path)

    checkpointer = RetryOnceCheckpointer()

    class Repository:
        claims = 0

        async def claim_queued(self, *, limit):
            assert limit == 1
            self.claims += 1
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        attempt=self.claims,
                        lease_token=f"lease-{self.claims}",
                        input_snapshot=SimpleNamespace(
                            value=SimpleNamespace(message="question", resolved_resources=())
                        ),
                    ),
                ),
            )

        def graph_checkpointer(self, _turn):
            return checkpointer

    class Conversations:
        repository = Repository()
        completed = []
        emitted = []

        async def emit(self, _conversation_id, _turn_id, event_type, payload, **_kwargs):
            self.emitted.append((event_type, payload))

        async def complete_evidence(self, _conversation_id, turn_id, evidence, **kwargs):
            self.completed.append((turn_id, evidence.outcome, kwargs["stream_events"]))

    class Knowledge:
        calls = 0

        async def answer(self, _request):
            self.calls += 1
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    conversations = Conversations()
    knowledge = Knowledge()
    worker = GenerationWorker(conversations, knowledge)

    assert await worker.run_once(limit=1) == 1
    assert conversations.completed == []
    assert conversations.emitted == []
    assert await worker.run_once(limit=1) == 1
    assert [event[0] for event in conversations.completed[0][2]] == [
        "context.assembled",
        "stage.completed",
        "retrieval.hits_ready",
        "answer.delta",
    ]
    assert conversations.completed[0][:2] == ("turn-1", "completed")
    assert conversations.emitted == []
    assert knowledge.calls == 1


@pytest.mark.asyncio
async def test_generation_worker_terminalizes_permanent_checkpoint_error_and_continues_batch():
    class PermanentlyUnavailableCheckpointer(InMemorySaver):
        async def aget_tuple(self, config):
            if config["configurable"]["thread_id"] == "turn-1":
                raise GraphCheckpointUnavailable("checkpoint data is invalid")
            return await super().aget_tuple(config)

    turns = tuple(
        (
            "conversation-1",
            SimpleNamespace(
                turn_id=f"turn-{number}",
                attempt=1,
                lease_token=f"lease-{number}",
                input_snapshot=SimpleNamespace(
                    value=SimpleNamespace(message="question", resolved_resources=())
                ),
            ),
        )
        for number in (1, 2)
    )

    class Repository:
        async def claim_queued(self, *, limit):
            assert limit == 2
            return turns

        def graph_checkpointer(self, turn):
            return PermanentlyUnavailableCheckpointer()

    class Conversations:
        repository = Repository()
        completed = []

        async def emit(self, *_args, **_kwargs):
            pass

        async def complete_evidence(
            self, _conversation_id, turn_id, evidence, *, terminal_event, **_kwargs
        ):
            self.completed.append((turn_id, evidence.outcome, terminal_event[0]))

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=2) == 2
    assert conversations.completed == [
        ("turn-1", "failed", "turn.failed"),
        ("turn-2", "completed", "turn.completed"),
    ]


@pytest.mark.asyncio
async def test_generation_worker_terminalizes_transient_checkpoint_failure_at_retry_budget():
    class RetryableFailureCheckpointer(InMemorySaver):
        async def aget_tuple(self, config):
            del config
            raise graph_runs.GraphCheckpointRetryable("checkpoint database unavailable")

    turn = SimpleNamespace(
        turn_id="turn-3",
        attempt=3,
        lease_token="lease-3",
        input_snapshot=SimpleNamespace(
            value=SimpleNamespace(message="question", resolved_resources=())
        ),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            assert limit == 1
            return (("conversation-1", turn),)

        def graph_checkpointer(self, _turn):
            return RetryableFailureCheckpointer()

    class Conversations:
        repository = Repository()
        completed = []

        async def complete_evidence(self, _conversation_id, turn_id, evidence, **kwargs):
            self.completed.append((turn_id, evidence.outcome, kwargs["terminal_event"][0]))

    class Knowledge:
        async def answer(self, _request):
            raise AssertionError("provider must not run after checkpoint preflight failure")

    conversations = Conversations()
    worker = GenerationWorker(conversations, Knowledge(), max_checkpoint_attempts=3)

    assert await worker.run_once(limit=1) == 1
    assert conversations.completed == [("turn-3", "failed", "turn.failed")]


@pytest.mark.asyncio
async def test_generation_worker_respects_lease_loss_during_checkpoint_failure_settlement():
    class PermanentFailureCheckpointer(InMemorySaver):
        async def aget_tuple(self, config):
            del config
            raise GraphCheckpointUnavailable("checkpoint schema is invalid")

    turn = SimpleNamespace(
        turn_id="turn-expired",
        attempt=1,
        lease_token="expired-lease",
        input_snapshot=SimpleNamespace(
            value=SimpleNamespace(message="question", resolved_resources=())
        ),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            assert limit == 1
            return (("conversation-1", turn),)

        def graph_checkpointer(self, _turn):
            return PermanentFailureCheckpointer()

    class Conversations:
        repository = Repository()
        settlements = 0

        async def complete_evidence(self, *_args, **_kwargs):
            self.settlements += 1
            raise ConversationConflict("generation lease lost")

    class Knowledge:
        async def answer(self, _request):
            raise AssertionError("provider must not run after checkpoint preflight failure")

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=1) == 1
    assert conversations.settlements == 1


@pytest.mark.asyncio
async def test_generation_worker_bounds_checkpoint_settlement_conflict_at_attempt_budget():
    turn = SimpleNamespace(
        turn_id="turn-conflict",
        attempt=3,
        lease_token="lease-3",
        input_snapshot=SimpleNamespace(
            value=SimpleNamespace(message="question", resolved_resources=())
        ),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            assert limit == 1
            return (("conversation-1", turn),)

    class Conversations:
        repository = Repository()
        attempts = []

        async def complete_evidence(self, _conversation_id, _turn_id, evidence, **_kwargs):
            self.attempts.append(evidence.outcome)
            if evidence.outcome == "completed":
                raise ConversationConflict("graph lease takeover was not settled")

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=1) == 1
    assert conversations.attempts == ["completed", "failed"]


@pytest.mark.asyncio
async def test_generation_worker_cannot_fail_a_checkpoint_conflict_after_losing_lease():
    turn = SimpleNamespace(
        turn_id="turn-loser",
        attempt=3,
        lease_token="stale-lease",
        input_snapshot=SimpleNamespace(
            value=SimpleNamespace(message="question", resolved_resources=())
        ),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            return (("conversation-1", turn),)

    class Conversations:
        repository = Repository()
        attempts = []

        async def complete_evidence(self, _conversation_id, _turn_id, evidence, **_kwargs):
            self.attempts.append(evidence.outcome)
            raise ConversationConflict("generation lease lost")

    class Knowledge:
        async def answer(self, _request):
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=1) == 1
    assert conversations.attempts == ["completed", "failed"]


@pytest.mark.asyncio
async def test_generation_worker_rechecks_turn_lease_before_provider_call():
    class Repository:
        validations = 0

        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=SimpleNamespace(
                            value=SimpleNamespace(message="question", resolved_resources=())
                        ),
                    ),
                ),
            )

        async def renew_processing_lease(self, *_args, **_kwargs):
            self.validations += 1
            if self.validations >= 3:
                raise ConversationConflict("generation lease lost")

    class Conversations:
        repository = Repository()

        async def emit(self, *_args, **_kwargs):
            pass

        async def complete_evidence(self, *_args, **_kwargs):
            pass

    class Knowledge:
        calls = 0

        async def answer(self, _request):
            self.calls += 1
            raise AssertionError("stale worker must not call the provider")

    knowledge = Knowledge()
    assert await GenerationWorker(Conversations(), knowledge).run_once(limit=1) == 1
    assert knowledge.calls == 0


async def _async_value(value):
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings_values,expected_corpus",
    [({}, "tapper-demo-v2"), ({"TAPPER_SCHEMA_VERSION": "doc-schema-v1"}, "tapper-demo-v1")],
)
async def test_runtime_corpus_reaches_worker_frozen_answer_policy(settings_values, expected_corpus):
    from datetime import datetime, timezone

    from tap.entrypoints.tapper_runtime import (
        TapperSettings,
        _assemble_http_services,
        _create_embeddings,
    )
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.application.assets import validation_asset_seed
    from tap.modules.chat.domain.conversations import (
        FrozenResource,
        TurnInput,
        TurnInputSnapshot,
        content_digest,
    )
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
    from tests.object_settings import S3_SETTINGS
    from tests.unit.knowledge import test_answer_service as fixtures

    settings = TapperSettings.from_mapping(S3_SETTINGS | settings_values)
    composed = _assemble_http_services(
        repository=SimpleNamespace(scope=VALIDATION_SCOPE),
        artifacts=object(),
        search=object(),
        embeddings=_create_embeddings(settings),
        readiness=object(),
        redactor=object(),
        scope_provider=object(),
        authorization_policy=object(),
        corpus_version=settings.corpus_version,
    )
    assert composed.knowledge._corpus_version == expected_corpus
    assert composed.knowledge._answers._corpus_version == expected_corpus

    response = replace(fixtures.answer_response(), corpus_version=expected_corpus)
    repository = fixtures.MemoryAnswerRepository((fixtures.ready(),))
    gateway = fixtures.Gateway(response)
    answers = fixtures.AnswerService(
        repository=repository, knowledge=gateway, corpus_version=expected_corpus
    )
    repository.scope = VALIDATION_SCOPE

    class Citations:
        scope = VALIDATION_SCOPE

        async def authorize_current(self, citation_id):
            assert citation_id == "citation-a"

    knowledge = KnowledgeHttpService(
        documents=SimpleNamespace(scope=VALIDATION_SCOPE),
        answers=answers,
        citations=Citations(),
        corpus_version=settings.corpus_version,
    )
    frozen = fixtures.ready()
    policy = build_demo_policy_context((frozen,), corpus_version=expected_corpus)
    assets = validation_asset_seed(VALIDATION_SCOPE)
    agent = assets.agents[0]
    skill = assets.skills[0]
    turn_input = TurnInput(
        message="What is the rule?",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        source_revision_ids=(frozen.revision_id,),
        resolved_resources=(
            FrozenResource(
                frozen.source_id,
                frozen.document_id,
                frozen.revision_id,
                frozen.source_content_hash,
            ),
        ),
        agent_revision_id=agent.revision_id,
        agent_revision_digest=agent.content_digest,
        skill_revision_ids=(skill.revision_id,),
        skill_revision_digests=(skill.content_digest,),
        agent_system_instruction=agent.system_instruction,
        agent_system_instruction_digest=agent.system_instruction_digest,
        agent_tool_allowlist=tuple(sorted(agent.tool_allowlist)),
        agent_output_schema_json=agent.output_schema_json,
        agent_output_schema_digest=agent.output_schema_digest,
        skill_instruction_templates=(skill.instruction_template,),
        skill_instruction_template_digests=(skill.instruction_template_digest,),
        acl_digest=policy.acl_digest,
        retrieval_policy_digest=content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        ),
    )
    snapshot = TurnInputSnapshot.create(
        snapshot_id="snapshot-1",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn-1",
        value=turn_input,
        now=datetime.now(timezone.utc),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            return (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        input_snapshot=snapshot,
                    ),
                ),
            )

        async def resolve_citations(self, trace_id, citation_ids):
            assert trace_id == "trace-a"
            return tuple(
                CitationEvidence(identity, "sha256:" + "d" * 64) for identity in citation_ids
            )

    class Conversations:
        repository = Repository()

        async def emit(self, *_args, **_kwargs):
            pass

        async def complete_evidence(self, *_args, **_kwargs):
            pass

    assert await GenerationWorker(Conversations(), knowledge).run_once(limit=1) == 1
    assert gateway.requests[0].resource_refs[0].source_id == frozen.source_id
    assert repository.snapshots[0].selected_revisions == (frozen,)


@pytest.mark.asyncio
async def test_generation_worker_skips_a_turn_whose_conversation_was_deleted():
    from tap.modules.chat.application.conversations import ConversationNotFound

    turn = SimpleNamespace(
        turn_id="turn-1",
        lease_token="lease-1",
        input_snapshot=SimpleNamespace(
            value=SimpleNamespace(
                message="question",
                source_revision_ids=("revision-1",),
                resolved_resources=(
                    SimpleNamespace(source_id="src_" + "1" * 32, revision_id="revision-1"),
                ),
            )
        ),
    )

    class Repository:
        async def claim_queued(self, *, limit):
            return (("conversation-1", turn),)

    class Conversations:
        repository = Repository()

        async def complete_evidence(self, *_args, **_kwargs):
            raise ConversationNotFound

    class Knowledge:
        async def answer(self, request):
            return SimpleNamespace(
                answer="grounded",
                citations=[],
                abstained=False,
                trace_id="trace-1",
                model_dump=lambda **_: {
                    "traceId": "trace-1",
                    "queryPlanId": "plan-1",
                    "contextSnapshotId": "context-1",
                    "corpusVersion": "v1",
                    "retrievalProfileId": "quick",
                    "degradedMode": False,
                    "answer": "grounded",
                    "abstained": False,
                    "abstentionReason": None,
                    "claims": [],
                    "citations": [],
                },
            )

    worker = GenerationWorker(Conversations(), Knowledge(), checkpointer=InMemorySaver())
    assert await worker.run_once(limit=1) == 1
