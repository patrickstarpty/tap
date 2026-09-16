"""Closed, framework-free values exposed by the Knowledge application boundary."""

from __future__ import annotations

import math
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


class ContextLayerKind(str, Enum):
    PROJECT_POLICY = "project_policy"
    PROJECT_CONTEXT = "project_context"
    RECENT_TURNS = "recent_turns"
    CONVERSATION_SUMMARY = "conversation_summary"
    CURRENT_TURN = "current_turn"


@dataclass(frozen=True, slots=True)
class DocumentAnchor:
    heading_path: tuple[str, ...] = ()
    page: int | None = None
    bbox: tuple[float, ...] = ()
    start_offset: int | None = None
    end_offset: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.heading_path, tuple)
            or len(self.heading_path) > 32
            or any(
                not isinstance(item, str) or not item or len(item) > 256
                for item in self.heading_path
            )
        ):
            raise ValueError("document heading path exceeds the immutable bound")
        _optional_strict_int("document page", self.page, minimum=1)
        _optional_strict_int("document start offset", self.start_offset, minimum=0)
        _optional_strict_int("document end offset", self.end_offset, minimum=0)
        if (
            not isinstance(self.bbox, tuple)
            or len(self.bbox) not in {0, 4}
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in self.bbox
            )
        ):
            raise ValueError("document bounding box must contain four finite numbers")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset < self.start_offset
        ):
            raise ValueError("document offsets must be ordered")


@dataclass(frozen=True, slots=True)
class CodeAnchor:
    repo: str
    path: str
    symbol: str | None
    line_start: int
    line_end: int

    def __post_init__(self) -> None:
        _bounded_string("code repository", self.repo, maximum=256)
        _bounded_string("code path", self.path, maximum=2_048)
        _optional_bounded_string("code symbol", self.symbol, maximum=512)
        _strict_int("code line start", self.line_start, minimum=1)
        _strict_int("code line end", self.line_end, minimum=1)
        if self.line_end < self.line_start:
            raise ValueError("code anchor must describe a non-empty ordered line range")


@dataclass(frozen=True, slots=True)
class BddAnchor:
    feature_id: str
    scenario_id: str | None = None
    step_id: str | None = None

    def __post_init__(self) -> None:
        _bounded_string("BDD feature", self.feature_id, maximum=256)
        _optional_bounded_string("BDD scenario", self.scenario_id, maximum=256)
        _optional_bounded_string("BDD step", self.step_id, maximum=256)


@dataclass(frozen=True, slots=True)
class OpenApiAnchor:
    method: str
    path: str
    json_pointer: str

    def __post_init__(self) -> None:
        _bounded_string("OpenAPI method", self.method, maximum=16)
        _bounded_string("OpenAPI path", self.path, maximum=2_048)
        _bounded_string("OpenAPI JSON pointer", self.json_pointer, maximum=4_096)


@dataclass(frozen=True, slots=True)
class FailureAnchor:
    incident_id: str
    run_id: str | None = None
    time_start: str | None = None
    time_end: str | None = None

    def __post_init__(self) -> None:
        _bounded_string("failure incident", self.incident_id, maximum=256)
        _optional_bounded_string("failure run", self.run_id, maximum=256)
        _optional_bounded_string("failure start time", self.time_start, maximum=128)
        _optional_bounded_string("failure end time", self.time_end, maximum=128)


