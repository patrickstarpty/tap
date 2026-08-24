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
    citation_ids: list[str]


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
    citations: list[RetrievalCitation]


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
