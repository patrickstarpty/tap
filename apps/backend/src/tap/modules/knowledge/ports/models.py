"""Provider-neutral request and result values used by Knowledge ports."""

from __future__ import annotations

from dataclasses import dataclass

from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.knowledge.domain.models import (
    ContentRole,
    IndexRevision,
    ResolvedResourceRef,
    SourceFamily,
    SourceRevisionRef,
)


@dataclass(frozen=True, slots=True)
class SearchExecution:
    query: str
    query_vector: tuple[float, ...]
    source_families: tuple[SourceFamily, ...]
    resources: tuple[ResolvedResourceRef, ...]
    effective_environment: str | None
    corpus_version: str
    candidate_limit: int
    profile_id: str
    policy: RetrievalPolicyContext


@dataclass(frozen=True, slots=True)
class SearchHit:
    family: SourceFamily
    chunk_id: str
    logical_chunk_id: str
    title: str | None
    content: str
    source: SourceRevisionRef
    chunk_content_hash: str
    content_role: ContentRole
    index_revision: IndexRevision
    embedding_model_version: str
    score: float
    local_rank: int = 1
    derived_from_chunk_ids: tuple[str, ...] = ()
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.local_rank) is not int or self.local_rank < 1:
            raise ValueError("search hit local rank must be a positive integer")


@dataclass(frozen=True, slots=True)
class Embedding:
    vector: tuple[float, ...]
    model_id: str
    provider_request_id: str | None
    gateway_call_id: str | None = None
    gateway_model_id: str | None = None
    provider_model_id: str | None = None
    completion_id: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedClaim:
    text: str
    evidence_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnswerGeneration:
    text: str
    claims: tuple[GeneratedClaim, ...]
    model_id: str
    profile_id: str
    provider_request_id: str | None
    gateway_call_id: str | None = None
    gateway_model_id: str | None = None
    provider_model_id: str | None = None
    completion_id: str | None = None