StructuralAnchor: TypeAlias = (
    DocumentAnchor | CodeAnchor | BddAnchor | OpenApiAnchor | FailureAnchor
)
STRUCTURAL_ANCHOR_TYPES = (
    DocumentAnchor,
    CodeAnchor,
    BddAnchor,
    OpenApiAnchor,
    FailureAnchor,
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
        if not isinstance(self.family, SourceFamily) or not isinstance(self.mode, ResourceMode):
            raise TypeError("resource family and mode must be closed values")
        _bounded_string("resource source ID", self.source_id, maximum=1_024)
        _optional_bounded_string(
            "resource requested revision",
            self.requested_revision,
            maximum=512,
        )
        if self.anchor is not None and not isinstance(self.anchor, STRUCTURAL_ANCHOR_TYPES):
            raise TypeError("resource anchor must be a closed structural anchor")


@dataclass(frozen=True, slots=True)
class FilterableSubtree:
    root_ids: tuple[str, ...] = ()
    parent_ids: tuple[str, ...] = ()
    logical_chunk_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, values in (
            ("root_ids", self.root_ids),
            ("parent_ids", self.parent_ids),
            ("logical_chunk_ids", self.logical_chunk_ids),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > 32
                or any(
                    not isinstance(value, str) or not value or len(value) > 256 for value in values
                )
            ):
                raise ValueError(f"{name} must be a bounded immutable identifier tuple")
        if not (self.root_ids or self.parent_ids or self.logical_chunk_ids):
            raise ValueError("filterable subtree must contain at least one locator")


@dataclass(frozen=True, slots=True)
class ResolvedResourceRef:
    family: SourceFamily
    source_id: str
    mode: ResourceMode
    revision_kind: RevisionKind
    revision: str
    source_content_hash: str
    anchor: StructuralAnchor | None
    subtree: FilterableSubtree | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.family, SourceFamily)
            or not isinstance(self.mode, ResourceMode)
            or not isinstance(self.revision_kind, RevisionKind)
        ):
            raise TypeError("resolved resource uses values outside the closed model")
        _bounded_string("resolved source ID", self.source_id, maximum=1_024)
        _immutable_revision(
            "resolved source revision",
            self.revision_kind,
            self.revision,
        )
        _digest("resolved source content hash", self.source_content_hash)
        if self.anchor is not None and not isinstance(self.anchor, STRUCTURAL_ANCHOR_TYPES):
            raise TypeError("resolved resource anchor must be a closed structural anchor")
        if self.subtree is not None and not isinstance(self.subtree, FilterableSubtree):
            raise TypeError("resolved resource subtree must be a trusted filterable subtree")
        if self.anchor is None and self.subtree is not None:
            raise ValueError("resolved subtree must be bound to a structural anchor")


@dataclass(frozen=True, slots=True)
class ContextLayer:
    kind: ContextLayerKind
    ref_ids: tuple[str, ...]
    content_hash: str
    token_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ContextLayerKind):
            raise TypeError("context layer kind must be closed")
        if not isinstance(self.ref_ids, tuple) or len(self.ref_ids) > 32:
            raise ValueError("context layer references exceed the bound")
        if any(not isinstance(item, str) or not item or len(item) > 256 for item in self.ref_ids):
            raise ValueError("context layer references must be bounded strings")
        _digest("context layer content hash", self.content_hash)
        if type(self.token_count) is not int or not 0 <= self.token_count <= 100_000:
            raise ValueError("context layer token count exceeds the bound")


