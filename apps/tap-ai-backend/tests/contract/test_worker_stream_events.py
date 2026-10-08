"""Worker-emitted SSE events must validate against the strict chat stream contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.entrypoints.tapper_generation_worker import GenerationWorker


def _envelope(*, sequence: int, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "eventId": f"event-{sequence}",
        "sequence": sequence,
        "chatId": "conversation-1",
        "turnId": "turn-1",
        "occurredAt": "2026-09-30T00:00:00Z",
        "schemaVersion": 1,
        "event": {"type": event_type, "payload": payload},
    }


class _KnowledgeRepository:
    async def claim_queued(self, *, limit):
        return (
            (
                "conversation-1",
                SimpleNamespace(
                    turn_id="turn-1",
                    lease_token="lease-1",
                    input_snapshot=SimpleNamespace(
                        snapshot_id="snapshot-1",
                        value=SimpleNamespace(
                            message="question",
                            resolved_resources=(),
                        ),
                    ),
                ),
            ),
        )

    async def resolve_citations(self, *_args):
        return ()


class _KnowledgeConversations:
    def __init__(self) -> None:
        self.repository = _KnowledgeRepository()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.completed: list[object] = []

    async def complete_evidence(self, conversation_id, turn_id, evidence, *, stream_events=(), **_):
        self.events.extend(stream_events)
        self.completed.append(evidence)


class _Knowledge:
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


@pytest.mark.parametrize("status", ["STALE", "EMPTY"])
def test_turn_completed_accepts_stale_and_empty_graph_context_status(status: str) -> None:
    """PR 3 Task 5 review I4: the relation analysis subgraph's `STALE`/`EMPTY`
    outcomes must be streamable, not just the HTTP answer contract."""
    envelope = _envelope(
        sequence=1,
        event_type="turn.completed",
        payload={
            "answer": {
                "traceId": "trace-1",
                "queryPlanId": "plan-1",
                "contextSnapshotId": "context-1",
                "corpusVersion": "tapper-demo-v1",
                "retrievalProfileId": "quick-hybrid-v1",
                "degradedMode": False,
                "answer": "grounded",
                "abstained": False,
                "abstentionReason": None,
                "claims": [],
                "citations": [],
                "graphContextStatus": status,
                "graphSnapshotId": None,
            }
        },
    )
    ChatEventEnvelope.model_validate(envelope)


@pytest.mark.asyncio
async def test_knowledge_events_validate_against_contract() -> None:
    conversations = _KnowledgeConversations()
    worker = GenerationWorker(conversations, _Knowledge(), checkpointer=InMemorySaver())
    assert await worker.run_once(limit=1) == 1

    assert conversations.events, "knowledge path must emit stream events"
    for index, (event_type, payload) in enumerate(conversations.events, start=1):
        ChatEventEnvelope.model_validate(
            _envelope(sequence=index, event_type=event_type, payload=payload)
        )

    stage_events = [
        payload for event_type, payload in conversations.events if event_type == "stage.completed"
    ]
    assert stage_events == [
        {"stage": "knowledge.answer", "durationMs": stage_events[0]["durationMs"]}
    ]
    assert isinstance(stage_events[0]["durationMs"], int)
    assert stage_events[0]["durationMs"] >= 0


class _InsightsRepository:
    def __init__(self, *, resolved_resources) -> None:
        self._resolved_resources = resolved_resources

    async def claim_queued(self, *, limit):
        return (
            (
                "conversation-1",
                SimpleNamespace(
                    turn_id="turn-1",
                    lease_token="lease-1",
                    input_snapshot=SimpleNamespace(
                        snapshot_id="snapshot-1",
                        value=SimpleNamespace(
                            message="Explain the timeout",
                            insights_query_id="query-1",
                            insights_report_refs=("report-1",),
                            resolved_resources=self._resolved_resources,
                        ),
                    ),
                ),
            ),
        )

    async def resolve_citations(self, *_args):
        return ()


class _InsightsConversations:
    def __init__(self, *, resolved_resources=()) -> None:
        self.repository = _InsightsRepository(resolved_resources=resolved_resources)
        self.scope = None
        self.events: list[tuple[str, dict[str, object]]] = []
        self.completed: list[object] = []
        self.terminal_events: list[tuple[str, dict[str, object]]] = []

    async def complete_evidence(
        self, conversation_id, turn_id, evidence, *, stream_events=(), terminal_event=None, **_
    ):
        self.events.extend(stream_events)
        self.completed.append(evidence)
        if terminal_event is not None:
            self.terminal_events.append(terminal_event)


class _NoOrdinaryAnswer:
    async def answer(self, _request):
        pytest.fail("Insights must not fall through to ordinary Knowledge Chat")


class _Explanation:
    def __init__(self, *, knowledge_search_performed: bool) -> None:
        self._knowledge_search_performed = knowledge_search_performed

    async def explain(self, scope, query_id, resource_refs, question, **binding):
        return {
            "queryId": query_id,
            "metricVersion": "metrics-v1",
            "asOf": "2026-09-27T00:00:00Z",
            "facts": [],
            "reportCoverage": [],
            "hypotheses": ["hypothesis [report-1]"],
            "evidenceExcerpts": [{"citationId": "report-1", "text": "excerpt"}],
            "missingInformation": [],
            "stopReason": "completed",
            "knowledgeSearchPerformed": self._knowledge_search_performed,
        }

    async def reauthorize_result(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_insights_events_validate_against_contract() -> None:
    conversations = _InsightsConversations()
    worker = GenerationWorker(
        conversations,
        _NoOrdinaryAnswer(),
        checkpointer=InMemorySaver(),
        insights_explanation=_Explanation(knowledge_search_performed=False),
    )
    assert await worker.run_once(limit=1) == 1

    assert conversations.events, "insights path must emit stream events"
    for index, (event_type, payload) in enumerate(conversations.events, start=1):
        ChatEventEnvelope.model_validate(
            _envelope(sequence=index, event_type=event_type, payload=payload)
        )

    for event_type, payload in conversations.terminal_events:
        ChatEventEnvelope.model_validate(
            _envelope(
                sequence=len(conversations.events) + 1, event_type=event_type, payload=payload
            )
        )

    stage_events = [
        payload for event_type, payload in conversations.events if event_type == "stage.completed"
    ]
    assert [item["stage"] for item in stage_events] == ["insights.explanation"]
    for item in stage_events:
        assert "graphRunId" not in item
        assert "conversationId" not in item
        assert "tool" not in item
        assert "queryId" not in item


@pytest.mark.asyncio
async def test_insights_knowledge_search_events_validate_against_contract(span_recorder) -> None:
    resolved_resources = (
        SimpleNamespace(
            source_id="src_" + "1" * 32,
            revision_id="revision-1",
            source_content_hash="sha256:" + "a" * 64,
        ),
    )
    conversations = _InsightsConversations(resolved_resources=resolved_resources)
    worker = GenerationWorker(
        conversations,
        _NoOrdinaryAnswer(),
        checkpointer=InMemorySaver(),
        insights_explanation=_Explanation(knowledge_search_performed=True),
    )
    assert await worker.run_once(limit=1) == 1

    assert conversations.events, "insights knowledge-search path must emit stream events"
    for index, (event_type, payload) in enumerate(conversations.events, start=1):
        ChatEventEnvelope.model_validate(
            _envelope(sequence=index, event_type=event_type, payload=payload)
        )
    for event_type, payload in conversations.terminal_events:
        ChatEventEnvelope.model_validate(
            _envelope(
                sequence=len(conversations.events) + 1, event_type=event_type, payload=payload
            )
        )

    stage_events = [
        payload for event_type, payload in conversations.events if event_type == "stage.completed"
    ]
    assert [item["stage"] for item in stage_events] == ["insights.explanation", "knowledge.search"]
    knowledge_search_payload = stage_events[1]
    assert set(knowledge_search_payload) == {"stage", "durationMs"}
    assert isinstance(knowledge_search_payload["durationMs"], int)
    assert knowledge_search_payload["durationMs"] >= 0

    audit_spans = [
        recorded_span
        for recorded_span in span_recorder.get_finished_spans()
        if recorded_span.name == "turn.execute"
        and "tap.retrieval.source_ids" in recorded_span.attributes
    ]
    assert len(audit_spans) == 1
    assert list(audit_spans[0].attributes["tap.retrieval.source_ids"]) == [
        item.source_id for item in resolved_resources
    ]


@pytest.mark.asyncio
async def test_hits_ready_omitted_without_trace_id() -> None:
    class Knowledge:
        async def answer(self, request):
            return SimpleNamespace(
                answer="grounded",
                citations=[],
                abstained=False,
                trace_id=None,
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

    conversations = _KnowledgeConversations()
    worker = GenerationWorker(conversations, Knowledge(), checkpointer=InMemorySaver())
    assert await worker.run_once(limit=1) == 1

    assert not any(
        event_type == "retrieval.hits_ready" for event_type, _payload in conversations.events
    )
