"""Closed internal Project compatibility events, separate from browser contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

COMPATIBILITY_TYPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "turn.process_requested": frozenset({"turn", "chat_turn"}),
        "chat.event_appended": frozenset({"turn", "chat_turn"}),
        "knowledge.ingestion_requested": frozenset({"knowledge_document"}),
        "knowledge.deletion_requested": frozenset({"knowledge_document"}),
    }
)


def _identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128 or value.strip() != value:
        raise ValueError(f"invalid {field}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectEventEnvelope:
    event_id: str
    event_type: str
    schema_version: int
    occurred_at: datetime
    scope_kind: str
    enterprise_id: str
    project_id: str
    actor_id: str
    identity_mode: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "enterprise_id",
            "project_id",
            "actor_id",
            "aggregate_id",
            "correlation_id",
            "idempotency_key",
        ):
            _identifier(getattr(self, name), name)
        if self.causation_id is not None:
            _identifier(self.causation_id, "causation_id")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported event schema version")
        if self.scope_kind != "PROJECT" or self.identity_mode not in {"validation", "product"}:
            raise ValueError("invalid Project event scope")
        if (
            self.event_type not in COMPATIBILITY_TYPES
            or self.aggregate_type not in COMPATIBILITY_TYPES[self.event_type]
        ):
            raise ValueError("unregistered compatibility event/aggregate")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must identify a UTC instant")
        if not isinstance(self.payload, Mapping) or set(self.payload) != {
            "aggregateId",
            "sequence",
        }:
            raise ValueError("invalid compatibility payload")
        sequence = self.payload["sequence"]
        if sequence is not None and (type(sequence) is not int or not 0 <= sequence <= 2**63 - 1):
            raise ValueError("invalid compatibility sequence")
        if self.payload["aggregateId"] != self.aggregate_id:
            raise ValueError("payload aggregate differs from envelope")
        if type(self.aggregate_version) is not int or self.aggregate_version != (
            sequence if sequence is not None else 0
        ):
            raise ValueError("compatibility aggregate version differs from sequence")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        return {
            **{
                name: getattr(self, name)
                for name in self.__dataclass_fields__
                if name not in {"occurred_at", "payload"}
            },
            "occurred_at": self.occurred_at.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, value: object) -> ProjectEventEnvelope:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("incomplete or unknown event envelope fields")
        fields = dict(value)
        timestamp = fields["occurred_at"]
        if not isinstance(timestamp, str):
            raise ValueError("invalid event timestamp")
        fields["occurred_at"] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(**fields)


def event_content_digest(envelope: ProjectEventEnvelope) -> str:
    """Hash only canonical event facts; never use as a business request digest."""
    content = envelope.to_dict()
    for name in ("event_id", "occurred_at", "correlation_id", "causation_id", "idempotency_key"):
        content.pop(name)
    canonical = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
