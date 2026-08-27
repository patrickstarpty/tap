"""Public HTTP DTOs for the first Knowledge Chat contract slice."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    """Base model that exposes camelCase JSON without accepting unknown fields."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class SourceFamily(str, Enum):
    DOC = "doc"
    CODE = "code"
    BDD = "bdd"
    FAILURE = "failure"


class ResourceMode(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    SCOPE = "scope"


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


class DocumentStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class IngestionStage(str, Enum):
    STORED = "stored"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    PUBLISHING = "publishing"
    READY = "ready"


class DocumentStageState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class HealthComponentName(str, Enum):
    MYSQL = "mysql"
    REDIS = "redis"
    BLOB = "blob"
    MILVUS = "milvus"
    MODELS = "models"


class HealthComponentState(str, Enum):
    OK = "ok"
    FAILED = "failed"


class HealthRemediationCode(str, Enum):
    START_MYSQL = "start-mysql"
    START_REDIS = "start-redis"
    START_BLOB = "start-blob"
    START_MILVUS = "start-milvus"
    CONFIGURE_MODELS = "configure-models"


ShortIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=256),
]
SourceIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=1_024),
]
RevisionIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=512),
]
SourceTypeIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128),
]
CanonicalSha256 = Annotated[
    str,
    Field(
        strict=True,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
    ),
]
PathValue = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=2_048),
]
JsonPointerValue = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=4_096),
]
TimestampValue = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128),
]
PositiveAnchorInteger = Annotated[
    StrictInt,
    Field(ge=1, le=2_147_483_647),
]
NonNegativeAnchorInteger = Annotated[
    StrictInt,
    Field(ge=0, le=2_147_483_647),
]
TopK = Annotated[StrictInt, Field(ge=1, le=100)]
BoundingBoxCoordinate = Annotated[float, Field(strict=True, allow_inf_nan=False)]
FiniteScore = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class DocumentStageSnapshot(ContractModel):
    stage: IngestionStage
    state: DocumentStageState
    completed_at: TimestampValue | None = None
    error_code: Annotated[str, Field(strict=True, min_length=1, max_length=64)] | None = None


class DocumentSummary(ContractModel):
    document_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    filename: Annotated[str, Field(strict=True, min_length=1, max_length=255)]
    media_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/markdown",
        "text/plain",
    ]
    status: DocumentStatus
    stage: IngestionStage
    chunk_count: Annotated[StrictInt, Field(ge=0, le=10_000)]
    updated_at: TimestampValue
    error_code: Annotated[str, Field(strict=True, min_length=1, max_length=64)] | None = None
    error_summary: Annotated[str, Field(strict=True, min_length=1, max_length=240)] | None = None


class DocumentAccepted(ContractModel):
    document: DocumentSummary
    job_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    duplicate: bool


class DocumentPage(ContractModel):
    items: Annotated[list[DocumentSummary], Field(max_length=50)]
    next_cursor: Annotated[str, Field(strict=True, min_length=1, max_length=512)] | None = None


class DocumentDetail(DocumentSummary):
    revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    source_content_hash: CanonicalSha256
    stages: Annotated[list[DocumentStageSnapshot], Field(min_length=1, max_length=6)]
    normalized_preview: Annotated[str, Field(strict=True, max_length=4_000)] | None = None

    @model_validator(mode="after")
    def validate_failure_fields(self) -> Self:
        fields_present = self.error_code is not None or self.error_summary is not None
        if self.status is DocumentStatus.FAILED and (
            self.error_code is None or self.error_summary is None
        ):
            raise ValueError("failed documents require a public error code and summary")
        if self.status is not DocumentStatus.FAILED and fields_present:
            raise ValueError("only failed documents may expose public error fields")
        return self


class CitationPreview(ContractModel):
    citation_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    document_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    filename: Annotated[str, Field(strict=True, min_length=1, max_length=255)]
    source_content_hash: CanonicalSha256
    chunk_content_hash: CanonicalSha256
    anchor: StructuralAnchor
    quote: Annotated[str, Field(strict=True, min_length=1, max_length=4_000)]
    prefix: Annotated[str, Field(strict=True, max_length=500)] = ""
    suffix: Annotated[str, Field(strict=True, max_length=500)] = ""


