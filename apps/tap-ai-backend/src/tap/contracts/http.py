"""Public HTTP DTOs for the first Knowledge Chat contract slice."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from tap.contracts.problems import ProblemDetails as ProblemDetails


class ContractModel(BaseModel):
    """Base model that exposes camelCase JSON without accepting unknown fields."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class RuntimeMode(ContractModel):
    """Server-owned validation context; never a personal authentication claim."""

    mode: Literal["validation"]
    project_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    identity_mode: Literal["validation"]


class ModelCatalogItem(ContractModel):
    alias: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    display_name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    capabilities: Annotated[
        list[Literal["chat", "embed", "structured"]], Field(min_length=1, max_length=3)
    ]


class ModelCatalogPage(ContractModel):
    default_alias: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    items: Annotated[list[ModelCatalogItem], Field(max_length=32)]


class AiAgentRevisionSummary(ContractModel):
    revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    asset_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    display_name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    content_digest: CanonicalSha256
    tool_allowlist: Annotated[
        list[Literal["knowledge.search", "knowledge.answer"]], Field(max_length=2)
    ]
    output_schema_digest: CanonicalSha256


class AiAgentRevisionPage(ContractModel):
    items: Annotated[list[AiAgentRevisionSummary], Field(max_length=64)]


class SkillRevisionSummary(ContractModel):
    revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    asset_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    display_name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    content_digest: CanonicalSha256
    applicable_tasks: Annotated[
        list[Literal["knowledge.answer", "test-plan.generate", "automation.generate"]],
        Field(min_length=1, max_length=3),
    ]


class SkillRevisionPage(ContractModel):
    items: Annotated[list[SkillRevisionSummary], Field(max_length=64)]


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


class KnowledgeReviewStatus(str, Enum):
    DRAFT = "draft"
    CHECKING = "checking"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    PUBLISHED = "published"
    NEEDS_REVIEW = "needs_review"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class KnowledgeReviewAction(str, Enum):
    EDIT = "edit"
    SUBMIT = "submit"
    RETURN = "return"
    APPROVE = "approve"
    PUBLISH = "publish"
    WITHDRAW = "withdraw"
    READ_ORIGINAL = "read_original"


class KnowledgeReviewCheckKind(str, Enum):
    SCOPE = "scope"
    TERM = "term"
    AMOUNT = "amount"
    UNIT = "unit"
    EXCEPTION = "exception"


class KnowledgeReviewDecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    EXCLUDED = "excluded"


class ParseInventoryItemStatus(str, Enum):
    PARSED = "parsed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    EXCLUDED = "excluded"


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
    source_id: Annotated[str, Field(strict=True, pattern=r"^src_[0-9a-f]{32}$")]
    document_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    filename: Annotated[str, Field(strict=True, min_length=1, max_length=255)]
    media_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/markdown",
        "text/plain",
        "image/png",
        "image/jpeg",
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


class KnowledgeReviewSummary(ContractModel):
    review_id: ShortIdentifier
    status: KnowledgeReviewStatus
    version: Annotated[StrictInt, Field(ge=1)]
    reviewer_actor_id: ShortIdentifier | None = None
    expires_at: TimestampValue
    approval_digest: CanonicalSha256


class KnowledgeReviewOpenRequest(ContractModel):
    source_revision_id: ShortIdentifier


class KnowledgePublishRequest(ContractModel):
    generation: ShortIdentifier


class KnowledgePublicationDetail(ContractModel):
    publication_id: ShortIdentifier
    review_id: ShortIdentifier
    review_version: Annotated[StrictInt, Field(ge=1)]
    version: Annotated[StrictInt, Field(ge=1)]
    status: Literal["published", "withdrawn"]
    generation: ShortIdentifier
    approval_digest: CanonicalSha256
    source_revision_ids: Annotated[list[ShortIdentifier], Field(min_length=1, max_length=100)]
    approved_item_ids: Annotated[list[ShortIdentifier], Field(min_length=1, max_length=10_000)]
    published_at: TimestampValue
    expires_at: TimestampValue
    withdrawn_by: ShortIdentifier | None = None
    withdrawn_at: TimestampValue | None = None


