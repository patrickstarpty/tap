"""Public SSE event models, deliberately separate from the HTTP DTO graph."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from pydantic.alias_generators import to_camel

from tap.contracts.problems import ProblemDetails


class StreamContractModel(BaseModel):
    """Base model for browser-visible stream payloads."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class SourceFamily(str, Enum):
    DOC = "doc"
    CODE = "code"
    BDD = "bdd"
    FAILURE = "failure"


class AnswerMode(str, Enum):
    QUICK = "quick"
    DEEP = "deep"


class RevisionKind(str, Enum):
    GIT_COMMIT = "git_commit"
    BLOB_VERSION = "blob_version"
    MYSQL_VERSION = "mysql_version"


class ContentRole(str, Enum):
    SOURCE = "source"
    GENERATED_SUMMARY = "generated_summary"


class AbstentionReason(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_SOURCES = "conflicting_sources"
    REVISION_MISMATCH = "revision_mismatch"
    OUT_OF_SCOPE = "out_of_scope"


class DocumentAnchor(StreamContractModel):
    type: Literal["document"]
    heading_path: list[str] | None = None
    page: int | None = None
    bbox: list[float] | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    inventory_item_id: str | None = Field(default=None, min_length=1, max_length=256)


class CodeAnchor(StreamContractModel):
    type: Literal["code"]
    repo: str = Field(min_length=1)
    path: str = Field(min_length=1)
    symbol: str | None = None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class BddAnchor(StreamContractModel):
    type: Literal["bdd"]
    feature_id: str = Field(min_length=1)
    scenario_id: str | None = None
    step_id: str | None = None


class OpenApiAnchor(StreamContractModel):
    type: Literal["openapi"]
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    json_pointer: str = Field(min_length=1)


class FailureAnchor(StreamContractModel):
    type: Literal["failure"]
    incident_id: str = Field(min_length=1)
    run_id: str | None = None
    time_start: str | None = None
    time_end: str | None = None


StructuralAnchorValue = Annotated[
    DocumentAnchor | CodeAnchor | BddAnchor | OpenApiAnchor | FailureAnchor,
    Field(discriminator="type"),
]


class StructuralAnchor(RootModel[StructuralAnchorValue]):
    """A closed source location retained in a browser-visible citation."""


class SourceRevisionRef(StreamContractModel):
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    revision_kind: RevisionKind
    revision: str = Field(min_length=1)
    source_content_hash: str = Field(min_length=1)
    anchor: StructuralAnchor


class GraphEndpointView(StreamContractModel):
    """One edge citation endpoint (subject or object)."""

    node_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1)


class EdgeCitationView(StreamContractModel):
    edge_id: str = Field(min_length=1, max_length=128)
    graph_version: str = Field(min_length=1, max_length=64)
    subject: GraphEndpointView
    object: GraphEndpointView
    relation_type: str = Field(min_length=1, max_length=64)
    relation_label: str = Field(min_length=1, max_length=64)


class Citation(StreamContractModel):
    citation_id: str = Field(min_length=1)
    evidence_label: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    logical_chunk_id: str = Field(min_length=1)
    source: SourceRevisionRef
    chunk_content_hash: str = Field(min_length=1)
    content_role: ContentRole
    derived_from_chunk_ids: list[str] | None = None
    publication_id: str | None = Field(default=None, min_length=1, max_length=256)
    approval_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    approved_item_id: str | None = Field(default=None, min_length=1, max_length=256)
    kind: Literal["chunk", "edge"] = "chunk"
    edge: EdgeCitationView | None = None

    @model_validator(mode="after")
    def validate_edge_kind(self):
        if (self.kind == "edge") != (self.edge is not None):
            raise ValueError("citation kind and edge payload must agree")
        return self


