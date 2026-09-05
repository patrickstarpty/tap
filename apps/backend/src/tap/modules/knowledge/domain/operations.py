"""Closed operator requests, fenced claims and secret-free observed results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from tap.modules.access.domain.context import ProjectScopeContext, require_identifier

COMMANDS = frozenset({"recover-uploads", "scavenge-staging", "rebuild-milvus", "reconcile-all"})
COUNT_KEYS = frozenset(
    {
        "processed_count",
        "recovered_count",
        "removed_count",
        "rebuilt_count",
        "failed_count",
        "skipped_count",
    }
)


class OperationBusy(RuntimeError):
    """An active lease already owns this operation."""


class OperationLeaseLost(RuntimeError):
    """The operation claim expired or was superseded."""


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationRequest:
    command: str
    limit: int
    idempotency_key: str
    correlation_id: str

    def __post_init__(self) -> None:
        if self.command not in COMMANDS:
            raise ValueError("unknown operator command")
        if type(self.limit) is not int or not 1 <= self.limit <= 500:
            raise ValueError("operator limit must be between 1 and 500")
        require_identifier("idempotency_key", self.idempotency_key)
        require_identifier("correlation_id", self.correlation_id)

    def digest(self, scope: ProjectScopeContext) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    scope.enterprise_id,
                    scope.project_id,
                    scope.actor_id,
                    scope.identity_mode.value,
                    self.command,
                    self.limit,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationResult:
    outcome: str
    counts: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "partial", "failed"}:
            raise ValueError("invalid operator outcome")
        if not isinstance(self.counts, Mapping) or not set(self.counts) <= COUNT_KEYS:
            raise ValueError("invalid operator counts")
        if any(
            type(count) is not int or not 0 <= count <= 1_000_000 for count in self.counts.values()
        ):
            raise ValueError("invalid operator count")
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))

    def to_dict(self) -> dict[str, object]:
        return {"outcome": self.outcome, "counts": dict(self.counts)}

    @property
    def digest(self) -> str:
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationClaim:
    scope: ProjectScopeContext
    operation_id: str
    command: str
    limit: int
    correlation_id: str
    fence: int
    claim_token: str
    result: OperationResult | None = None
