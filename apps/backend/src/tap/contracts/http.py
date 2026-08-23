"""Public HTTP DTOs for the first Knowledge Chat contract slice."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel
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


class DocumentAnchor(ContractModel):
    type: Literal["document"]
    heading_path: list[str] | None = None
    page: int | None = None
    bbox: list[float] | None = None
    start_offset: int | None = None
    end_offset: int | None = None


class CodeAnchor(ContractModel):
    type: Literal["code"]
    repo: str = Field(min_length=1)
    path: str = Field(min_length=1)
    symbol: str | None = None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class BddAnchor(ContractModel):
    type: Literal["bdd"]
    feature_id: str = Field(min_length=1)
    scenario_id: str | None = None
    step_id: str | None = None


class OpenApiAnchor(ContractModel):
    type: Literal["openapi"]
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    json_pointer: str = Field(min_length=1)


class FailureAnchor(ContractModel):
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
    """A closed, structural location inside one authorized source family."""


class ResourceRef(ContractModel):
    """Browser-provided retrieval intent; it cannot contain policy or ACL facts."""

    family: SourceFamily
    source_id: str = Field(min_length=1)
    mode: ResourceMode = ResourceMode.PREFERRED
    requested_revision: str | None = None
    anchor: StructuralAnchor | None = None


class ChatTurnRequest(ContractModel):
    """A browser request to create one turn in an existing chat."""

    client_request_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    answer_mode: AnswerMode = AnswerMode.QUICK
    source_scope: list[SourceFamily] | None = None
    resource_refs: list[ResourceRef] | None = None
    requested_environment: str | None = None
    requested_corpus_version: str | None = None


class RetrievalSearchRequest(ContractModel):
    """Browser-visible retrieval intent; all authoritative scope is omitted."""

    query: str = Field(min_length=1, max_length=8_000)
    answer_mode: AnswerMode = AnswerMode.QUICK
    sources: list[SourceFamily] | None = Field(default=None, max_length=4)
    resource_refs: list[ResourceRef] | None = Field(default=None, max_length=20)
    requested_environment: str | None = Field(default=None, min_length=1, max_length=128)
    requested_corpus_version: str | None = Field(default=None, min_length=1, max_length=128)
    top_k: int | None = Field(default=None, ge=1, le=100)


class RetrievalAnswerRequest(RetrievalSearchRequest):
    """Grounded-answer intent with the same narrowing-only search fields."""


class RetrievalSourceRevision(ContractModel):
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    revision_kind: RevisionKind
    revision: str = Field(min_length=1)
    source_content_hash: str = Field(min_length=1)
    anchor: StructuralAnchor


class RetrievalScores(ContractModel):
    exact: float | None = None
    bm25: float | None = None
    vector: float | None = None
    rrf: float | None = None
    rerank: float | None = None


class RetrievalHit(ContractModel):
    index_family: SourceFamily
    chunk_id: str = Field(min_length=1)
    logical_chunk_id: str = Field(min_length=1)
    title: str | None = None
    content: str
    source: RetrievalSourceRevision
    chunk_content_hash: str = Field(min_length=1)
    content_role: ContentRole
    citation_id: str = Field(min_length=1)
    evidence_label: str = Field(min_length=1)
    scores: RetrievalScores
    acl_decision_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    embedding_model_version: str = Field(min_length=1)


class RetrievalCitation(ContractModel):
    citation_id: str = Field(min_length=1)
    evidence_label: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    logical_chunk_id: str = Field(min_length=1)
    source: RetrievalSourceRevision
    chunk_content_hash: str = Field(min_length=1)
    content_role: ContentRole
    derived_from_chunk_ids: list[str] | None = None


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