class KnowledgeReviewInventoryItem(ContractModel):
    source_revision_id: ShortIdentifier
    item_id: ShortIdentifier
    attempt: Annotated[StrictInt, Field(ge=1)]
    kind: Literal[
        "document",
        "page",
        "paragraph",
        "heading",
        "table",
        "image",
        "list",
        "code",
        "flow_node",
        "flow_edge",
    ]
    locator: Annotated[str, Field(strict=True, min_length=1, max_length=1_024)]
    status: ParseInventoryItemStatus
    artifact_digest: CanonicalSha256
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None = None
    decision_actor_id: ShortIdentifier | None = None


class KnowledgeReviewInventory(ContractModel):
    items: Annotated[list[KnowledgeReviewInventoryItem], Field(max_length=100)]
    total_count: Annotated[StrictInt, Field(ge=0)]
    parsed_count: Annotated[StrictInt, Field(ge=0)]
    failed_count: Annotated[StrictInt, Field(ge=0)]
    needs_review_count: Annotated[StrictInt, Field(ge=0)]
    excluded_count: Annotated[StrictInt, Field(ge=0)]
    next_cursor: ShortIdentifier | None = None


class KnowledgeReviewItemDecisionDetail(ContractModel):
    decision_id: ShortIdentifier
    decision_digest: CanonicalSha256
    item_id: ShortIdentifier
    check_kind: KnowledgeReviewCheckKind
    status: KnowledgeReviewDecisionStatus
    note: Annotated[str, Field(strict=True, min_length=1, max_length=1_000)]
    actor_id: ShortIdentifier
    review_version: Annotated[StrictInt, Field(ge=2)]
    decided_at: TimestampValue


class KnowledgeReviewHistoryDetail(ContractModel):
    review_version: Annotated[StrictInt, Field(ge=1)]
    action: ShortIdentifier
    actor_id: ShortIdentifier
    occurred_at: TimestampValue
    item_id: ShortIdentifier | None = None
    decision_id: ShortIdentifier | None = None
    decision_digest: CanonicalSha256 | None = None


class KnowledgeReviewDecisionPage(ContractModel):
    items: Annotated[list[KnowledgeReviewItemDecisionDetail], Field(max_length=100)]
    total_count: Annotated[StrictInt, Field(ge=0)]
    next_cursor: Annotated[StrictInt, Field(ge=1)] | None = None


class KnowledgeReviewHistoryPage(ContractModel):
    items: Annotated[list[KnowledgeReviewHistoryDetail], Field(max_length=100)]
    total_count: Annotated[StrictInt, Field(ge=0)]
    next_cursor: Annotated[StrictInt, Field(ge=1)] | None = None


class KnowledgePublicationPage(ContractModel):
    items: Annotated[list[KnowledgePublicationDetail], Field(max_length=100)]
    total_count: Annotated[StrictInt, Field(ge=0)]
    next_cursor: ShortIdentifier | None = None


class KnowledgePublicationTarget(ContractModel):
    status: Literal["ready", "unavailable"]
    generation: ShortIdentifier | None = None
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None = None


class KnowledgeReviewDetail(KnowledgeReviewSummary):
    source_revision_ids: Annotated[list[ShortIdentifier], Field(min_length=1, max_length=100)]
    editor_actor_ids: Annotated[list[ShortIdentifier], Field(min_length=1, max_length=100)]
    blocking_item_ids: Annotated[list[ShortIdentifier], Field(max_length=10_000)]
    approved_item_ids: Annotated[list[ShortIdentifier], Field(max_length=10_000)]
    inventory: KnowledgeReviewInventory
    decisions: Annotated[list[KnowledgeReviewItemDecisionDetail], Field(max_length=500)]
    decision_history: Annotated[list[KnowledgeReviewItemDecisionDetail], Field(max_length=100)]
    decision_history_total_count: Annotated[StrictInt, Field(ge=0)]
    decision_history_next_cursor: Annotated[StrictInt, Field(ge=1)] | None = None
    history: Annotated[list[KnowledgeReviewHistoryDetail], Field(max_length=100)]
    history_total_count: Annotated[StrictInt, Field(ge=0)]
    history_next_cursor: Annotated[StrictInt, Field(ge=1)] | None = None
    publication_ids: Annotated[list[ShortIdentifier], Field(max_length=100)]
    publication_total_count: Annotated[StrictInt, Field(ge=0)]
    publication_next_cursor: ShortIdentifier | None = None
    current_publication: KnowledgePublicationDetail | None = None
    publication_target: KnowledgePublicationTarget
    allowed_actions: Annotated[list[KnowledgeReviewAction], Field(max_length=7)]