class HealthComponent(ContractModel):
    name: HealthComponentName
    state: HealthComponentState
    remediation_code: HealthRemediationCode | None = None

    @model_validator(mode="after")
    def validate_remediation_code(self) -> Self:
        expected = {
            HealthComponentName.MYSQL: HealthRemediationCode.START_MYSQL,
            HealthComponentName.REDIS: HealthRemediationCode.START_REDIS,
            HealthComponentName.BLOB: HealthRemediationCode.START_BLOB,
            HealthComponentName.MILVUS: HealthRemediationCode.START_MILVUS,
            HealthComponentName.MODELS: HealthRemediationCode.CONFIGURE_MODELS,
        }[self.name]
        if self.remediation_code is not None and self.remediation_code is not expected:
            raise ValueError("health remediation code must match its fixed component")
        return self


class LiveHealth(ContractModel):
    status: Literal["ok"]


class ReadyHealth(ContractModel):
    status: Literal["ready", "unready"]
    components: Annotated[list[HealthComponent], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def validate_component_coverage(self) -> Self:
        if {component.name for component in self.components} != set(HealthComponentName):
            raise ValueError("readiness must report every fixed dependency exactly once")
        return self


class DocumentAnchor(ContractModel):
    type: Literal["document"]
    heading_path: Annotated[list[ShortIdentifier], Field(max_length=32)] | None = None
    page: PositiveAnchorInteger | None = None
    bbox: Annotated[list[BoundingBoxCoordinate], Field(min_length=4, max_length=4)] | None = None
    start_offset: NonNegativeAnchorInteger | None = None
    end_offset: NonNegativeAnchorInteger | None = None

    @model_validator(mode="after")
    def validate_ordered_offsets(self) -> Self:
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset < self.start_offset
        ):
            raise ValueError("document anchor offsets must be ordered")
        return self


