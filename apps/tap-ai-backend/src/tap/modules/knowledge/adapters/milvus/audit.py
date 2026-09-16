"""Fixed-shape, secret-free audit contract for Milvus search attempts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

from tap.modules.access.domain.context import require_identifier


@dataclass(frozen=True, slots=True)
class SearchAuditMetadata:
    enterprise_id: str
    project_id: str
    query_hash: str
    policy_digest: str
    policy_version: str
    redaction_version: str
    candidate_limit: int

    def validate(self) -> None:
        for name in ("enterprise_id", "project_id", "policy_version", "redaction_version"):
            require_identifier(name, getattr(self, name))
        for digest in (self.query_hash, self.policy_digest):
            if type(digest) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                raise ValueError("invalid search audit digest")
        if self.redaction_version != "tapper-pattern-egress-v1":
            raise ValueError("invalid search audit redaction version")
        if type(self.candidate_limit) is not int or not 1 <= self.candidate_limit <= 100:
            raise ValueError("invalid search audit candidate limit")


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
    metadata: SearchAuditMetadata | None = None


class SearchAuditSink(Protocol):
    async def emit(self, event: MilvusSearchAuditEvent) -> None: ...