class KnowledgeReviewPage(ContractModel):
    items: Annotated[list[KnowledgeReviewDetail], Field(max_length=100)]
    next_cursor: ShortIdentifier | None = None


class KnowledgeReviewDecisionRequest(ContractModel):
    check_kind: KnowledgeReviewCheckKind
    status: KnowledgeReviewDecisionStatus
    note: Annotated[str, Field(strict=True, min_length=1, max_length=1_000)]


class KnowledgeFlowchartNode(ContractModel):
    id: Annotated[str, Field(strict=True, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
    label: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    lane: Annotated[str, Field(strict=True, max_length=100)]
    box: Annotated[
        list[Annotated[StrictInt, Field(ge=0, le=8192)]], Field(min_length=4, max_length=4)
    ]


class KnowledgeFlowchartEdge(ContractModel):
    source: Annotated[str, Field(strict=True, min_length=1, max_length=32)]
    target: Annotated[str, Field(strict=True, min_length=1, max_length=32)]
    condition: Annotated[str, Field(strict=True, max_length=200)]
    certain: StrictBool


class KnowledgeFlowchart(ContractModel):
    nodes: Annotated[list[KnowledgeFlowchartNode], Field(min_length=1, max_length=100)]
    edges: Annotated[list[KnowledgeFlowchartEdge], Field(max_length=200)]


class KnowledgeFlowchartCorrection(ContractModel):
    source_revision_id: ShortIdentifier


class KnowledgeReviewPreview(ContractModel):
    availability: Literal["available", "unavailable", "unsupported"]
    excerpt: Annotated[str, Field(strict=True, max_length=4_000)] | None = None
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None = None


class KnowledgeReviewItemComparison(ContractModel):
    review_id: ShortIdentifier
    item_id: ShortIdentifier
    original: KnowledgeReviewPreview
    extracted: KnowledgeReviewPreview


class PublishedKnowledgeSource(ContractModel):
    source_id: Annotated[str, Field(strict=True, pattern=r"^src_[0-9a-f]{32}$")]
    document_id: ShortIdentifier
    revision_id: ShortIdentifier
    source_name: Annotated[str, Field(strict=True, min_length=1, max_length=255)]
    filename: Annotated[str, Field(strict=True, min_length=1, max_length=255)]
    publication_id: ShortIdentifier | None = None
    expires_at: TimestampValue | None = None
    approved_item_count: Annotated[StrictInt, Field(ge=0, le=10_000)]
    inventory_item_count: Annotated[StrictInt, Field(ge=0, le=10_000)]
    partial: bool


class PublishedKnowledgeSourcePage(ContractModel):
    items: Annotated[list[PublishedKnowledgeSource], Field(max_length=100)]


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


class SourceSummary(ContractModel):
    source_id: Annotated[str, Field(strict=True, pattern=r"^src_[0-9a-f]{32}$")]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=255)]
    created_at: TimestampValue
    document_count: Annotated[StrictInt, Field(ge=0, le=50)]
    ready_count: Annotated[StrictInt, Field(ge=0, le=50)]
    failed_count: Annotated[StrictInt, Field(ge=0, le=50)]


class SourcePage(ContractModel):
    items: Annotated[list[SourceSummary], Field(max_length=50)]
    next_cursor: Annotated[str, Field(strict=True, min_length=1, max_length=512)] | None = None


class SourceDocument(DocumentDetail):
    source_id: Annotated[str, Field(strict=True, pattern=r"^src_[0-9a-f]{32}$")]
    attempt: Annotated[StrictInt, Field(ge=1)]


class SourceDocumentPage(ContractModel):
    items: Annotated[list[SourceDocument], Field(max_length=50)]
    next_cursor: Annotated[str, Field(strict=True, min_length=1, max_length=512)] | None = None


class SourceDetail(SourceSummary):
    documents: SourceDocumentPage


class SourceAccepted(ContractModel):
    source: SourceSummary
    accepted: DocumentAccepted


