"""Provider-neutral request and result values used by Knowledge ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.knowledge.domain.models import (
    ContentRole,
    ContextSnapshot,
    IndexRevision,
    QueryPlan,
    SourceFamily,
    SourceRevisionRef,
)


@dataclass(frozen=True, slots=True)
class SearchExecution:
    policy: RetrievalPolicyContext
    plan: QueryPlan
    context_snapshot: ContextSnapshot
    query_vector: tuple[float, ...]


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
    root_id: str | None = None
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.local_rank) is not int or self.local_rank < 1:
            raise ValueError("search hit local rank must be a positive integer")


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    input_tokens: int
    total_tokens: int
    response_cost_usd: Decimal | None
    calculated_cost_cny: Decimal | None = None

    def __post_init__(self) -> None:
        if (
            type(self.input_tokens) is not int
            or type(self.total_tokens) is not int
            or not 0 <= self.input_tokens <= 1_000_000
            or not self.input_tokens <= self.total_tokens <= 1_000_000
        ):
            raise ValueError("embedding usage tokens are outside the closed bounds")
        costs = (self.response_cost_usd, self.calculated_cost_cny)
        if sum(cost is not None for cost in costs) > 1:
            raise ValueError("embedding usage cannot mix cost currencies")
        for cost in costs:
            exponent = (
                cost.as_tuple().exponent if type(cost) is Decimal and cost.is_finite() else None
            )
            if cost is not None and (
                type(cost) is not Decimal
                or not cost.is_finite()
                or cost < 0
                or cost > Decimal("100")
                or type(exponent) is not int
                or not -18 <= exponent <= 0
                or len(cost.as_tuple().digits) > 21
            ):
                raise ValueError("embedding response cost is outside the closed bounds")


@dataclass(frozen=True, slots=True)
class Embedding:
    vector: tuple[float, ...] = field(repr=False)
    model_id: str
    provider_request_id: str | None
    gateway_call_id: str | None = None
    gateway_model_id: str | None = None
    provider_model_id: str | None = None
    completion_id: str | None = None
    usage: EmbeddingUsage | None = None


@dataclass(frozen=True, slots=True)
class RedactionResult:
    sanitized_text: str
    redaction_version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sanitized_text, str)
            or not self.sanitized_text.strip()
            or len(self.sanitized_text) > 8_000
        ):
            raise ValueError("redacted query must contain between one and 8,000 characters")
        if (
            not isinstance(self.redaction_version, str)
            or not self.redaction_version
            or len(self.redaction_version) > 128
        ):
            raise ValueError("redaction version must be a bounded identifier")


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
