"""Closed internal Project event registry, separate from browser contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class EventDefinition:
    aggregate_types: frozenset[str]
    aggregate_field: str
    fields: Mapping[str, tuple[str, ...]]
    compatibility: bool = False


def _definition(
    aggregate: str, identity: str, fields: str, **enums: tuple[str, ...]
) -> EventDefinition:
    return EventDefinition(
        frozenset({aggregate}),
        identity,
        MappingProxyType({name: enums.get(name, ()) for name in fields.split()}),
    )


_OPERATION = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT")
_TEST_OUTCOME = ("NOT_RUN", "PASSED", "FAILED", "INCONCLUSIVE")
_COMPATIBILITY = {
    "turn.process_requested": frozenset({"turn", "chat_turn"}),
    "chat.event_appended": frozenset({"turn", "chat_turn"}),
    "knowledge.ingestion_requested": frozenset({"knowledge_document"}),
    "knowledge.deletion_requested": frozenset({"knowledge_document"}),
}
COMPATIBILITY_TYPES: Mapping[str, frozenset[str]] = MappingProxyType(_COMPATIBILITY)
EVENT_REGISTRY: Mapping[str, EventDefinition] = MappingProxyType(
    {
        **{
            name: EventDefinition(
                types, "aggregateId", MappingProxyType({"aggregateId": (), "sequence": ()}), True
            )
            for name, types in _COMPATIBILITY.items()
        },
        "knowledge.document-revision.accepted": _definition(
            "DocumentRevision", "revisionId", "sourceId documentId revisionId contentHash"
        ),
        "knowledge.document-revision.ready": _definition(
            "DocumentRevision", "revisionId", "revisionId chunkManifestDigest projectionDigest"
        ),
        "knowledge.operator.completed": _definition(
            "KnowledgeOperation",
            "operationId",
            "operationId command outcome resultDigest",
            command=("recover-uploads", "scavenge-staging", "rebuild-milvus", "reconcile-all"),
            outcome=("completed", "partial", "failed"),
        ),
        "knowledge.graph-snapshot.requested": _definition(
            "GraphSnapshot", "snapshotId", "snapshotId sourceRevisionIds extractionProfileDigest"
        ),
        "knowledge.graph-snapshot.ready": _definition(
            "GraphSnapshot", "snapshotId", "snapshotId graphDigest evidenceDigest"
        ),
        "conversation.turn.requested": _definition(
            "Turn", "turnId", "conversationId turnId inputSnapshotDigest"
        ),
        "conversation.turn.completed": _definition(
            "Turn",
            "turnId",
            "turnId answerEvidenceSnapshotId answerEvidenceSnapshotDigest outcome",
            outcome=("completed", "abstained", "canceled", "failed"),
        ),
        "test-plan.generation.requested": _definition(
            "TestPlanRevision",
            "revisionId",
            "revisionId inputSnapshotDigest answerEvidenceSnapshotDigest requestDigest",
        ),
        "test-plan.revision.published": _definition(
            "TestPlanRevision", "revisionId", "revisionId contentDigest validationDigest"
        ),
        "automation.generation.requested": _definition(
            "AutomationRevision",
            "revisionId",
            "revisionId inputSnapshotDigest answerEvidenceSnapshotDigest requestDigest",
        ),
        "automation.revision.published": _definition(
            "AutomationRevision", "revisionId", "revisionId testIrDigest bundleManifestDigest"
        ),
        "automation.debug-execution.requested": _definition(
            "DebugExecution",
            "debugExecutionId",
            "debugExecutionId draftDigest environmentRevisionId",
        ),
        "automation.debug-execution.status-changed": _definition(
            "DebugExecution",
            "debugExecutionId",
            "debugExecutionId from to sequence",
            **{"from": _OPERATION, "to": _OPERATION},
        ),
        "automation.debug-execution.completed": _definition(
            "DebugExecution",
            "debugExecutionId",
            "debugExecutionId outcome evidenceManifestDigest",
            outcome=_TEST_OUTCOME,
        ),
        "recorder.session.requested": _definition(
            "RecorderSession", "sessionId", "sessionId environmentRevisionId policyDigest"
        ),
        "recorder.session.completed": _definition(
            "RecorderSession",
            "sessionId",
            "sessionId eventManifestDigest outcome",
            outcome=_OPERATION[2:],
        ),
        "execution.run.requested": _definition(
            "ExecutionRun", "runId", "runId submissionKey configurationManifestDigest"
        ),
        "execution.run.status-changed": _definition(
            "ExecutionRun",
            "runId",
            "runId from to sequence observationId",
            **{"from": _OPERATION, "to": _OPERATION},
        ),
        "execution.run.completed": _definition(
            "ExecutionRun",
            "runId",
            "runId outcome evidenceStatus resultManifestDigest",
            outcome=_TEST_OUTCOME,
            evidenceStatus=("PENDING", "COMPLETE", "INCOMPLETE"),
        ),
    }
)


def _identifier(value: object, field: str, *, max_length: int = 128) -> None:
    if not isinstance(value, str) or not value or len(value) > max_length or value.strip() != value:
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
            _identifier(getattr(self, name), name, max_length=64 if name == "aggregate_id" else 128)
        if self.causation_id is not None:
            _identifier(self.causation_id, "causation_id")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported event schema version")
        if self.scope_kind != "PROJECT" or self.identity_mode not in {"validation", "product"}:
            raise ValueError("invalid Project event scope")
        if (
            self.event_type not in EVENT_REGISTRY
            or self.aggregate_type not in EVENT_REGISTRY[self.event_type].aggregate_types
        ):
            raise ValueError("unregistered event/aggregate")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must identify a UTC instant")
        try:
            self.occurred_at.astimezone(timezone.utc)
        except OverflowError as error:
            raise ValueError("occurred_at must be representable in canonical UTC") from error
        definition = EVENT_REGISTRY[self.event_type]
        if not isinstance(self.payload, Mapping) or set(self.payload) != set(definition.fields):
            raise ValueError("invalid registered event payload")
        frozen: dict[str, object] = {}
        for name, choices in definition.fields.items():
            value = self.payload[name]
            if name == "sequence":
                if value is None and definition.compatibility:
                    pass
                elif (
                    type(value) is not int
                    or not (0 if definition.compatibility else 1) <= value <= 2**63 - 1
                ):
                    raise ValueError("invalid event sequence")
            elif name == "sourceRevisionIds":
                if not isinstance(value, (list, tuple)) or not value:
                    raise ValueError("invalid source revision list")
                for item in value:
                    _identifier(item, name)
                value = tuple(value)
            else:
                _identifier(value, name)
                if choices and value not in choices:
                    raise ValueError("invalid registered event value")
            frozen[name] = value
        if self.payload[definition.aggregate_field] != self.aggregate_id:
            raise ValueError("payload aggregate differs from envelope")
        if (
            type(self.aggregate_version) is not int
            or not (0 if definition.compatibility else 1) <= self.aggregate_version <= 2**63 - 1
        ):
            raise ValueError("invalid aggregate version")
        if self.event_type == "knowledge.operator.completed":
            if self.aggregate_version != 1:
                raise ValueError("operator completion version must be one")
            if re.fullmatch(r"sha256:[0-9a-f]{64}", str(self.payload["resultDigest"])) is None:
                raise ValueError("operator result digest must be SHA-256")
        sequence = self.payload.get("sequence")
        if definition.compatibility or "sequence" in self.payload:
            if self.aggregate_version != (sequence if sequence is not None else 0):
                raise ValueError("aggregate version differs from sequence")
        object.__setattr__(self, "payload", MappingProxyType(frozen))

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
            "payload": {
                name: list(value) if isinstance(value, tuple) else value
                for name, value in self.payload.items()
            },
        }

    @classmethod
    def from_dict(cls, value: object) -> ProjectEventEnvelope:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("incomplete or unknown event envelope fields")
        fields = dict(value)
        timestamp = fields["occurred_at"]
        if (
            not isinstance(timestamp, str)
            or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
                timestamp,
            )
            is None
        ):
            raise ValueError("invalid event timestamp")
        fields["occurred_at"] = datetime.fromisoformat(timestamp.upper().replace("Z", "+00:00"))
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


def project_event_schema() -> dict[str, Any]:
    """Generate private JSON Schema from the same registry used by runtime validation.

    JSON Schema checks shape; aggregate equality and scoped resource membership
    remain application invariants, not authorization inferred from identifier text.
    """
    identifier = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^\S(?:[\s\S]*\S)?$",
    }
    integer = {"type": "integer", "minimum": 1, "maximum": 2**63 - 1}
    variants = []
    for event_type, definition in EVENT_REGISTRY.items():
        properties: dict[str, Any] = {
            name: dict(identifier)
            for name in (
                "event_id",
                "enterprise_id",
                "project_id",
                "actor_id",
                "aggregate_id",
                "correlation_id",
                "idempotency_key",
            )
        }
        properties["aggregate_id"]["maxLength"] = 64
        properties.update(
            {
                "event_type": {"const": event_type},
                "schema_version": {"const": 1, "type": "integer"},
                "occurred_at": {"type": "string", "format": "date-time"},
                "scope_kind": {"const": "PROJECT"},
                "identity_mode": {"enum": ["validation", "product"]},
                "aggregate_type": {"enum": sorted(definition.aggregate_types)},
                "aggregate_version": {**integer, "minimum": 0 if definition.compatibility else 1},
                "causation_id": {"anyOf": [identifier, {"type": "null"}]},
            }
        )
        if event_type == "knowledge.operator.completed":
            properties["aggregate_version"] = {"const": 1, "type": "integer"}
        payload: dict[str, Any] = {}
        for name, choices in definition.fields.items():
            if name == "sequence":
                payload[name] = (
                    {"anyOf": [{**integer, "minimum": 0}, {"type": "null"}]}
                    if definition.compatibility
                    else integer
                )
            elif name == "sourceRevisionIds":
                payload[name] = {"type": "array", "minItems": 1, "items": identifier}
            else:
                payload[name] = {**identifier, **({"enum": list(choices)} if choices else {})}
        if event_type == "knowledge.operator.completed":
            payload["resultDigest"] = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
        payload[definition.aggregate_field]["maxLength"] = 64
        properties["payload"] = {
            "type": "object",
            "additionalProperties": False,
            "properties": payload,
            "required": list(payload),
        }
        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": list(ProjectEventEnvelope.__dataclass_fields__),
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ProjectEventEnvelope",
        "oneOf": variants,
    }