class SourceRetryRequest(ContractModel):
    document_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    expected_attempt: Annotated[StrictInt, Field(ge=1)]


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
    detail: Annotated[str, Field(min_length=1, max_length=2048)] | None = None

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
    inventory_item_id: ShortIdentifier | None = None

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
    publication_id: ShortIdentifier | None = None
    approval_digest: CanonicalSha256 | None = None
    approved_item_id: ShortIdentifier | None = None

    @model_validator(mode="after")
    def validate_index_family(self) -> Self:
        if self.index_family is not self.source.derived_family:
            raise ValueError("hit index family does not match source provenance")
        return self


class GraphEndpointView(ContractModel):
    """One edge citation endpoint (subject or object), nested under
    `EdgeCitationView`."""

    node_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1)


class EdgeCitationView(ContractModel):
    edge_id: str = Field(min_length=1, max_length=128)
    graph_version: str = Field(min_length=1, max_length=64)
    subject: GraphEndpointView
    object: GraphEndpointView
    relation_type: str = Field(min_length=1, max_length=64)
    relation_label: str = Field(min_length=1, max_length=64)


class RetrievalCitation(ContractModel):
    citation_id: str = Field(min_length=1)
    evidence_label: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    logical_chunk_id: str = Field(min_length=1)
    source: RetrievalSourceRevision
    chunk_content_hash: CanonicalSha256
    content_role: ContentRole
    derived_from_chunk_ids: list[str] | None = None
    publication_id: ShortIdentifier | None = None
    approval_digest: CanonicalSha256 | None = None
    approved_item_id: ShortIdentifier | None = None
    kind: Literal["chunk", "edge"] = "chunk"
    edge: EdgeCitationView | None = None

    @model_validator(mode="after")
    def validate_internal_source_family(self, info: ValidationInfo) -> Self:
        context = info.context
        if not isinstance(context, dict) or "source_family" not in context:
            return self
        expected = context["source_family"]
        if not isinstance(expected, SourceFamily) or expected is not self.source.derived_family:
            raise ValueError("citation family does not match source provenance")
        return self

    @model_validator(mode="after")
    def validate_edge_kind(self) -> Self:
        if (self.kind == "edge") != (self.edge is not None):
            raise ValueError("citation kind and edge payload must agree")
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


class GraphContextSummaryView(ContractModel):
    status: Literal["APPLIED", "NOT_READY", "STALE", "FAILED", "EMPTY"]
    graph_version: str | None = None
    seed_count: int = Field(ge=0)
    paths: list[list[str]] = Field(default_factory=list)
    relation_count: int = Field(ge=0)


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
    graph_context_status: Literal[
        "APPLIED", "NOT_READY", "STALE", "FAILED", "UNAVAILABLE", "NOT_SELECTED", "EMPTY"
    ] = "NOT_SELECTED"
    graph_snapshot_id: str | None = None
    graph_context: GraphContextSummaryView | None = None

    @model_validator(mode="after")
    def validate_graph_context(self) -> Self:
        if (self.graph_context_status == "APPLIED") != bool(self.graph_snapshot_id):
            raise ValueError("only applied Graph context can identify a snapshot")
        return self

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


class InsightsExplanationRequest(ContractModel):
    query_id: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
    resource_refs: Annotated[
        list[Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]],
        Field(min_length=1, max_length=20),
    ]
    question: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    conversation_id: str | None = None
    source_revision_ids: Annotated[list[str], Field(max_length=20)] = []
    document_revision_ids: Annotated[list[str], Field(max_length=20)] = []

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must be nonblank")
        return value

    @field_validator("resource_refs")
    @classmethod
    def unique_resource_refs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("resource refs must be unique")
        return values


class InsightsExplanationFact(ContractModel):
    metric_id: str
    numerator: int | None
    denominator: int | None
    value: float | None
    completeness: Literal["complete", "empty", "unavailable"]
    missing_reasons: list[str]
    evidence_refs: list[str]


class InsightsEvidenceExcerpt(ContractModel):
    citation_id: str
    text: str
    evidence_version: str | None = None
    source_id: str | None = None
    revision_id: str | None = None
    chunk_id: str | None = None
    source_content_hash: str | None = None
    chunk_content_hash: str | None = None
    publication_id: str | None = None
    approval_digest: str | None = None
    approved_item_id: str | None = None
    page: int | None = None