class CodeAnchor(ContractModel):
    type: Literal["code"]
    repo: ShortIdentifier
    path: PathValue
    symbol: Annotated[str, Field(strict=True, min_length=1, max_length=512)] | None = None
    line_start: PositiveAnchorInteger
    line_end: PositiveAnchorInteger

    @model_validator(mode="after")
    def validate_ordered_lines(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("code anchor lines must be ordered")
        return self


class BddAnchor(ContractModel):
    type: Literal["bdd"]
    feature_id: ShortIdentifier
    scenario_id: ShortIdentifier | None = None
    step_id: ShortIdentifier | None = None


class OpenApiAnchor(ContractModel):
    type: Literal["openapi"]
    method: Annotated[str, Field(strict=True, min_length=1, max_length=16)]
    path: PathValue
    json_pointer: JsonPointerValue


class FailureAnchor(ContractModel):
    type: Literal["failure"]
    incident_id: ShortIdentifier
    run_id: ShortIdentifier | None = None
    time_start: TimestampValue | None = None
    time_end: TimestampValue | None = None


StructuralAnchorValue = Annotated[
    DocumentAnchor | CodeAnchor | BddAnchor | OpenApiAnchor | FailureAnchor,
    Field(discriminator="type"),
]


class StructuralAnchor(RootModel[StructuralAnchorValue]):
    """A closed, structural location inside one authorized source family."""


_KNOWN_SOURCE_TYPE_FAMILY = {
    "code": SourceFamily.CODE,
    "code_summary": SourceFamily.CODE,
    "bdd": SourceFamily.BDD,
    "doc": SourceFamily.DOC,
    "document": SourceFamily.DOC,
    "openapi": SourceFamily.DOC,
    "failure": SourceFamily.FAILURE,
}


def _source_family_for_provenance(
    *,
    source_type: str,
    revision_kind: RevisionKind,
    anchor: StructuralAnchor,
) -> SourceFamily:
    value = anchor.root
    if isinstance(value, CodeAnchor):
        family = SourceFamily.CODE
        expected_revision = RevisionKind.GIT_COMMIT
    elif isinstance(value, BddAnchor):
        family = SourceFamily.BDD
        expected_revision = RevisionKind.GIT_COMMIT
    elif isinstance(value, (DocumentAnchor, OpenApiAnchor)):
        family = SourceFamily.DOC
        expected_revision = RevisionKind.BLOB_VERSION
    elif isinstance(value, FailureAnchor):
        family = SourceFamily.FAILURE
        expected_revision = RevisionKind.MYSQL_VERSION
    else:  # pragma: no cover - the discriminated closed union is exhaustive
        raise ValueError("source provenance uses an unknown structural anchor")

    known_family = _KNOWN_SOURCE_TYPE_FAMILY.get(source_type)
    if revision_kind is not expected_revision or (
        known_family is not None and known_family is not family
    ):
        raise ValueError("source provenance does not resolve to one compatible family")
    return family


class ResourceRef(ContractModel):
    """Browser-provided retrieval intent; it cannot contain policy or ACL facts."""

    family: SourceFamily
    source_id: SourceIdentifier
    mode: ResourceMode = ResourceMode.PREFERRED
    requested_revision: RevisionIdentifier | None = None
    anchor: StructuralAnchor | None = None


class ChatTurnRequest(ContractModel):
    """A browser request to create one turn in an existing chat."""

    client_request_id: ShortIdentifier
    message: Annotated[str, Field(strict=True, min_length=1, max_length=8_000)]
    answer_mode: AnswerMode = AnswerMode.QUICK
    source_scope: Annotated[list[SourceFamily], Field(max_length=4)] | None = None
    resource_refs: Annotated[list[ResourceRef], Field(max_length=20)] | None = None
    requested_environment: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None
    requested_corpus_version: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None


class RetrievalSearchRequest(ContractModel):
    """Browser-visible retrieval intent; all authoritative scope is omitted."""

    query: Annotated[str, Field(strict=True, min_length=1, max_length=8_000)]
    answer_mode: AnswerMode = AnswerMode.QUICK
    sources: list[SourceFamily] | None = Field(default=None, max_length=4)
    resource_refs: list[ResourceRef] | None = Field(default=None, max_length=20)
    requested_environment: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None
    requested_corpus_version: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None
    top_k: TopK | None = None

    @field_validator("query")
    @classmethod
    def validate_nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("retrieval query must contain non-whitespace text")
        return value

    @field_validator("sources")
    @classmethod
    def validate_unique_sources(cls, value: list[SourceFamily] | None) -> list[SourceFamily] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("retrieval sources must be unique")
        return value


class RetrievalAnswerRequest(RetrievalSearchRequest):
    """Grounded-answer intent with the same narrowing-only search fields."""


class RetrievalSourceRevision(ContractModel):
    source_id: SourceIdentifier
    source_type: SourceTypeIdentifier
    revision_kind: RevisionKind
    revision: RevisionIdentifier
    source_content_hash: CanonicalSha256
    anchor: StructuralAnchor

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        _source_family_for_provenance(
            source_type=self.source_type,
            revision_kind=self.revision_kind,
            anchor=self.anchor,
        )
        if self.revision_kind is RevisionKind.GIT_COMMIT and (
            len(self.revision) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in self.revision)
        ):
            raise ValueError("Git source revision must be a canonical commit ID")
        return self

    @property
    def derived_family(self) -> SourceFamily:
        return _source_family_for_provenance(
            source_type=self.source_type,
            revision_kind=self.revision_kind,
            anchor=self.anchor,
        )


class RetrievalScores(ContractModel):
    exact: FiniteScore | None = None
    bm25: FiniteScore | None = None
    vector: FiniteScore | None = None
    rrf: FiniteScore | None = None
    rerank: FiniteScore | None = None


class RetrievalHit(ContractModel):
    index_family: SourceFamily
    chunk_id: str = Field(min_length=1)
    logical_chunk_id: str = Field(min_length=1)
    title: str | None = None
    content: str
    source: RetrievalSourceRevision
    chunk_content_hash: CanonicalSha256
    content_role: ContentRole
    citation_id: str = Field(min_length=1)
    evidence_label: str = Field(min_length=1)
    scores: RetrievalScores
    acl_decision_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    embedding_model_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_index_family(self) -> Self:
        if self.index_family is not self.source.derived_family:
            raise ValueError("hit index family does not match source provenance")
        return self


