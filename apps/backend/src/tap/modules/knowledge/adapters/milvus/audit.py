"""Fixed-shape, secret-free audit contract for Milvus search attempts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class MilvusSearchAuditEvent:
    outcome: Literal["success", "failure"]
    provider: Literal["milvus"]
    query_plan_id: str
    acl_digest: str
    alias: str
    physical_collection: str | None
    schema_version: str
    corpus_version: str
    embedding_model_version: str
    provider_row_count: int
    rejected_row_count: int
    elapsed_milliseconds: int
    provider_request_ids: tuple[str, ...]
    error_code: Literal["unavailable", "bounds"] | None


class SearchAuditSink(Protocol):
    async def emit(self, event: MilvusSearchAuditEvent) -> None: ...