class InsightsExplanationResult(ContractModel):
    query_id: str | None
    metric_version: str | None
    as_of: datetime | None
    fact_watermark: dict[str, object] | None = None
    knowledge_search_performed: bool = False
    facts: list[InsightsExplanationFact]
    report_coverage: list[dict[str, object]]
    hypotheses: list[str]
    evidence_excerpts: list[InsightsEvidenceExcerpt]
    missing_information: list[str]
    stop_reason: Literal["completed", "budget-exhausted", "insights-unavailable"]


class InsightsExplanationAccepted(ContractModel):
    conversation_id: str
    turn_id: str
    state: Literal["queued", "running", "completed", "abstained", "canceled", "failed"]


class ConversationCreateRequest(ContractModel):
    message: Annotated[str, Field(strict=True, min_length=1, max_length=20_000)]
    model_alias: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    source_revision_ids: Annotated[list[str], Field(max_length=50)] = []
    document_revision_ids: Annotated[list[str], Field(max_length=50)] = []
    agent_revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)] | None = None
    skill_revision_ids: Annotated[list[str], Field(max_length=16)] = []

    @field_validator("message")
    @classmethod
    def message_is_trimmed(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be nonblank")
        return value


class ConversationResolvedResourceView(ContractModel):
    source_id: str
    document_id: str
    source_revision_id: str | None = None
    document_revision_id: str
    label: str


class ConversationTurnInputView(ContractModel):
    """Browser-safe immutable input facts; excludes instructions and policy internals."""

    message: str
    model_alias: str
    source_revision_ids: list[str]
    document_revision_ids: list[str]
    resolved_resources: list[ConversationResolvedResourceView]
    agent_revision_id: str | None = None
    agent_label: str | None = None
    skill_revision_ids: list[str]
    skill_labels: list[str]
    insights_query_id: str | None = None


class ConversationTurnSummary(ContractModel):
    turn_id: str
    state: Literal["queued", "running", "completed", "abstained", "canceled", "failed"]
    attempt: StrictInt
    input_snapshot_digest: CanonicalSha256
    answer_evidence_snapshot_id: str | None = None
    answer_evidence_snapshot_digest: CanonicalSha256 | None = None
    graph_context_status: (
        Literal["APPLIED", "NOT_READY", "STALE", "FAILED", "UNAVAILABLE", "NOT_SELECTED", "EMPTY"]
        | None
    ) = None
    graph_snapshot_id: str | None = None
    input: ConversationTurnInputView
    trace_id: str | None = None


class ConversationSummary(ContractModel):
    conversation_id: str
    title: str
    created_at: TimestampValue
    updated_at: TimestampValue


class ConversationRenameRequest(ContractModel):
    """Owner-only title change; surrounding whitespace is removed before bounds apply."""

    title: Annotated[
        str,
        Field(strict=True),
        StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    ]


class ConversationPage(ContractModel):
    items: list[ConversationSummary]
    next_cursor: str | None = None


class ConversationDetail(ConversationSummary):
    turns: list[ConversationTurnSummary]


class ConversationAccepted(ContractModel):
    conversation_id: str
    turn_id: str
    state: Literal["queued"]


class TurnTraceSummary(ContractModel):
    total_duration_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None = None
    cost_incomplete: bool
    requested_models: list[str]
    upstream_models: list[str]
    attempt_count: int


class TraceSpanView(ContractModel):
    span_id: str
    parent_span_id: str | None = None
    name: str
    status: Literal["ok", "error"]
    started_at: datetime
    duration_ms: int
    attributes: dict[str, JsonValue]
    attempt: int | None = None


class ModelCallView(ContractModel):
    call_id: str
    span_id: str | None = None
    operation: str
    model_name: str
    upstream_model: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    latency_ms: int
    attempts: int
    status: Literal["ok", "error"]
    error_code: str | None = None
    created_at: datetime


class ModelCallDetail(ModelCallView):
    request: str
    response: str | None = None
    reasoning: str | None = None


class TurnTrace(ContractModel):
    trace_id: str
    summary: TurnTraceSummary
    spans: list[TraceSpanView]
    model_calls: list[ModelCallView]


class ConversationEventItem(ContractModel):
    event_id: str
    sequence: StrictInt
    turn_id: str
    event_type: Literal[
        "turn.started",
        "context.assembled",
        "query.plan_ready",
        "stage.started",
        "stage.completed",
        "retrieval.hits_ready",
        "graph.context_ready",
        "rerank.completed",
        "answer.delta",
        "citation.resolved",
        "turn.completed",
        "turn.abstained",
        "turn.degraded",
        "turn.canceled",
        "turn.failed",
        "conversation.turn.requested",
        "conversation.turn.completed",
        "test-plan.generation.waiting",
        "test-plan.generation.result_ready",
        "test-plan.generation.failed",
        "test-plan.generation.canceled",
    ]
    payload: dict[str, object]
    occurred_at: TimestampValue


class ConversationEventPage(ContractModel):
    items: list[ConversationEventItem]


class GraphNodeView(ContractModel):
    node_id: str
    label: str
    node_type: str
    canonical_key: str
    evidence_ids: list[str] = []


class GraphEdgeView(ContractModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    origin: Literal["EXTRACTED", "INFERRED"]
    confidence: float
    evidence_ids: list[str] = []


class GraphEvidenceView(ContractModel):
    evidence_id: str
    source_revision_id: str
    document_revision_id: str
    chunk_id: str
    anchor: dict[str, object]
    content_digest: CanonicalSha256


class GraphSubgraphView(ContractModel):
    snapshot_id: str
    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]
    evidence: list[GraphEvidenceView] = []


class GraphSearchRequest(ContractModel):
    snapshot_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)] | None = None
    query: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    node_limit: Annotated[StrictInt, Field(ge=1, le=500)] = 50
    source_revision_ids: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]],
        Field(max_length=50),
    ] = []
    graph_version: StrictInt | None = None


