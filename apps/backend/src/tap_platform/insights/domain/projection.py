"""Immutable values for append-only Insights fact projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionSnapshot:
    projection_version: str
    visible_data_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionReservation:
    projection_batch_id: str
    projection_version: str
    receipt_id: str
    data_version: int
    payload_checksum: str
    row_count: int
    complete: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectedAttempt:
    fact_key: str
    fact_checksum: str
    projection_version: str
    data_version: int
    projection_batch_id: str
    receipt_id: str
    project_id: str
    source_id: str
    external_run_id: str
    report_batch_id: str
    shard_id: str
    correction_no: int
    raw_object_ref: str
    raw_checksum: str
    parser_version: str
    application_commit: str
    script_commit: str
    environment: str
    configuration: str
    timezone: str
    source_test_identity: str
    source_locator: str
    stable_test_id: str | None
    data_row: str | None
    attempt: int | None
    result: str
    duration_seconds: float | None
    missing_reasons: tuple[str, ...]
    first_attempt_eligible: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class RunDimension:
    project_id: str
    source_id: str
    external_run_id: str
    report_batch_id: str
    shard_id: str
    correction_no: int
    application_commit: str
    script_commit: str
    environment: str
    configuration: str
    timezone: str
    job_id: str | None
    build_id: str | None
    branch: str | None
    business_cycle_id: str | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RebuildResult:
    projection_version: str
    row_count: int
    oracle_checksum: str
    clickhouse_checksum: str
