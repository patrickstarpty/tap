"""Worker-emitted SSE events must validate against the strict chat stream contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.chat.application.conversations import ClaimBatch


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
        return ClaimBatch(
            (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        attempt=1,
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
        )

    async def resolve_citations(self, *_args):
        return ()


class _KnowledgeConversations:
    def __init__(self) -> None:
        self.repository = _KnowledgeRepository()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.completed: list[object] = []

    async def emit(self, *args, **kwargs):
        pass

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
    """The relation analysis subgraph's `STALE`/`EMPTY`
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
                "graphContext": {
                    "status": status,
                    "graphVersion": "7",
                    "seedCount": 2,
                    "querySeedCount": 0,
                    "paths": [],
                    "relationCount": 0,
                },
            }
        },
    )
    ChatEventEnvelope.model_validate(envelope)


@pytest.mark.parametrize(
    "insights_facts",
    [
        None,
        [
            {
                "label": "I1",
                "queryId": "query-1",
                "metricId": "first_pass_rate",
                "value": 0.92,
                "asOf": "2026-10-01",
            }
        ],
    ],
    ids=["none", "cited"],
)
def test_turn_completed_streams_the_http_answer_dump_with_insights_facts(
    insights_facts: list[dict[str, object]] | None,
) -> None:
    """The worker persists `turn.completed` as the HTTP `RetrievalAnswerResponse`
    dump, which always carries `insightsFacts` (PR 5 Task 8); the strict
    stream contract must accept it or every SSE replay of a terminal answer
    fails mid-body."""
    from tap.contracts.chat_stream import RetrievalAnswerResponse as StreamAnswer
    from tap.contracts.http import RetrievalAnswerResponse as HttpAnswer

    answer = HttpAnswer.model_validate(
        {
            "traceId": "trace-1",
            "queryPlanId": "plan-1",
            "contextSnapshotId": "context-1",
            "corpusVersion": "tapper-demo-v1",
            "retrievalProfileId": "quick-hybrid-v1",
            "degradedMode": False,
            "answer": "首次通过率为 92%。",
            "abstained": False,
            "claims": [],
            "citations": [],
            "insightsFacts": insights_facts,
        }
    ).model_dump(mode="json", by_alias=True)
    assert "insightsFacts" in answer

    envelope = ChatEventEnvelope.model_validate(
        _envelope(sequence=1, event_type="turn.completed", payload={"answer": answer})
    ).model_dump(mode="json", by_alias=True)

    assert envelope["event"]["payload"]["answer"]["insightsFacts"] == insights_facts
    assert set(StreamAnswer.model_json_schema(by_alias=True)["properties"]) == set(
        HttpAnswer.model_json_schema(by_alias=True)["properties"]
    )


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

    graph_context_payload = next(
        payload
        for event_type, payload in conversations.events
        if event_type == "graph.context_ready"
    )
    assert graph_context_payload["seedCount"] == 0
    assert graph_context_payload["querySeedCount"] == 0


class _EarlyHitsConversations(_KnowledgeConversations):
    """Separately records every `emit` call (the early, pre-`finalize`
    commit path) alongside `complete_evidence`'s terminal batch, so a test
    can see the two independently and in order."""

    def __init__(self) -> None:
        super().__init__()
        self.emitted: list[tuple[str, dict[str, object]]] = []

    async def emit(self, conversation_id, turn_id, event_type, payload, *, lease_token=None):
        self.emitted.append((event_type, payload))