class GraphNeighborRequest(ContractModel):
    snapshot_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    depth: Annotated[StrictInt, Field(ge=1, le=2)] = 1
    node_limit: Annotated[StrictInt, Field(ge=1, le=500)] = 50


class GraphPathRequest(ContractModel):
    snapshot_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)] | None = None
    source_node_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    target_node_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    node_limit: Annotated[StrictInt, Field(ge=1, le=500)] = 50
    max_hops: Annotated[StrictInt, Field(ge=1, le=3)] = 3
    source_revision_ids: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]],
        Field(max_length=50),
    ] = []
    graph_version: StrictInt | None = None


class ProjectGraphCommunityView(ContractModel):
    community_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    label: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    size: StrictInt


class ProjectGraphView(ContractModel):
    graph_version: StrictInt | None = None
    status: Literal["EMPTY", "MERGING", "READY", "FAILED"]
    node_count: StrictInt
    edge_count: StrictInt
    communities: list[ProjectGraphCommunityView] = []
    merged_at: datetime | None = None
    extracting_revision_ids: list[str] = []
    partial_revision_ids: list[str] = []


class ProjectGraphNodeView(ContractModel):
    node_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    label: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    node_type: Annotated[str, Field(strict=True, min_length=1, max_length=32)]
    canonical_key: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    degree: StrictInt
    community_id: str | None = None
    aliases: list[str] = []


class ProjectGraphEdgeView(ContractModel):
    edge_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    source_node_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    target_node_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    relation_type: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    relation_label: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    origin: Literal["EXTRACTED", "INFERRED"]
    confidence: float


class ProjectGraphEvidenceView(ContractModel):
    owner_kind: Literal["node", "edge"]
    owner_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    source_revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    document_revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    chunk_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    anchor: dict[str, object]
    content_digest: CanonicalSha256 | None = None
    snippet: Annotated[str, Field(strict=True, min_length=1, max_length=300)] | None = None


class ProjectGraphSubgraphView(ContractModel):
    graph_version: StrictInt
    nodes: list[ProjectGraphNodeView]
    edges: list[ProjectGraphEdgeView]
    evidence: list[ProjectGraphEvidenceView] = []


class ProjectGraphRelationGroupView(ContractModel):
    relation_type: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    edges: list[ProjectGraphEdgeView]


class ProjectGraphSourceGroupView(ContractModel):
    source_revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    document_revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    source_name: str | None = None
    evidence: list[ProjectGraphEvidenceView]


class ProjectGraphNodeDetailView(ContractModel):
    graph_version: StrictInt
    node: ProjectGraphNodeView
    community: ProjectGraphCommunityView | None = None
    sources: list[ProjectGraphSourceGroupView] = []
    relations: list[ProjectGraphRelationGroupView] = []
    neighbors: list[ProjectGraphNodeView] = []


