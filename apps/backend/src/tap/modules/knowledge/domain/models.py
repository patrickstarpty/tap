"""Closed, framework-free values exposed by the Knowledge application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


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


class RetrievalProfileId(str, Enum):
    QUICK_HYBRID_V1 = "quick-hybrid-v1"
    DEEP_HYBRID_V1 = "deep-hybrid-v1"


class AbstentionReason(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_SOURCES = "conflicting_sources"
    REVISION_MISMATCH = "revision_mismatch"


@dataclass(frozen=True, slots=True)
class DocumentAnchor:
    heading_path: tuple[str, ...] = ()
    page: int | None = None
    bbox: tuple[float, ...] = ()
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True, slots=True)
class CodeAnchor:
    repo: str
    path: str
    symbol: str | None
    line_start: int
    line_end: int

    def __post_init__(self) -> None:
        if not self.repo or not self.path or self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("code anchor must describe a non-empty ordered line range")


@dataclass(frozen=True, slots=True)
class BddAnchor:
    feature_id: str
    scenario_id: str | None = None
    step_id: str | None = None


@dataclass(frozen=True, slots=True)
class OpenApiAnchor:
    method: str
    path: str
    json_pointer: str


@dataclass(frozen=True, slots=True)
class FailureAnchor:
    incident_id: str
    run_id: str | None = None
    time_start: str | None = None
    time_end: str | None = None


StructuralAnchor: TypeAlias = (
    DocumentAnchor | CodeAnchor | BddAnchor | OpenApiAnchor | FailureAnchor
)


def anchor_authorization_key(anchor: StructuralAnchor) -> str:
    """Return a stable locator compared with a server-produced resource grant."""
    if isinstance(anchor, DocumentAnchor):
        headings = "/".join(anchor.heading_path)
        bbox = ",".join(str(value) for value in anchor.bbox)
        return (
            f"document:{headings}:{anchor.page or ''}:{bbox}:"
            f"{anchor.start_offset if anchor.start_offset is not None else ''}:"
            f"{anchor.end_offset if anchor.end_offset is not None else ''}"
        )
    if isinstance(anchor, CodeAnchor):
        return (
            f"code:{anchor.repo}:{anchor.path}:{anchor.symbol or ''}:"
            f"{anchor.line_start}:{anchor.line_end}"
        )
    if isinstance(anchor, BddAnchor):
        return f"bdd:{anchor.feature_id}:{anchor.scenario_id or ''}:{anchor.step_id or ''}"
    if isinstance(anchor, OpenApiAnchor):
        return f"openapi:{anchor.method.upper()}:{anchor.path}:{anchor.json_pointer}"
    return (
        f"failure:{anchor.incident_id}:{anchor.run_id or ''}:"
        f"{anchor.time_start or ''}:{anchor.time_end or ''}"
    )


@dataclass(frozen=True, slots=True)
class ResourceRef:
    family: SourceFamily
    source_id: str
    mode: ResourceMode = ResourceMode.PREFERRED
    requested_revision: str | None = None
    anchor: StructuralAnchor | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("resource source_id must not be empty")


@dataclass(frozen=True, slots=True)
class ResolvedResourceRef:
    family: SourceFamily
    source_id: str
    mode: ResourceMode
    revision_kind: RevisionKind
    revision: str
    source_content_hash: str
    anchor: StructuralAnchor | None


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    answer_mode: AnswerMode = AnswerMode.QUICK
    source_families: tuple[SourceFamily, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    requested_environment: str | None = None
    requested_corpus_version: str | None = None
    top_k: int | None = None

    def __post_init__(self) -> None:
        _validate_retrieval_intent(
            query=self.query,
            source_families=self.source_families,
            resource_refs=self.resource_refs,
            top_k=self.top_k,
        )


@dataclass(frozen=True, slots=True)
class AnswerRequest:
    query: str
    answer_mode: AnswerMode = AnswerMode.QUICK
    source_families: tuple[SourceFamily, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    requested_environment: str | None = None
    requested_corpus_version: str | None = None
    top_k: int | None = None

    def __post_init__(self) -> None:
        _validate_retrieval_intent(
            query=self.query,
            source_families=self.source_families,
            resource_refs=self.resource_refs,
            top_k=self.top_k,
        )

    def as_search_request(self) -> SearchRequest:
        return SearchRequest(
            query=self.query,
            answer_mode=self.answer_mode,
            source_families=self.source_families,
            resource_refs=self.resource_refs,
            requested_environment=self.requested_environment,
            requested_corpus_version=self.requested_corpus_version,
            top_k=self.top_k,
        )


@dataclass(frozen=True, slots=True)
class SourceRevisionRef:
    source_id: str
    source_type: str
    revision_kind: RevisionKind
    revision: str
    source_content_hash: str
    anchor: StructuralAnchor


@dataclass(frozen=True, slots=True)
class IndexRevision:
    """Internal search provenance, deliberately omitted from public HTTP DTOs."""

    physical_index: str
    schema_version: str
    corpus_version: str


@dataclass(frozen=True, slots=True)
class Evidence:
    family: SourceFamily
    chunk_id: str
    logical_chunk_id: str
    title: str | None
    content: str
    source: SourceRevisionRef
    chunk_content_hash: str
    content_role: ContentRole
    citation_id: str
    evidence_label: str
    index_revision: IndexRevision
    embedding_model_version: str
    acl_decision_id: str
    score: float
    derived_from_chunk_ids: tuple[str, ...] = ()
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    evidence_label: str
    chunk_id: str
    logical_chunk_id: str
    source: SourceRevisionRef
    chunk_content_hash: str
    content_role: ContentRole
    derived_from_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    text: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchResponse:
    trace_id: str
    query_plan_id: str
    context_snapshot_id: str
    corpus_version: str
    retrieval_profile_id: RetrievalProfileId
    evidence: tuple[Evidence, ...]
    degraded_mode: bool = False
    degradation_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerResponse:
    trace_id: str
    query_plan_id: str
    context_snapshot_id: str
    corpus_version: str
    retrieval_profile_id: RetrievalProfileId
    answer: str
    abstained: bool
    claims: tuple[Claim, ...]
    citations: tuple[Citation, ...]
    abstention_reason: AbstentionReason | None = None
    degraded_mode: bool = False
    degradation_reasons: tuple[str, ...] = ()


def _validate_retrieval_intent(
    *,
    query: str,
    source_families: tuple[SourceFamily, ...],
    resource_refs: tuple[ResourceRef, ...],
    top_k: int | None,
) -> None:
    if not isinstance(query, str) or not query.strip() or len(query) > 8_000:
        raise ValueError("retrieval query must contain between one and 8,000 characters")
    if len(source_families) > len(SourceFamily):
        raise ValueError("source family preference exceeds the closed family set")
    if len(resource_refs) > 20:
        raise ValueError("resource reference count exceeds the request bound")
    if top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 100):
        raise ValueError("top_k must be an integer between one and 100")
