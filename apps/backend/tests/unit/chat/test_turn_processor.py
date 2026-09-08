from types import SimpleNamespace

import pytest

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.conversations import GraphContextStatus


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
                        input_snapshot=SimpleNamespace(value=SimpleNamespace(message="question")),
                    ),
                ),
            )

    class Conversations:
        repository = Repository()
        events = []
        completed = []

        async def emit(self, conversation_id, turn_id, event_type, payload):
            self.events.append((conversation_id, turn_id, event_type, payload))

        async def complete_evidence(self, conversation_id, turn_id, evidence):
            self.completed.append((conversation_id, turn_id, evidence))

    class Knowledge:
        async def answer(self, request):
            return SimpleNamespace(
                answer="grounded", citations=[], abstained=False, trace_id="trace-1"
            )

    conversations = Conversations()
    assert await GenerationWorker(conversations, Knowledge()).run_once(limit=1) == 1
    assert conversations.events == [
        ("conversation-1", "turn-1", "answer.delta", {"text": "grounded"})
    ]
    assert conversations.completed[0][2].outcome == "completed"