class ProjectGraphNeighborRequest(ContractModel):
    node_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    depth: Annotated[StrictInt, Field(ge=1, le=2)] = 1
    node_limit: Annotated[StrictInt, Field(ge=1, le=500)] = 50
    source_revision_ids: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]],
        Field(max_length=50),
    ] = []
    graph_version: StrictInt | None = None


class ProjectGraphHighlightRequest(ContractModel):
    edge_ids: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]],
        Field(min_length=1, max_length=50),
    ]
    graph_version: StrictInt | None = None


class GraphFragmentRetryView(ContractModel):
    revision_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    requeued_batches: StrictInt
    job_status: Literal["PENDING", "RUNNING", "READY", "FAILED"]


class TestPlanGenerationRequestBody(ContractModel):
    conversation_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    turn_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    objective: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class TestPlanGenerationAccepted(ContractModel):
    job_id: str
    test_plan_id: str
    revision_id: str
    status: Literal["PENDING", "RUNNING", "WAITING", "DRAFT_READY", "FAILED", "CANCELED"]
    progress: Literal["queued", "running", "waiting", "completed", "failed", "canceled"]
    failure_code: str | None = None
    deep_link: str
    row_version: StrictInt


class TestPlanStepView(ContractModel):
    step_id: str
    ordinal: StrictInt
    keyword: Literal["Given", "When", "Then", "And", "But"]
    text: str
    expected_result: str | None = None
    critical: bool
    citation_ids: list[str] = []
    unknown_ids: list[str] = []


class TestPlanScenarioView(ContractModel):
    scenario_id: str
    ordinal: StrictInt
    title: str
    steps: list[TestPlanStepView]


class TestPlanCaseView(ContractModel):
    case_id: str
    ordinal: StrictInt
    title: str
    objective: str
    critical: bool
    scenarios: list[TestPlanScenarioView]
    covered_requirement_ids: list[str] = []


class TestPlanCitationView(ContractModel):
    citation_id: str
    source_revision_id: str
    document_revision_id: str
    chunk_id: str
    content_digest: CanonicalSha256
    claim_text: str
    origin: Literal["SOURCE", "GRAPH_EXTRACTED"]
    evidence_preview_url: str | None = None
    anchor: dict[str, object] | None = None


class TestPlanEvidencePreview(ContractModel):
    citation_id: str
    source_revision_id: str
    document_revision_id: str
    chunk_id: str
    content_digest: CanonicalSha256
    claim_text: str
    origin: Literal["SOURCE", "GRAPH_EXTRACTED", "GRAPH_INFERRED"]
    anchor: dict[str, object]


class TestPlanTextFactView(ContractModel):
    fact_id: str
    text: str
    graph_edge_id: str | None = None
    requirement_ref: str | None = None


class TestPlanCoverageGapView(ContractModel):
    gap_id: str
    requirement_ref: str
    reason: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class TestPlanReviewDecisionView(ContractModel):
    decision_id: str
    disposition: Literal["PENDING", "ACCEPTED_UNCHANGED", "ACCEPTED_MODIFIED", "REJECTED"]
    reason: str
    actor_id: str
    reviewed_content_digest: CanonicalSha256
    created_at: datetime | None = None


class TestPlanReviewRequest(ContractModel):
    disposition: Literal["PENDING", "ACCEPTED_UNCHANGED", "ACCEPTED_MODIFIED", "REJECTED"]
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class TestPlanReviewSummaryView(ContractModel):
    reviewed_count: StrictInt
    unchanged_count: StrictInt
    modified_count: StrictInt
    rejected_count: StrictInt
    unchanged_adoption_rate: float | None
    total_adoption_rate: float | None