class _EarlyHitsKnowledge:
    """Implements the full resumable `ConversationAnswerSteps` protocol so
    `prepare_conversation_answer` can hand `seed_search` a `retrieval_hits`
    payload (with `labels`) the way the real `KnowledgeHttpService` does."""

    def __init__(self) -> None:
        self.hits_ready_committed_before_generate = False

    async def prepare_conversation_answer(self, request, frozen_input, **_kwargs):
        return {
            "kind": "prepared",
            "bundle": {},
            "retrieval_hits": {
                "traceId": "trace-1",
                "authorizedHitCount": 1,
                "labels": [{"label": "S1", "title": "doc-a", "location": None}],
            },
        }

    async def generate_conversation_answer(self, step, frozen_input, *, on_text=None, **_kwargs):
        return {"kind": "generated", "bundle": step["bundle"], "generation": {}}

    async def verify_conversation_answer(self, step, frozen_input, **_kwargs):
        return {"kind": "verified", "response": {}}

    async def finalize_conversation_answer(self, step, frozen_input, **_kwargs):
        return SimpleNamespace(
            answer="grounded",
            citations=(),
            abstained=False,
            trace_id="trace-1",
            model_dump=lambda **_: {"answer": "grounded", "citations": []},
        )

    async def refuse_out_of_scope(self, frozen_input, **_kwargs):  # pragma: no cover - unused
        raise NotImplementedError