class RetrievalCitation(ContractModel):
    citation_id: str = Field(min_length=1)
    evidence_label: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    logical_chunk_id: str = Field(min_length=1)
    source: RetrievalSourceRevision
    chunk_content_hash: CanonicalSha256
    content_role: ContentRole
    derived_from_chunk_ids: list[str] | None = None

    @model_validator(mode="after")
    def validate_internal_source_family(self, info: ValidationInfo) -> Self:
        context = info.context
        if not isinstance(context, dict) or "source_family" not in context:
            return self
        expected = context["source_family"]
        if not isinstance(expected, SourceFamily) or expected is not self.source.derived_family:
            raise ValueError("citation family does not match source provenance")
        return self


class RetrievalClaim(ContractModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    answer_start: NonNegativeAnchorInteger
    answer_end: NonNegativeAnchorInteger
    citation_ids: Annotated[list[str], Field(min_length=1, max_length=20)]


class RetrievalSearchResponse(ContractModel):
    trace_id: str = Field(min_length=1)
    query_plan_id: str = Field(min_length=1)
    context_snapshot_id: str = Field(min_length=1)
    corpus_version: str = Field(min_length=1)
    retrieval_profile_id: str = Field(min_length=1)
    degraded_mode: bool
    degradation_reasons: list[str] | None = None
    hits: list[RetrievalHit]


class RetrievalAnswerResponse(ContractModel):
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
    claims: list[RetrievalClaim]
    citations: Annotated[list[RetrievalCitation], Field(max_length=20)]

    @model_validator(mode="after")
    def validate_claim_spans(self) -> Self:
        citation_ids = {citation.citation_id for citation in self.citations}
        paragraphs = _answer_paragraph_spans(self.answer)
        previous_end = 0
        for claim in self.claims:
            if not set(claim.citation_ids) <= citation_ids:
                raise ValueError("claim citations must exist in the answer citation set")
            if "\n\n" in claim.text:
                raise ValueError("claim text must not contain a paragraph separator")
            matches = [
                (start, end) for start, end in paragraphs if self.answer[start:end] == claim.text
            ]
            if len(matches) != 1 or matches[0] != (claim.answer_start, claim.answer_end):
                raise ValueError("claim text must occupy one unique complete answer paragraph")
            if claim.answer_end < claim.answer_start:
                raise ValueError("claim answer offsets must be ordered")
            if claim.answer_start < previous_end:
                raise ValueError("claim answer spans must not overlap")
            if claim.answer_end > len(self.answer):
                raise ValueError("claim answer span exceeds the answer")
            if self.answer[claim.answer_start : claim.answer_end] != claim.text:
                raise ValueError("claim text must match its answer span")
            if claim.answer_start != 0 and not self.answer[: claim.answer_start].endswith("\n\n"):
                raise ValueError("claim answer span must start on a paragraph boundary")
            if claim.answer_end != len(self.answer) and not self.answer[
                claim.answer_end :
            ].startswith("\n\n"):
                raise ValueError("claim answer span must end on a paragraph boundary")
            previous_end = claim.answer_end
        return self


def _answer_paragraph_spans(answer: str) -> tuple[tuple[int, int], ...]:
    """Return complete answer-paragraph spans using Unicode code-point offsets."""
    start = 0
    spans: list[tuple[int, int]] = []
    for paragraph in answer.split("\n\n"):
        end = start + len(paragraph)
        spans.append((start, end))
        start = end + 2
    return tuple(spans)


class ChatTurnAccepted(ContractModel):
    """The durable identity returned after a turn has been accepted for processing."""

    chat_id: str
    turn_id: str
    state: Literal["queued"]


class ProblemDetails(ContractModel):
    """RFC 9457 problem details returned by the public HTTP interface."""

    type: str = Field(pattern=r"^https://")
    title: str = Field(min_length=1)
    status: int = Field(ge=100, le=599)
    detail: str = Field(min_length=1)
    instance: str | None = None