@dataclass(frozen=True, slots=True)
class QueryPlan:
    query_plan_id: str
    operation_id: str
    tenant_id: str
    project_id: str
    policy_decision_id: str
    policy_version: str
    acl_digest: str
    answer_mode: AnswerMode
    retrieval_profile_id: RetrievalProfileId
    source_families: tuple[SourceFamily, ...]
    resources: tuple[ResolvedResourceRef, ...]
    effective_environment: str | None
    corpus_version: str
    candidate_limit: int
    raw_request_hash: str
    sanitized_query: str
    sanitized_query_hash: str
    redaction_version: str
    embedding_model_id: str
    embedding_dimension: int

    def __post_init__(self) -> None:
        for name in (
            "query_plan_id",
            "operation_id",
            "tenant_id",
            "project_id",
            "policy_decision_id",
            "policy_version",
            "acl_digest",
            "corpus_version",
            "redaction_version",
            "embedding_model_id",
        ):
            _bounded_string(name, getattr(self, name), maximum=256)
        if not isinstance(self.answer_mode, AnswerMode) or not isinstance(
            self.retrieval_profile_id, RetrievalProfileId
        ):
            raise TypeError("query plan profile values must be closed")
        if (
            not isinstance(self.source_families, tuple)
            or not self.source_families
            or len(self.source_families) > len(SourceFamily)
            or len(set(self.source_families)) != len(self.source_families)
            or not all(isinstance(item, SourceFamily) for item in self.source_families)
        ):
            raise ValueError("query plan source families are outside the closed bound")
        if (
            not isinstance(self.resources, tuple)
            or len(self.resources) > 20
            or not all(isinstance(item, ResolvedResourceRef) for item in self.resources)
        ):
            raise ValueError("query plan resources exceed the immutable bound")
        if self.effective_environment is not None:
            _bounded_string("effective_environment", self.effective_environment, maximum=128)
        if type(self.candidate_limit) is not int or not 1 <= self.candidate_limit <= 100:
            raise ValueError("query plan candidate limit must be an integer from one to 100")
        _digest("raw request hash", self.raw_request_hash)
        _bounded_string("sanitized query", self.sanitized_query, maximum=8_000)
        _digest("sanitized query hash", self.sanitized_query_hash)
        if type(self.embedding_dimension) is not int or not 1 <= self.embedding_dimension <= 4_096:
            raise ValueError("query plan embedding dimension exceeds the bound")


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    context_snapshot_id: str
    operation_id: str
    tenant_id: str
    project_id: str
    policy_decision_id: str
    policy_version: str
    acl_digest: str
    layers: tuple[ContextLayer, ...]

    def __post_init__(self) -> None:
        for name in (
            "context_snapshot_id",
            "operation_id",
            "tenant_id",
            "project_id",
            "policy_decision_id",
            "policy_version",
            "acl_digest",
        ):
            _bounded_string(name, getattr(self, name), maximum=256)
        if (
            not isinstance(self.layers, tuple)
            or not 1 <= len(self.layers) <= 8
            or not all(isinstance(layer, ContextLayer) for layer in self.layers)
        ):
            raise ValueError("context snapshot layers must contain one to eight bounded layers")


def context_snapshot_binds_query_plan(
    plan: QueryPlan,
    snapshot: ContextSnapshot,
) -> bool:
    """Check the shared operation/policy identity and current-turn lineage."""
    if not isinstance(plan, QueryPlan) or not isinstance(snapshot, ContextSnapshot):
        return False
    current_turn_layers = tuple(
        layer for layer in snapshot.layers if layer.kind is ContextLayerKind.CURRENT_TURN
    )
    return (
        snapshot.operation_id == plan.operation_id
        and snapshot.tenant_id == plan.tenant_id
        and snapshot.project_id == plan.project_id
        and snapshot.policy_decision_id == plan.policy_decision_id
        and snapshot.policy_version == plan.policy_version
        and snapshot.acl_digest == plan.acl_digest
        and len(current_turn_layers) == 1
        and current_turn_layers[0].content_hash == plan.sanitized_query_hash
    )


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
            answer_mode=self.answer_mode,
            source_families=self.source_families,
            resource_refs=self.resource_refs,
            requested_environment=self.requested_environment,
            requested_corpus_version=self.requested_corpus_version,
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
            answer_mode=self.answer_mode,
            source_families=self.source_families,
            resource_refs=self.resource_refs,
            requested_environment=self.requested_environment,
            requested_corpus_version=self.requested_corpus_version,
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

    def __post_init__(self) -> None:
        _bounded_string("source revision source ID", self.source_id, maximum=1_024)
        _bounded_string("source revision source type", self.source_type, maximum=128)
        if not isinstance(self.revision_kind, RevisionKind):
            raise TypeError("source revision kind must be closed")
        _immutable_revision("source revision", self.revision_kind, self.revision)
        _digest("source content hash", self.source_content_hash)
        if not isinstance(self.anchor, STRUCTURAL_ANCHOR_TYPES):
            raise TypeError("source revision anchor must be a closed structural anchor")


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
    root_id: str | None = None
    parent_id: str | None = None

    def __post_init__(self) -> None:
        _digest("evidence chunk content hash", self.chunk_content_hash)