class TestPlanRevisionView(ContractModel):
    test_plan_id: str
    revision_id: str
    version: StrictInt
    row_version: StrictInt
    title: str
    objective: str
    scope_items: list[str]
    prerequisites: list[str]
    risks: list[str]
    status: Literal["DRAFT", "VALIDATING", "PUBLISHED", "SUPERSEDED"]
    origin: Literal["VALIDATION", "PRODUCT"]
    adopted_from_revision_id: str | None = None
    content_digest: CanonicalSha256
    validation_digest: CanonicalSha256 | None = None
    cases: list[TestPlanCaseView]
    citations: list[TestPlanCitationView]
    assumptions: list[TestPlanTextFactView]
    unknowns: list[TestPlanTextFactView]
    coverage_gaps: list[TestPlanCoverageGapView]
    requirement_scope_id: str | None = None
    requirement_scope_version: StrictInt | None = None
    requirement_scope_digest: CanonicalSha256 | None = None
    requirement_ids: list[str] = []
    covered_requirement_ids: list[str] = []
    coverage_denominator: StrictInt = 0
    covered_requirement_count: StrictInt = 0
    approved_knowledge_revision_ids: list[str] = []
    model_revision_id: str | None = None
    agent_revision_id: str | None = None
    skill_revision_ids: list[str] = []
    author_actor_id: str | None = None
    strict_review_required: bool = False
    generated_content_digest: CanonicalSha256 | None = None
    review_decisions: list[TestPlanReviewDecisionView] = []
    needs_review: bool = False
    needs_review_reason: str | None = None
    deep_link: str


class TestPlanRevisionPage(ContractModel):
    items: list[TestPlanRevisionView]


class TestPlanRevisionUpdate(ContractModel):
    title: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    objective: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    scope_items: list[str]
    prerequisites: list[str]
    risks: list[str]
    cases: list[TestPlanCaseView]
    citations: list[TestPlanCitationView]
    assumptions: list[TestPlanTextFactView]
    unknowns: list[TestPlanTextFactView]
    coverage_gaps: list[TestPlanCoverageGapView]


class KnowledgeChunkSettings(ContractModel):
    mode: Literal["general", "parent_child"] = "general"
    parent_mode: Literal["paragraph", "full_doc"] = "paragraph"
    separator: str = Field(default="\n\n", max_length=100)
    max_length: int = Field(default=1024, ge=1, le=32768)
    overlap: int = Field(default=50, ge=0, le=32767)
    child_separator: str = Field(default="\n", max_length=100)
    child_max_length: int = Field(default=256, ge=1, le=32768)
    replace_whitespace: bool = False
    remove_urls: bool = False


class KnowledgeChunk(ContractModel):
    chunk_id: str
    content: str
    enabled: bool
    edited: bool
    position: int
    char_count: int
    tokens: int
    keywords: list[str] = Field(default_factory=list)
    summary: str | None = None
    children: list["KnowledgeChunk"] = Field(default_factory=list)
    index_status: Literal["pending", "ready", "error"]
    index_error: str | None = None
    version: int


class KnowledgeChunkPage(ContractModel):
    items: list[KnowledgeChunk]
    total: int
    page: int
    page_size: int


class KnowledgeChunkPreview(ContractModel):
    items: list[KnowledgeChunk]
    total: int


class KnowledgeChunkSettingsView(ContractModel):
    settings: KnowledgeChunkSettings
    version: int
    original_revision_id: str


class KnowledgeChunkSettingsRequest(ContractModel):
    settings: KnowledgeChunkSettings


class KnowledgeChunkSettingsSave(KnowledgeChunkSettingsRequest):
    version: int = Field(ge=1)
    confirm_replace: bool = False


class KnowledgeChunkCreate(ContractModel):
    content: str = Field(min_length=1, max_length=32768)


class KnowledgeChunkChange(ContractModel):
    version: int = Field(ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=32768)
    enabled: bool | None = None
    regenerate_children: bool = False


class KnowledgeChunkVersion(ContractModel):
    chunk_id: str
    version: int = Field(ge=1)


class KnowledgeChunkBatch(ContractModel):
    action: Literal["enable", "disable", "delete", "retry"]
    items: list[KnowledgeChunkVersion] = Field(min_length=1, max_length=1000)


class KnowledgeChunkImport(ContractModel):
    contents: list[str] = Field(min_length=1, max_length=1000)


class KnowledgeChunkFailure(ContractModel):
    chunk_id: str
    error: str


class KnowledgeChunkBatchResult(ContractModel):
    succeeded: list[str]
    failed: list[KnowledgeChunkFailure]


class PromptSuggestionSource(ContractModel):
    source_id: Annotated[str, Field(strict=True, pattern=r"^src_[0-9a-f]{32}$")]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=255)]


class PromptSuggestionItem(ContractModel):
    id: ShortIdentifier
    question: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    sources: Annotated[list[PromptSuggestionSource], Field(min_length=1, max_length=3)]


class PromptSuggestionPage(ContractModel):
    items: list[PromptSuggestionItem]