class AnswerClaim(StreamContractModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    answer_start: Annotated[int, Field(ge=0, strict=True)]
    answer_end: Annotated[int, Field(ge=0, strict=True)]
    citation_ids: list[str]

    @model_validator(mode="after")
    def ordered_answer_span(self):
        if self.answer_end < self.answer_start:
            raise ValueError("answer claim end must not precede start")
        return self


class GraphContextSummaryView(StreamContractModel):
    status: Literal["APPLIED", "NOT_READY", "STALE", "FAILED", "EMPTY"]
    graph_version: str | None = None
    seed_count: int = Field(ge=0)
    query_seed_count: int = Field(default=0, ge=0)
    paths: list[list[str]] = Field(default_factory=list)
    relation_count: int = Field(ge=0)


class InsightsFactView(StreamContractModel):
    """Stream mirror of the HTTP `InsightsFactView` (PR 5 Task 8): one cited
    `I<n>` Insights metric record, in citation order."""

    label: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    value: float | int | None = None
    as_of: str = Field(min_length=1)


class RetrievalAnswerResponse(StreamContractModel):
    trace_id: str = Field(min_length=1)
    query_plan_id: str = Field(min_length=1)
    context_snapshot_id: str = Field(min_length=1)
    corpus_version: str = Field(min_length=1)
    retrieval_profile_id: str = Field(min_length=1)
    degraded_mode: bool
    degradation_reasons: list[str] | None = None
    answer: str
    abstained: bool
    abstention_reason: AbstentionReason | None = None
    answer_kind: Literal["answer", "clarification"] = "answer"
    claims: list[AnswerClaim]
    citations: list[Citation]
    graph_context_status: Literal[
        "APPLIED", "NOT_READY", "STALE", "FAILED", "UNAVAILABLE", "NOT_SELECTED", "EMPTY"
    ] = "NOT_SELECTED"
    graph_snapshot_id: str | None = None
    graph_context: GraphContextSummaryView | None = None
    insights_facts: list[InsightsFactView] | None = None


class TurnStartedPayload(StreamContractModel):
    state: Literal["running"]


class TurnStartedEvent(StreamContractModel):
    type: Literal["turn.started"]
    payload: TurnStartedPayload


class ContextAssembledPayload(StreamContractModel):
    context_snapshot_id: str = Field(min_length=1)
    token_count: int = Field(ge=0)


class ContextAssembledEvent(StreamContractModel):
    type: Literal["context.assembled"]
    payload: ContextAssembledPayload


class QueryPlanReadyPayload(StreamContractModel):
    query_plan_id: str = Field(min_length=1)
    answer_mode: AnswerMode
    source_families: list[SourceFamily]


class QueryPlanReadyEvent(StreamContractModel):
    type: Literal["query.plan_ready"]
    payload: QueryPlanReadyPayload


class StageStartedPayload(StreamContractModel):
    stage: str = Field(min_length=1)


class StageStartedEvent(StreamContractModel):
    type: Literal["stage.started"]
    payload: StageStartedPayload


class StageCompletedPayload(StreamContractModel):
    stage: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)


class StageCompletedEvent(StreamContractModel):
    type: Literal["stage.completed"]
    payload: StageCompletedPayload


class RetrievalHitsLabelView(StreamContractModel):
    """One evidence/relation label's display mapping, so the client can
    render an inline `[S3]`/`[R1]` marker in a streaming draft as a badge
    showing its source before the terminal answer (and its own `citation`
    payloads) ever arrive (spec SS2.3)."""

    label: str = Field(min_length=1, max_length=4)
    title: str = Field(min_length=1)
    location: str | None = None


class RetrievalHitsReadyPayload(StreamContractModel):
    trace_id: str = Field(min_length=1)
    authorized_hit_count: int = Field(ge=0)
    labels: list[RetrievalHitsLabelView] | None = None
    # PR 5 (Task 9): set when this hit-count came from a `tools` graph-node
    # tool call (`knowledge.search`/`graph.relations`), so the client can
    # correlate it with the `task.step` that produced it; `None` for the
    # original `seed_search`-produced event.
    step_id: str | None = None


class RetrievalHitsReadyEvent(StreamContractModel):
    type: Literal["retrieval.hits_ready"]
    payload: RetrievalHitsReadyPayload


class TaskStepPayload(StreamContractModel):
    """One bounded tool-calling round's per-call progress (PR 5 Task 9):
    `status` moves `running` -> a terminal state (`completed`/`rejected`/
    `failed`); `step_id` is `f"{turnId}:r{round}:c{index}"`, unique per call."""

    step_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    status: Literal["running", "completed", "rejected", "failed"]
    summary: str = Field(max_length=200)
    duration_ms: int = Field(ge=0)
    tool_round: int = Field(ge=1)


class TaskStepEvent(StreamContractModel):
    type: Literal["task.step"]
    payload: TaskStepPayload


class GraphContextReadyPayload(StreamContractModel):
    status: Literal[
        "APPLIED", "NOT_READY", "STALE", "FAILED", "EMPTY", "UNAVAILABLE", "NOT_SELECTED"
    ]
    graph_version: str | None = None
    seed_count: int = Field(ge=0)
    query_seed_count: int = Field(default=0, ge=0)
    paths: list[list[str]] = Field(max_length=3)
    relation_count: int = Field(ge=0, le=20)


class GraphContextReadyEvent(StreamContractModel):
    type: Literal["graph.context_ready"]
    payload: GraphContextReadyPayload


class RerankCompletedPayload(StreamContractModel):
    candidate_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class RerankCompletedEvent(StreamContractModel):
    type: Literal["rerank.completed"]
    payload: RerankCompletedPayload


class AnswerDraftPayload(StreamContractModel):
    """One batched, redacted draft chunk of the model's in-progress answer,
    streamed from Redis (never persisted to MySQL): `attempt`/`seq` order
    drafts within one Turn attempt, not the envelope `sequence`, which is
    carried only for the `ChatEventEnvelope` wrapper and is never advanced
    or deduplicated against for this event type (spec SS task 6)."""

    attempt: int = Field(ge=1)
    seq: int = Field(ge=0)
    text: str
    # Set by `DraftBatcher` when a redaction-driven rewrite changes an
    # already-sent prefix: `text` is empty and the client must discard every
    # earlier chunk for this attempt before accumulating what follows (I-2).
    reset: bool = False


