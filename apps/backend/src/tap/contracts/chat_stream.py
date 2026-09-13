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


class DocumentAnchor(StreamContractModel):
    type: Literal["document"]
    heading_path: list[str] | None = None
    page: int | None = None
    bbox: list[float] | None = None
    start_offset: int | None = None
    end_offset: int | None = None


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


class Citation(StreamContractModel):
    citation_id: str = Field(min_length=1)
    evidence_label: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    logical_chunk_id: str = Field(min_length=1)
    source: SourceRevisionRef
    chunk_content_hash: str = Field(min_length=1)
    content_role: ContentRole
    derived_from_chunk_ids: list[str] | None = None


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
    claims: list[AnswerClaim]
    citations: list[Citation]
    graph_context_status: Literal[
        "APPLIED", "NOT_READY", "FAILED", "UNAVAILABLE", "NOT_SELECTED"
    ] = "NOT_SELECTED"
    graph_snapshot_id: str | None = None


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


class RetrievalHitsReadyPayload(StreamContractModel):
    trace_id: str = Field(min_length=1)
    authorized_hit_count: int = Field(ge=0)


class RetrievalHitsReadyEvent(StreamContractModel):
    type: Literal["retrieval.hits_ready"]
    payload: RetrievalHitsReadyPayload


class RerankCompletedPayload(StreamContractModel):
    candidate_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class RerankCompletedEvent(StreamContractModel):
    type: Literal["rerank.completed"]
    payload: RerankCompletedPayload


class AnswerDeltaPayload(StreamContractModel):
    text: str


class AnswerDeltaEvent(StreamContractModel):
    type: Literal["answer.delta"]
    payload: AnswerDeltaPayload


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


ChatStreamEvent = Annotated[
    TurnStartedEvent
    | ContextAssembledEvent
    | QueryPlanReadyEvent
    | StageStartedEvent
    | StageCompletedEvent
    | RetrievalHitsReadyEvent
    | RerankCompletedEvent
    | AnswerDeltaEvent
    | CitationResolvedEvent
    | TurnCompletedEvent
    | TurnAbstainedEvent
    | TurnDegradedEvent
    | TurnCanceledEvent
    | TurnFailedEvent
    | ConversationTurnRequestedEvent
    | ConversationTurnCompletedEvent,
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