@pytest.mark.asyncio
async def test_retrieval_hits_ready_with_labels_is_committed_before_generation() -> None:
    """M-3: `retrieval.hits_ready` (with `labels`) must be committed through
    `conversations.emit` before `generate_conversation_answer` ever runs,
    and that committed payload must itself validate against
    `ChatEventEnvelope` -- the strict chat stream contract, not just the
    plain dict `KnowledgeHttpService` builds it from."""
    conversations = _EarlyHitsConversations()
    knowledge = _EarlyHitsKnowledge()

    async def generate_conversation_answer(step, frozen_input, *, on_text=None, **_kwargs):
        knowledge.hits_ready_committed_before_generate = any(
            event_type == "retrieval.hits_ready" for event_type, _payload in conversations.emitted
        )
        return {"kind": "generated", "bundle": step["bundle"], "generation": {}}

    knowledge.generate_conversation_answer = generate_conversation_answer
    worker = GenerationWorker(conversations, knowledge, checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1

    assert knowledge.hits_ready_committed_before_generate is True
    assert [event_type for event_type, _payload in conversations.emitted] == [
        "retrieval.hits_ready",
        "stage.started",
    ]
    event_type, payload = conversations.emitted[0]
    assert event_type == "retrieval.hits_ready"
    assert payload["labels"] == [{"label": "S1", "title": "doc-a", "location": None}]
    ChatEventEnvelope.model_validate(_envelope(sequence=1, event_type=event_type, payload=payload))
    # The terminal batch must not repeat the already-committed event.
    assert "retrieval.hits_ready" not in [
        event_type for event_type, _payload in conversations.events
    ]


class _InsightsRepository:
    def __init__(self, *, resolved_resources) -> None:
        self._resolved_resources = resolved_resources

    async def claim_queued(self, *, limit):
        return ClaimBatch(
            (
                (
                    "conversation-1",
                    SimpleNamespace(
                        turn_id="turn-1",
                        lease_token="lease-1",
                        attempt=1,
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


_EDGE_CITATION_PAYLOAD = {
    "citationId": "citation-r1",
    "evidenceLabel": "R1",
    "chunkId": "chunk-1",
    "logicalChunkId": "chunk-1",
    "source": {
        "sourceId": "source-1",
        "sourceType": "doc",
        "revisionKind": "blob_version",
        "revision": "rev-1",
        "sourceContentHash": "sha256:" + "a" * 64,
        "anchor": {"type": "document", "page": 1},
    },
    "chunkContentHash": "sha256:" + "b" * 64,
    "contentRole": "source",
    "derivedFromChunkIds": None,
    "publicationId": None,
    "approvalDigest": None,
    "approvedItemId": None,
    "kind": "edge",
    "edge": {
        "edgeId": "edge-1",
        "graphVersion": "7",
        "subject": {"nodeId": "node-1", "label": "核保流程"},
        "object": {"nodeId": "node-2", "label": "健康告知"},
        "relationType": "requires",
        "relationLabel": "需要",
    },
}

_GRAPH_CONTEXT_PAYLOAD = {
    "status": "APPLIED",
    "graphVersion": "7",
    "seedCount": 2,
    "querySeedCount": 2,
    "paths": [["核保流程", "健康告知"]],
    "relationCount": 1,
}


@pytest.mark.asyncio
async def test_graph_context_ready_event_precedes_answer_and_citation_kind_is_emitted() -> None:
    """The relation analysis subgraph's readiness must stream as
    `graph.context_ready`, and an edge citation must carry `kind == "edge"`."""

    class _KnowledgeWithGraphContext:
        async def answer(self, request):
            edge_citation = SimpleNamespace(
                citation_id="citation-r1",
                model_dump=lambda **_: dict(_EDGE_CITATION_PAYLOAD),
            )
            graph_context = SimpleNamespace(
                model_dump=lambda **_: dict(_GRAPH_CONTEXT_PAYLOAD),
            )
            return SimpleNamespace(
                answer="grounded",
                citations=[edge_citation],
                abstained=False,
                trace_id="trace-1",
                graph_context_status="APPLIED",
                graph_snapshot_id="7",
                graph_context=graph_context,
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
                    "graphContextStatus": "APPLIED",
                    "graphSnapshotId": "7",
                },
            )

    conversations = _KnowledgeConversations()
    worker = GenerationWorker(
        conversations, _KnowledgeWithGraphContext(), checkpointer=InMemorySaver()
    )
    assert await worker.run_once(limit=1) == 1

    event_types = [event_type for event_type, _payload in conversations.events]
    assert event_types == [
        "context.assembled",
        "stage.completed",
        "retrieval.hits_ready",
        "graph.context_ready",
        "citation.resolved",
    ]

    for index, (event_type, payload) in enumerate(conversations.events, start=1):
        ChatEventEnvelope.model_validate(
            _envelope(sequence=index, event_type=event_type, payload=payload)
        )

    graph_context_ready_payload = next(
        payload
        for event_type, payload in conversations.events
        if event_type == "graph.context_ready"
    )
    assert graph_context_ready_payload == _GRAPH_CONTEXT_PAYLOAD

    last_citation_resolved = [
        payload for event_type, payload in conversations.events if event_type == "citation.resolved"
    ][-1]
    assert last_citation_resolved["citation"]["kind"] == "edge"


def test_answer_draft_event_schema_and_no_answer_delta() -> None:
    """PR 3 Task 6: `answer.draft {attempt, seq, text}` replaces the retired
    `answer.delta` event; the envelope still validates with `sequence` set to
    the most recent authoritative sequence (drafts never advance it)."""
    envelope = _envelope(
        sequence=3,
        event_type="answer.draft",
        payload={"attempt": 1, "seq": 0, "text": "Evidence shows"},
    )
    validated = ChatEventEnvelope.model_validate(envelope)
    assert validated.event.type == "answer.draft"
    assert validated.event.payload.attempt == 1
    assert validated.event.payload.seq == 0
    assert validated.event.payload.text == "Evidence shows"

    with pytest.raises(ValueError):
        ChatEventEnvelope.model_validate(
            _envelope(sequence=1, event_type="answer.delta", payload={"text": "stale"})
        )


def test_turn_canceled_accepts_partial_answer() -> None:
    """`turn.canceled` gained an optional `partialAnswer` alongside the
    existing `partialAnswerRetained`, so a canceled Turn's SSE frame can
    carry the stopped draft text without it entering the citation system."""
    envelope = _envelope(
        sequence=2,
        event_type="turn.canceled",
        payload={"partialAnswerRetained": True, "partialAnswer": "Stopped mid"},
    )
    validated = ChatEventEnvelope.model_validate(envelope)
    assert validated.event.payload.partial_answer_retained is True
    assert validated.event.payload.partial_answer == "Stopped mid"

    without_partial = ChatEventEnvelope.model_validate(
        _envelope(
            sequence=2,
            event_type="turn.canceled",
            payload={"partialAnswerRetained": False},
        )
    )
    assert without_partial.event.payload.partial_answer is None