class AnswerDraftEvent(StreamContractModel):
    type: Literal["answer.draft"]
    payload: AnswerDraftPayload


class CitationResolvedPayload(StreamContractModel):
    citation: Citation


class CitationResolvedEvent(StreamContractModel):
    type: Literal["citation.resolved"]
    payload: CitationResolvedPayload


class TurnCompletedPayload(StreamContractModel):
    answer: RetrievalAnswerResponse | None = None
    state: Literal["completed"] | None = None

    @model_validator(mode="after")
    def one_current_or_legacy_fact(self):
        if (self.answer is None) == (self.state is None):
            raise ValueError("turn completion must contain one current or legacy fact")
        return self


class TurnCompletedEvent(StreamContractModel):
    type: Literal["turn.completed"]
    payload: TurnCompletedPayload


class TurnAbstainedPayload(StreamContractModel):
    answer: RetrievalAnswerResponse


class TurnAbstainedEvent(StreamContractModel):
    type: Literal["turn.abstained"]
    payload: TurnAbstainedPayload


class TurnDegradedPayload(StreamContractModel):
    reason: str = Field(min_length=1)
    available_stages: list[str]


class TurnDegradedEvent(StreamContractModel):
    type: Literal["turn.degraded"]
    payload: TurnDegradedPayload


class TurnCanceledPayload(StreamContractModel):
    partial_answer_retained: bool
    partial_answer: str | None = None


class TurnCanceledEvent(StreamContractModel):
    type: Literal["turn.canceled"]
    payload: TurnCanceledPayload


class TurnFailedPayload(StreamContractModel):
    problem: ProblemDetails


class TurnFailedEvent(StreamContractModel):
    type: Literal["turn.failed"]
    payload: TurnFailedPayload


class ConversationTurnRequestedPayload(StreamContractModel):
    conversation_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    input_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ConversationTurnRequestedEvent(StreamContractModel):
    type: Literal["conversation.turn.requested"]
    payload: ConversationTurnRequestedPayload


class ConversationTurnCompletedPayload(StreamContractModel):
    turn_id: str = Field(min_length=1)
    answer_evidence_snapshot_id: str = Field(min_length=1)
    answer_evidence_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    outcome: Literal["completed", "abstained", "canceled", "failed"]


class ConversationTurnCompletedEvent(StreamContractModel):
    type: Literal["conversation.turn.completed"]
    payload: ConversationTurnCompletedPayload


class TestPlanGenerationWaitingPayload(StreamContractModel):
    job_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TestPlanGenerationWaitingEvent(StreamContractModel):
    type: Literal["test-plan.generation.waiting"]
    payload: TestPlanGenerationWaitingPayload


class TestPlanGenerationResultPayload(StreamContractModel):
    job_id: str = Field(min_length=1)
    test_plan_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    deep_link: str = Field(min_length=1)


class TestPlanGenerationResultEvent(StreamContractModel):
    type: Literal["test-plan.generation.result_ready"]
    payload: TestPlanGenerationResultPayload


class TestPlanGenerationFailedPayload(StreamContractModel):
    job_id: str = Field(min_length=1)
    failure_code: str = Field(min_length=1)


class TestPlanGenerationFailedEvent(StreamContractModel):
    type: Literal["test-plan.generation.failed"]
    payload: TestPlanGenerationFailedPayload


class TestPlanGenerationCanceledPayload(StreamContractModel):
    job_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TestPlanGenerationCanceledEvent(StreamContractModel):
    type: Literal["test-plan.generation.canceled"]
    payload: TestPlanGenerationCanceledPayload


ChatStreamEvent = Annotated[
    TurnStartedEvent
    | ContextAssembledEvent
    | QueryPlanReadyEvent
    | StageStartedEvent
    | StageCompletedEvent
    | RetrievalHitsReadyEvent
    | TaskStepEvent
    | GraphContextReadyEvent
    | RerankCompletedEvent
    | AnswerDraftEvent
    | CitationResolvedEvent
    | TurnCompletedEvent
    | TurnAbstainedEvent
    | TurnDegradedEvent
    | TurnCanceledEvent
    | TurnFailedEvent
    | ConversationTurnRequestedEvent
    | ConversationTurnCompletedEvent
    | TestPlanGenerationWaitingEvent
    | TestPlanGenerationResultEvent
    | TestPlanGenerationFailedEvent
    | TestPlanGenerationCanceledEvent,
    Field(discriminator="type"),
]


class ChatEventEnvelope(StreamContractModel):
    """A recoverable, ordered event persisted for one chat turn."""

    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    chat_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    event: ChatStreamEvent
