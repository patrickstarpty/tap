"""Immutable report intake domain values."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/;=@+-]{0,255}")


def _require_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


class ReportState(StrEnum):
    RECEIVED = "received"
    VALIDATING = "validating"
    MAPPED = "mapped"
    PROJECTING = "projecting"
    READY = "ready"
    REJECTED = "rejected"
    CONFLICTED = "conflicted"
    FAILED = "failed"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportManifest:
    project_id: str
    source_id: str
    external_run_id: str
    batch_id: str
    shard_id: str
    expected_shards: int | None
    contains_complete_attempts: bool
    application_commit: str
    script_commit: str
    environment: str
    configuration: str
    timezone: str
    correction_no: int = 0
    attachments: frozenset[str] = frozenset()
    external_test_id_mapping: tuple[tuple[str, str], ...] = ()
    job_id: str | None = None
    build_id: str | None = None
    branch: str | None = None
    data_row_context: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    business_cycle_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "source_id",
            "external_run_id",
            "batch_id",
            "shard_id",
            "application_commit",
            "script_commit",
            "environment",
            "configuration",
            "timezone",
        ):
            _require_identifier(name, getattr(self, name))
        if self.expected_shards is not None and (
            type(self.expected_shards) is not int or self.expected_shards < 1
        ):
            raise ValueError("expected_shards must be a positive integer or null")
        if type(self.contains_complete_attempts) is not bool:
            raise TypeError("contains_complete_attempts must be boolean")
        if type(self.correction_no) is not int or self.correction_no < 0:
            raise ValueError("correction_no must be a non-negative integer")
        if not isinstance(self.attachments, frozenset):
            raise TypeError("attachments must be an immutable set")
        for attachment in self.attachments:
            _require_identifier("attachment", attachment)
        for name in (
            "job_id",
            "build_id",
            "branch",
            "data_row_context",
            "started_at",
            "finished_at",
            "business_cycle_id",
        ):
            optional_value = getattr(self, name)
            if optional_value is not None:
                _require_identifier(name, optional_value)
        for source_identity, stable_id in self.external_test_id_mapping:
            _require_identifier("mapping source identity", source_identity)
            _require_identifier("mapping stable test id", stable_id)

    @property
    def initial_completeness(self) -> Completeness:
        if self.expected_shards is None:
            return Completeness.UNKNOWN
        return (
            Completeness.COMPLETE if self.expected_shards == 1 else Completeness.PARTIAL
        )

    @property
    def identity_digest(self) -> str:
        value = json.dumps(
            [
                self.project_id,
                self.source_id,
                self.external_run_id,
                self.batch_id,
                self.shard_id,
                self.correction_no,
            ],
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(value).hexdigest()

    @property
    def fingerprint(self) -> str:
        """Fingerprint every normalized field that affects report interpretation."""
        value = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(value).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "source_id": self.source_id,
            "external_run_id": self.external_run_id,
            "batch_id": self.batch_id,
            "shard_id": self.shard_id,
            "expected_shards": self.expected_shards,
            "contains_complete_attempts": self.contains_complete_attempts,
            "application_commit": self.application_commit,
            "script_commit": self.script_commit,
            "environment": self.environment,
            "configuration": self.configuration,
            "timezone": self.timezone,
            "correction_no": self.correction_no,
            "attachments": sorted(self.attachments),
            "external_test_id_mapping": [
                list(item) for item in self.external_test_id_mapping
            ],
            "job_id": self.job_id,
            "build_id": self.build_id,
            "branch": self.branch,
            "data_row_context": self.data_row_context,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "business_cycle_id": self.business_cycle_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReportManifest:
        aliases = {
            "projectId": "project_id",
            "sourceId": "source_id",
            "externalRunId": "external_run_id",
            "batchId": "batch_id",
            "shardId": "shard_id",
            "expectedShards": "expected_shards",
            "containsCompleteAttempts": "contains_complete_attempts",
            "applicationCommit": "application_commit",
            "scriptCommit": "script_commit",
            "correctionNo": "correction_no",
            "externalTestIdMapping": "external_test_id_mapping",
            "jobId": "job_id",
            "buildId": "build_id",
            "dataRowContext": "data_row_context",
            "startedAt": "started_at",
            "finishedAt": "finished_at",
            "businessCycleId": "business_cycle_id",
        }
        normalized: dict[str, Any] = {}
        ignored_observations = {"receivedShards", "completeness"}
        for name, item in value.items():
            if name in ignored_observations:
                continue
            normalized_name = aliases.get(name, name)
            if normalized_name in normalized:
                raise ValueError(f"manifest field supplied twice: {normalized_name}")
            normalized[normalized_name] = item
        required = {
            "project_id",
            "source_id",
            "external_run_id",
            "batch_id",
            "shard_id",
            "expected_shards",
            "contains_complete_attempts",
            "application_commit",
            "script_commit",
            "environment",
            "configuration",
            "timezone",
        }
        optional = {
            "correction_no",
            "attachments",
            "external_test_id_mapping",
            "job_id",
            "build_id",
            "branch",
            "data_row_context",
            "started_at",
            "finished_at",
            "business_cycle_id",
        }
        unknown = set(normalized) - (required | optional)
        missing = required - set(normalized)
        if unknown or missing:
            raise ValueError(
                f"manifest fields invalid; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        mapping_value = normalized.get("external_test_id_mapping", ())
        mapping_items = (
            mapping_value.items() if isinstance(mapping_value, dict) else mapping_value
        )
        return cls(
            project_id=normalized["project_id"],
            source_id=normalized["source_id"],
            external_run_id=normalized["external_run_id"],
            batch_id=normalized["batch_id"],
            shard_id=normalized["shard_id"],
            expected_shards=normalized["expected_shards"],
            contains_complete_attempts=normalized["contains_complete_attempts"],
            application_commit=normalized["application_commit"],
            script_commit=normalized["script_commit"],
            environment=normalized["environment"],
            configuration=normalized["configuration"],
            timezone=normalized["timezone"],
            correction_no=normalized.get("correction_no", 0),
            attachments=frozenset(normalized.get("attachments", ())),
            external_test_id_mapping=tuple(tuple(item) for item in mapping_items),
            job_id=normalized.get("job_id"),
            build_id=normalized.get("build_id"),
            branch=normalized.get("branch"),
            data_row_context=normalized.get("data_row_context"),
            started_at=normalized.get("started_at"),
            finished_at=normalized.get("finished_at"),
            business_cycle_id=normalized.get("business_cycle_id"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportReceipt:
    receipt_id: str
    project_id: str
    source_id: str
    external_run_id: str
    batch_id: str
    shard_id: str
    checksum: str
    parser_version: str
    correction_no: int
    raw_object_ref: str
    state: ReportState
    completeness: Completeness
    size_bytes: int
    conflict_with_receipt_id: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TestAttemptFact:
    source_test_identity: str
    source_locator: str
    stable_test_id: str | None
    data_row: str | None
    attempt: int | None
    result: str
    duration_seconds: float | None
    evidence_refs: tuple[str, ...]
    missing_reasons: tuple[str, ...]
    first_attempt_eligible: bool


def logical_attempt_key(manifest: ReportManifest, fact: TestAttemptFact) -> str:
    """Execution identity is independent of delivery and correction provenance."""
    return _digest(
        [
            manifest.project_id,
            manifest.source_id,
            manifest.external_run_id,
            manifest.application_commit,
            manifest.script_commit,
            manifest.environment,
            manifest.configuration,
            manifest.timezone,
            fact.stable_test_id or fact.source_test_identity,
            fact.data_row,
            fact.attempt,
            fact.source_locator if fact.attempt is None else None,
        ]
    )


def attempt_content_checksum(manifest: ReportManifest, fact: TestAttemptFact) -> str:
    content = asdict(fact)
    content.pop("source_locator")
    return _digest(
        {
            "fact_key": logical_attempt_key(manifest, fact),
            "content": content,
            "started_at": manifest.started_at,
            "build_id": manifest.build_id,
            "branch": manifest.branch,
        }
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