@dataclass(frozen=True, slots=True)
class ModelCallProvenance:
    configured_model_id: str
    provider_request_id: str | None
    gateway_call_id: str | None = None
    gateway_model_id: str | None = None
    provider_model_id: str | None = None
    completion_id: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    family: SourceFamily
    citation_id: str
    evidence_label: str
    chunk_id: str
    logical_chunk_id: str
    source: SourceRevisionRef
    chunk_content_hash: str
    content_role: ContentRole
    derived_from_chunk_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.family, SourceFamily):
            raise TypeError("citation family must be closed")
        _digest("citation chunk content hash", self.chunk_content_hash)


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    text: str
    answer_start: int
    answer_end: int
    citation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _strict_int("claim answer start", self.answer_start, minimum=0)
        _strict_int("claim answer end", self.answer_end, minimum=0)
        if self.answer_end < self.answer_start:
            raise ValueError("claim answer offsets must be ordered")


@dataclass(frozen=True, slots=True)
class SearchResponse:
    trace_id: str
    query_plan_id: str
    context_snapshot_id: str
    corpus_version: str
    retrieval_profile_id: RetrievalProfileId
    evidence: tuple[Evidence, ...]
    embedding_provenance: ModelCallProvenance
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
    embedding_provenance: ModelCallProvenance
    answer_provenance: ModelCallProvenance | None
    abstention_reason: AbstentionReason | None = None
    degraded_mode: bool = False
    degradation_reasons: tuple[str, ...] = ()


def _validate_retrieval_intent(
    *,
    query: str,
    answer_mode: AnswerMode,
    source_families: tuple[SourceFamily, ...],
    resource_refs: tuple[ResourceRef, ...],
    requested_environment: str | None,
    requested_corpus_version: str | None,
    top_k: int | None,
) -> None:
    if not isinstance(query, str) or not query.strip() or len(query) > 8_000:
        raise ValueError("retrieval query must contain between one and 8,000 characters")
    if not isinstance(answer_mode, AnswerMode):
        raise TypeError("answer mode must be a closed value")
    if (
        not isinstance(source_families, tuple)
        or len(source_families) > len(SourceFamily)
        or len(set(source_families)) != len(source_families)
        or not all(isinstance(item, SourceFamily) for item in source_families)
    ):
        raise ValueError("source family preference exceeds the closed family set")
    if (
        not isinstance(resource_refs, tuple)
        or len(resource_refs) > 20
        or not all(isinstance(item, ResourceRef) for item in resource_refs)
    ):
        raise ValueError("resource reference count exceeds the request bound")
    _optional_bounded_string(
        "requested environment",
        requested_environment,
        maximum=128,
    )
    _optional_bounded_string(
        "requested corpus version",
        requested_corpus_version,
        maximum=128,
    )
    if top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 100):
        raise ValueError("top_k must be an integer between one and 100")


def _bounded_string(name: str, value: object, *, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")


def _optional_bounded_string(name: str, value: object, *, maximum: int) -> None:
    if value is not None:
        _bounded_string(name, value, maximum=maximum)


def _strict_int(name: str, value: object, *, minimum: int) -> None:
    if type(value) is not int or not minimum <= value <= 2_147_483_647:
        raise ValueError(f"{name} must be a strict bounded integer")


def _optional_strict_int(name: str, value: object, *, minimum: int) -> None:
    if value is not None:
        _strict_int(name, value, minimum=minimum)


def _digest(name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be a canonical sha256 digest")


def _immutable_revision(name: str, kind: RevisionKind, value: str) -> None:
    _bounded_string(name, value, maximum=512)
    if kind is RevisionKind.GIT_COMMIT and (
        len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a canonical Git commit ID")
