"""Immutable Project-scoped Conversation and Turn evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def content_digest(value: object) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(material.encode()).hexdigest()


def citation_evidence_digest(
    *,
    citation_id: str,
    trace_id: str,
    source_id: str,
    document_id: str,
    revision_id: str,
    chunk_id: str,
    source_content_hash: str,
    chunk_content_hash: str,
    anchor: object,
) -> str:
    return content_digest(
        {
            "citationId": citation_id,
            "traceId": trace_id,
            "sourceId": source_id,
            "documentId": document_id,
            "revisionId": revision_id,
            "chunkId": chunk_id,
            "sourceContentHash": source_content_hash,
            "chunkContentHash": chunk_content_hash,
            "anchor": anchor,
        }
    )


class GraphContextStatus(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    APPLIED = "APPLIED"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class FrozenResource:
    source_id: str
    document_id: str
    revision_id: str
    source_content_hash: str

    def __post_init__(self) -> None:
        if not all((self.source_id, self.document_id, self.revision_id)):
            raise ValueError("resolved resource identity must be complete")
        if _DIGEST.fullmatch(self.source_content_hash) is None:
            raise ValueError("resolved resource content hash must be canonical SHA-256")


@dataclass(frozen=True, slots=True)
class TurnInput:
    message: str
    actor_id: str
    identity_mode: str
    model_alias: str
    source_revision_ids: tuple[str, ...] = ()
    document_revision_ids: tuple[str, ...] = ()
    resolved_resources: tuple[FrozenResource, ...] = ()
    agent_revision_id: str | None = None
    agent_revision_digest: str | None = None
    skill_revision_ids: tuple[str, ...] = ()
    skill_revision_digests: tuple[str, ...] = ()
    acl_digest: str = "sha256:" + "0" * 64
    retrieval_policy_digest: str = "sha256:" + "0" * 64

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("blank message")
        if len(self.message) > 20_000:
            raise ValueError("message must be bounded")
        if self.identity_mode not in {"validation", "product"}:
            raise ValueError("invalid identity mode")
        if bool(self.agent_revision_id) != bool(self.agent_revision_digest):
            raise ValueError("agent revision identity and digest must be paired")
        if len(self.skill_revision_ids) != len(self.skill_revision_digests):
            raise ValueError("skill revision identities and digests must be paired")
        if len({item.revision_id for item in self.resolved_resources}) != len(
            self.resolved_resources
        ):
            raise ValueError("resolved resources must be unique")
        for digest in (
            self.acl_digest,
            self.retrieval_policy_digest,
            *self.skill_revision_digests,
            *(() if self.agent_revision_digest is None else (self.agent_revision_digest,)),
        ):
            if _DIGEST.fullmatch(digest) is None:
                raise ValueError("revision and policy digests must be canonical SHA-256")

    def material(self, *, project_id: str, turn_id: str) -> dict[str, object]:
        return {"projectId": project_id, "turnId": turn_id, **asdict(self)}


@dataclass(frozen=True, slots=True)
class TurnInputSnapshot:
    snapshot_id: str
    project_id: str
    turn_id: str
    value: TurnInput
    digest: str
    created_at: datetime

    def __post_init__(self) -> None:
        expected = content_digest(
            self.value.material(project_id=self.project_id, turn_id=self.turn_id)
        )
        if self.digest != expected:
            raise ValueError("input snapshot digest does not match Project/Turn facts")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("snapshot timestamp must be UTC-aware")

    @classmethod
    def create(
        cls, *, snapshot_id: str, project_id: str, turn_id: str, value: TurnInput, now: datetime
    ):
        return cls(
            snapshot_id,
            project_id,
            turn_id,
            value,
            content_digest(value.material(project_id=project_id, turn_id=turn_id)),
            now.astimezone(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class RetrievalSummary:
    status: str
    trace_id: str | None = None
    authorized_hit_count: int = 0

    def __post_init__(self) -> None:
        if self.status not in {"completed", "abstained", "failed", "canceled"}:
            raise ValueError("invalid retrieval status")
        if type(self.authorized_hit_count) is not int or self.authorized_hit_count < 0:
            raise ValueError("invalid retrieval count")


@dataclass(frozen=True, slots=True)
class CitationEvidence:
    citation_snapshot_id: str
    citation_digest: str

    def __post_init__(self) -> None:
        if _DIGEST.fullmatch(self.citation_digest) is None:
            raise ValueError("invalid citation digest")


@dataclass(frozen=True, slots=True)
class AnswerEvidence:
    answer: str
    outcome: str
    retrieval_summary: RetrievalSummary
    graph_context_status: GraphContextStatus
    graph_snapshot_id: str | None = None
    citations: tuple[CitationEvidence, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "abstained", "canceled", "failed"}:
            raise ValueError("invalid outcome")
        if (self.graph_context_status is GraphContextStatus.APPLIED) != bool(
            self.graph_snapshot_id
        ):
            if self.graph_context_status is GraphContextStatus.APPLIED:
                raise ValueError("APPLIED graph context requires a graph snapshot")
            raise ValueError("graph snapshot is legal only for APPLIED graph context")


@dataclass(frozen=True, slots=True)
class AnswerEvidenceSnapshot:
    snapshot_id: str
    project_id: str
    turn_id: str
    input_snapshot_digest: str
    value: AnswerEvidence
    answer_digest: str
    digest: str
    created_at: datetime

    def __post_init__(self) -> None:
        answer_digest = content_digest(self.value.answer)
        material = {
            "projectId": self.project_id,
            "turnId": self.turn_id,
            "inputSnapshotDigest": self.input_snapshot_digest,
            "answerDigest": answer_digest,
            "evidence": asdict(self.value),
        }
        if self.answer_digest != answer_digest or self.digest != content_digest(material):
            raise ValueError("answer evidence snapshot digest does not match bound facts")
        if _DIGEST.fullmatch(self.input_snapshot_digest) is None:
            raise ValueError("answer evidence must bind a canonical input digest")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("snapshot timestamp must be UTC-aware")

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        project_id: str,
        turn_id: str,
        input_digest: str,
        value: AnswerEvidence,
        now: datetime,
    ):
        answer_digest = content_digest(value.answer)
        material = {
            "projectId": project_id,
            "turnId": turn_id,
            "inputSnapshotDigest": input_digest,
            "answerDigest": answer_digest,
            "evidence": asdict(value),
        }
        return cls(
            snapshot_id,
            project_id,
            turn_id,
            input_digest,
            value,
            answer_digest,
            content_digest(material),
            now.astimezone(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    event_id: str
    sequence: int
    event_type: str
    payload: Mapping[str, object]
    occurred_at: datetime
    turn_id: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in {
            "turn.started",
            "context.assembled",
            "query.plan_ready",
            "stage.started",
            "stage.completed",
            "retrieval.hits_ready",
            "rerank.completed",
            "answer.delta",
            "citation.resolved",
            "turn.completed",
            "turn.abstained",
            "turn.degraded",
            "turn.canceled",
            "turn.failed",
            "conversation.turn.requested",
            "conversation.turn.completed",
        }:
            raise ValueError("unknown conversation event")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    turn_id: str
    client_request_id: str
    attempt: int
    state: str
    input_snapshot: TurnInputSnapshot
    answer_snapshot: AnswerEvidenceSnapshot | None = None
    lease_token: str | None = None


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str
    project_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    turns: tuple[ConversationTurn, ...] = ()
    events: tuple[ConversationEvent, ...] = ()
