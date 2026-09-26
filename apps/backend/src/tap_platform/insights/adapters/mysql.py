"""TAP-owned SQLAlchemy receipt ledger with MySQL transaction semantics."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal, cast
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    BigInteger,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import mysql

from tap_platform.insights.adapters.junit import PARSER_VERSION
from tap_platform.insights.adapters.objects import StoredObject
from tap_platform.insights.application.queries import (
    AttemptDetail,
    FailureDetail,
    MetricQuery,
    QueryFilters,
    QueryRecord,
    RunSummary,
    TrendPoint,
)
from tap_platform.insights.domain.metrics import MetricId, MetricValue
from tap_platform.insights.domain.reports import (
    Completeness,
    ReportManifest,
    ReportReceipt,
    ReportState,
    TestAttemptFact,
)
from tap_platform.insights.domain.projection import (
    ProjectionReservation,
    ProjectionSnapshot,
)


metadata = MetaData()
precise_datetime = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")

report_identity_claims = Table(
    "tap_report_identity_claims",
    metadata,
    Column("identity_digest", String(64), primary_key=True),
    Column("canonical_receipt_id", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

report_receipts = Table(
    "tap_report_receipts",
    metadata,
    Column("receipt_id", String(36), primary_key=True),
    Column("identity_digest", String(64), nullable=False),
    Column("manifest_digest", String(64), nullable=False),
    Column("project_id", String(256), nullable=False),
    Column("source_id", String(256), nullable=False),
    Column("external_run_id", String(256), nullable=False),
    Column("batch_id", String(256), nullable=False),
    Column("shard_id", String(256), nullable=False),
    Column("correction_no", Integer, nullable=False),
    Column("checksum", String(64), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("raw_object_ref", String(512), nullable=False),
    Column("parser_version", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("completeness", String(32), nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("conflict_with_receipt_id", String(36), nullable=True),
    Column("failure_reason", String(256), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "identity_digest",
        "checksum",
        "manifest_digest",
        name="uq_tap_report_identity_content",
    ),
)

report_outbox = Table(
    "tap_report_outbox",
    metadata,
    Column("outbox_id", String(36), primary_key=True),
    Column(
        "receipt_id",
        String(36),
        ForeignKey("tap_report_receipts.receipt_id"),
        nullable=False,
    ),
    Column("event_type", String(64), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("receipt_id", "sequence_no", name="uq_tap_report_outbox_sequence"),
)

report_transitions = Table(
    "tap_report_transitions",
    metadata,
    Column("transition_id", String(36), primary_key=True),
    Column(
        "receipt_id",
        String(36),
        ForeignKey("tap_report_receipts.receipt_id"),
        nullable=False,
    ),
    Column("from_state", String(32), nullable=True),
    Column("to_state", String(32), nullable=False),
    Column("reason", String(256), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_tap_report_transition_receipt", "receipt_id", "created_at"),
)

report_attempts = Table(
    "tap_report_attempts",
    metadata,
    Column("attempt_fact_id", String(36), primary_key=True),
    Column(
        "receipt_id",
        String(36),
        ForeignKey("tap_report_receipts.receipt_id"),
        nullable=False,
    ),
    Column("ordinal", Integer, nullable=False),
    Column("source_test_identity", String(512), nullable=False),
    Column("source_locator", String(256), nullable=False),
    Column("stable_test_id", String(256), nullable=True),
    Column("data_row", String(256), nullable=True),
    Column("attempt", Integer, nullable=True),
    Column("result", String(32), nullable=False),
    Column("duration_seconds", Float, nullable=True),
    Column("evidence_refs", JSON, nullable=False),
    Column("missing_reasons", JSON, nullable=False),
    Column("first_attempt_eligible", Boolean, nullable=False),
    UniqueConstraint("receipt_id", "ordinal", name="uq_tap_report_attempt_ordinal"),
)

insights_projection_versions = Table(
    "tap_insights_projection_versions",
    metadata,
    Column("projection_version", String(128), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("next_data_version", BigInteger, nullable=False),
    Column("visible_data_version", BigInteger, nullable=False),
    Column("verified_row_count", BigInteger, nullable=True),
    Column("verified_checksum", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=True),
)

insights_projection_state = Table(
    "tap_insights_projection_state",
    metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column(
        "active_projection_version",
        String(128),
        ForeignKey("tap_insights_projection_versions.projection_version"),
        nullable=False,
    ),
    Column("visible_data_version", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

insights_projection_batches = Table(
    "tap_insights_projection_batches",
    metadata,
    Column("projection_batch_id", String(64), primary_key=True),
    Column(
        "projection_version",
        String(128),
        ForeignKey("tap_insights_projection_versions.projection_version"),
        nullable=False,
    ),
    Column(
        "receipt_id",
        String(36),
        ForeignKey("tap_report_receipts.receipt_id"),
        nullable=False,
    ),
    Column("data_version", BigInteger, nullable=False),
    Column("payload_checksum", String(64), nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "projection_version",
        "receipt_id",
        name="uq_tap_insights_projection_receipt",
    ),
    UniqueConstraint(
        "projection_version",
        "data_version",
        name="uq_tap_insights_projection_data_version",
    ),
)

insights_queries = Table(
    "tap_insights_queries",
    metadata,
    Column("query_id", String(36), primary_key=True),
    Column("project_id", String(256), nullable=False),
    Column("metric_version", String(128), nullable=False),
    Column("filters", JSON, nullable=False),
    Column("from_date", String(10), nullable=False),
    Column("to_date", String(10), nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("as_of", precise_datetime, nullable=False),
    Column("projection_version", String(128), nullable=False),
    Column("visible_data_version", BigInteger, nullable=False),
    Column("result_payload", JSON, nullable=False),
    Column("created_at", precise_datetime, nullable=False),
    Index("ix_tap_insights_query_project_created", "project_id", "created_at"),
)


_ALLOWED_TRANSITIONS: dict[ReportState, set[ReportState]] = {
    ReportState.RECEIVED: {ReportState.VALIDATING},
    ReportState.VALIDATING: {
        ReportState.MAPPED,
        ReportState.REJECTED,
        ReportState.FAILED,
    },
    ReportState.MAPPED: {ReportState.PROJECTING},
    ReportState.PROJECTING: {ReportState.FAILED},
    ReportState.FAILED: {ReportState.VALIDATING, ReportState.PROJECTING},
}


def _now() -> datetime:
    # Existing projection timestamps are second-precision. Floor before writing
    # so a just-captured as-of never precedes a rounded-up activation/completion.
    return datetime.now(UTC).replace(microsecond=0)


class SqlAlchemyReportLedger:
    """Receipt governance and outbox share each SQL transaction."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def accept(self, manifest: ReportManifest, raw: StoredObject) -> ReportReceipt:
        try:
            return self._accept_once(manifest, raw)
        except IntegrityError:
            existing = self._find_exact(
                manifest.identity_digest, raw.checksum, manifest.fingerprint
            )
            if existing is not None:
                return existing
            canonical = self._find_canonical(manifest.identity_digest)
            if canonical is None:
                raise
            try:
                return self._insert_conflict(manifest, raw, canonical)
            except IntegrityError:
                raced = self._find_exact(
                    manifest.identity_digest, raw.checksum, manifest.fingerprint
                )
                if raced is None:
                    raise
                return raced

    def _accept_once(
        self, manifest: ReportManifest, raw: StoredObject
    ) -> ReportReceipt:
        now = _now()
        with self._engine.begin() as connection:
            exact = (
                connection.execute(
                    select(report_receipts).where(
                        report_receipts.c.identity_digest == manifest.identity_digest,
                        report_receipts.c.checksum == raw.checksum,
                        report_receipts.c.manifest_digest == manifest.fingerprint,
                    )
                )
                .mappings()
                .first()
            )
            if exact is not None:
                return self._row_to_receipt(exact)
            receipt_id = str(uuid.uuid4())
            connection.execute(
                insert(report_identity_claims).values(
                    identity_digest=manifest.identity_digest,
                    canonical_receipt_id=receipt_id,
                    created_at=now,
                )
            )
            completeness = self._batch_completeness(connection, manifest)
            connection.execute(
                insert(report_receipts).values(
                    receipt_id=receipt_id,
                    identity_digest=manifest.identity_digest,
                    manifest_digest=manifest.fingerprint,
                    project_id=manifest.project_id,
                    source_id=manifest.source_id,
                    external_run_id=manifest.external_run_id,
                    batch_id=manifest.batch_id,
                    shard_id=manifest.shard_id,
                    correction_no=manifest.correction_no,
                    checksum=raw.checksum,
                    size_bytes=raw.size_bytes,
                    raw_object_ref=raw.ref,
                    parser_version=PARSER_VERSION,
                    state=ReportState.RECEIVED.value,
                    completeness=completeness.value,
                    manifest=manifest.to_dict(),
                    conflict_with_receipt_id=None,
                    failure_reason=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._insert_transition(
                connection,
                receipt_id=receipt_id,
                from_state=None,
                to_state=ReportState.RECEIVED,
                reason=None,
                now=now,
            )
            self._insert_outbox(connection, receipt_id, ReportState.RECEIVED, now)
            row = (
                connection.execute(
                    select(report_receipts).where(
                        report_receipts.c.receipt_id == receipt_id
                    )
                )
                .mappings()
                .one()
            )
            return self._row_to_receipt(row)

    def get_receipt(self, receipt_id: str) -> ReportReceipt:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(report_receipts).where(
                        report_receipts.c.receipt_id == receipt_id
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                manifest_value = row["manifest"]
                if isinstance(manifest_value, str):
                    manifest_value = json.loads(manifest_value)
                completeness = self._batch_completeness(
                    connection, ReportManifest.from_dict(manifest_value)
                )
        if row is None:
            raise KeyError(receipt_id)
        return self._row_to_receipt(row, completeness=completeness)

    def get_manifest(self, receipt_id: str) -> ReportManifest:
        with self._engine.connect() as connection:
            value = connection.execute(
                select(report_receipts.c.manifest).where(
                    report_receipts.c.receipt_id == receipt_id
                )
            ).scalar_one_or_none()
        if value is None:
            raise KeyError(receipt_id)
        if isinstance(value, str):
            value = json.loads(value)
        return ReportManifest.from_dict(value)

    def transition(
        self,
        receipt_id: str,
        *,
        expected: ReportState,
        target: ReportState,
        reason: str | None = None,
        attempts: Iterable[TestAttemptFact] | None = None,
    ) -> ReportReceipt:
        if target not in _ALLOWED_TRANSITIONS.get(expected, set()):
            raise ValueError(
                f"invalid report transition {expected.value}->{target.value}"
            )
        now = _now()
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    select(report_receipts)
                    .where(report_receipts.c.receipt_id == receipt_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError(receipt_id)
            current = ReportState(row["state"])
            if current is not expected:
                return self._row_to_receipt(row)
            if attempts is not None:
                connection.execute(
                    delete(report_attempts).where(
                        report_attempts.c.receipt_id == receipt_id
                    )
                )
                for ordinal, attempt in enumerate(attempts, start=1):
                    connection.execute(
                        insert(report_attempts).values(
                            attempt_fact_id=str(uuid.uuid4()),
                            receipt_id=receipt_id,
                            ordinal=ordinal,
                            source_test_identity=attempt.source_test_identity,
                            source_locator=attempt.source_locator,
                            stable_test_id=attempt.stable_test_id,
                            data_row=attempt.data_row,
                            attempt=attempt.attempt,
                            result=attempt.result,
                            duration_seconds=attempt.duration_seconds,
                            evidence_refs=list(attempt.evidence_refs),
                            missing_reasons=list(attempt.missing_reasons),
                            first_attempt_eligible=attempt.first_attempt_eligible,
                        )
                    )
            connection.execute(
                update(report_receipts)
                .where(
                    report_receipts.c.receipt_id == receipt_id,
                    report_receipts.c.state == expected.value,
                )
                .values(
                    state=target.value,
                    failure_reason=reason,
                    updated_at=now,
                )
            )
            self._insert_transition(
                connection,
                receipt_id=receipt_id,
                from_state=expected,
                to_state=target,
                reason=reason,
                now=now,
            )
            self._insert_outbox(connection, receipt_id, target, now)
            updated = (
                connection.execute(
                    select(report_receipts).where(
                        report_receipts.c.receipt_id == receipt_id
                    )
                )
                .mappings()
                .one()
            )
            return self._row_to_receipt(updated)

    def attempts_for(self, receipt_id: str) -> list[TestAttemptFact]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(report_attempts)
                .where(report_attempts.c.receipt_id == receipt_id)
                .order_by(report_attempts.c.ordinal)
            ).mappings()
            return [self._row_to_attempt(row) for row in rows]

    def outbox_events(self, receipt_id: str) -> list[str]:
        with self._engine.connect() as connection:
            return list(
                connection.execute(
                    select(report_outbox.c.event_type)
                    .where(report_outbox.c.receipt_id == receipt_id)
                    .order_by(report_outbox.c.sequence_no)
                ).scalars()
            )

    def referenced_raw_objects(self) -> set[str]:
        with self._engine.connect() as connection:
            return set(
                connection.execute(select(report_receipts.c.raw_object_ref)).scalars()
            )

    def next_processable(self, *, limit: int = 100) -> list[str]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._engine.connect() as connection:
            return list(
                connection.execute(
                    select(report_receipts.c.receipt_id)
                    .where(
                        report_receipts.c.state.in_(
                            (
                                ReportState.RECEIVED.value,
                                ReportState.VALIDATING.value,
                                ReportState.MAPPED.value,
                                ReportState.PROJECTING.value,
                            )
                        )
                    )
                    .order_by(
                        report_receipts.c.created_at,
                        report_receipts.c.receipt_id,
                    )
                    .limit(limit)
                ).scalars()
            )

    def count_receipts(self) -> int:
        with self._engine.connect() as connection:
            return int(
                connection.execute(
                    select(func.count()).select_from(report_receipts)
                ).scalar_one()
            )

    def save_query(self, record: QueryRecord) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(insights_queries).values(
                    query_id=record.query_id,
                    project_id=record.project_id,
                    metric_version=record.metric_version,
                    filters={
                        "source_ids": list(record.query.filters.source_ids),
                        "run_ids": list(record.query.filters.run_ids),
                        "build_ids": list(record.query.filters.build_ids),
                        "branches": list(record.query.filters.branches),
                        "environments": list(record.query.filters.environments),
                        "configurations": list(record.query.filters.configurations),
                    },
                    from_date=record.query.from_date,
                    to_date=record.query.to_date,
                    timezone=record.query.timezone,
                    as_of=record.query.as_of,
                    projection_version=record.snapshot.projection_version,
                    visible_data_version=record.snapshot.visible_data_version,
                    result_payload=_query_result_payload(record),
                    created_at=record.created_at,
                )
            )

    def get_query(self, query_id: str) -> QueryRecord:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(insights_queries).where(
                        insights_queries.c.query_id == query_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise KeyError(query_id)
        return _row_to_query_record(row)

    def projection_snapshot_at(self, as_of: datetime) -> ProjectionSnapshot:
        if as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        requested = as_of.astimezone(UTC)
        with self._engine.begin() as connection:
            self._ensure_default_projection(connection)
            versions = list(
                connection.execute(
                    select(insights_projection_versions)
                    .where(insights_projection_versions.c.activated_at.is_not(None))
                    .order_by(insights_projection_versions.c.activated_at.desc())
                ).mappings()
            )
            selected = next(
                (
                    row
                    for row in versions
                    if _aware_utc(row["activated_at"]) <= requested
                ),
                None,
            )
            if selected is None:
                # Before the first activation the authoritative view is explicitly
                # empty, not an infrastructure error and never the latest facts.
                return ProjectionSnapshot(
                    projection_version=str(versions[-1]["projection_version"]),
                    visible_data_version=0,
                )
            batches = connection.execute(
                select(
                    insights_projection_batches.c.data_version,
                    insights_projection_batches.c.status,
                    insights_projection_batches.c.completed_at,
                )
                .where(
                    insights_projection_batches.c.projection_version
                    == selected["projection_version"]
                )
                .order_by(insights_projection_batches.c.data_version)
            ).mappings()
            visible = 0
            for batch in batches:
                completed_at = batch["completed_at"]
                if (
                    int(batch["data_version"]) != visible + 1
                    or batch["status"] != "complete"
                    or completed_at is None
                    or _aware_utc(completed_at) > requested
                ):
                    break
                visible += 1
            return ProjectionSnapshot(
                projection_version=str(selected["projection_version"]),
                visible_data_version=visible,
            )

    def projection_snapshot(
        self, projection_version: str | None = None
    ) -> ProjectionSnapshot:
        with self._engine.begin() as connection:
            self._ensure_default_projection(connection)
            if projection_version is None:
                row = connection.execute(
                    select(
                        insights_projection_state.c.active_projection_version,
                        insights_projection_state.c.visible_data_version,
                    ).where(insights_projection_state.c.singleton_id == 1)
                ).one()
                return ProjectionSnapshot(
                    projection_version=row.active_projection_version,
                    visible_data_version=int(row.visible_data_version),
                )
            version_row = connection.execute(
                select(
                    insights_projection_versions.c.projection_version,
                    insights_projection_versions.c.visible_data_version,
                ).where(
                    insights_projection_versions.c.projection_version
                    == projection_version
                )
            ).one_or_none()
            if version_row is None:
                raise KeyError(projection_version)
            return ProjectionSnapshot(
                projection_version=version_row.projection_version,
                visible_data_version=int(version_row.visible_data_version),
            )

    def reserve_projection_batch(
        self,
        *,
        receipt_id: str,
        payload_checksum: str,
        row_count: int,
        projection_version: str | None = None,
    ) -> ProjectionReservation:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", payload_checksum):
            raise ValueError("payload_checksum must be a bounded lowercase token")
        if row_count < 0:
            raise ValueError("row_count must be non-negative")
        now = _now()
        with self._engine.begin() as connection:
            self._ensure_default_projection(connection)
            selected_version = (
                projection_version
                or connection.execute(
                    select(insights_projection_state.c.active_projection_version).where(
                        insights_projection_state.c.singleton_id == 1
                    )
                ).scalar_one()
            )
            version_row = (
                connection.execute(
                    select(insights_projection_versions)
                    .where(
                        insights_projection_versions.c.projection_version
                        == selected_version
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if version_row is None:
                raise KeyError(selected_version)
            if projection_version is None:
                active_now = connection.execute(
                    select(insights_projection_state.c.active_projection_version).where(
                        insights_projection_state.c.singleton_id == 1
                    )
                ).scalar_one()
                if version_row["status"] != "active" or active_now != selected_version:
                    raise RuntimeError("active projection changed before batch reserve")
            elif version_row["status"] != "building":
                raise ValueError("rebuild projection version is not building")
            existing = (
                connection.execute(
                    select(insights_projection_batches).where(
                        insights_projection_batches.c.projection_version
                        == selected_version,
                        insights_projection_batches.c.receipt_id == receipt_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing["payload_checksum"] != payload_checksum
                    or int(existing["row_count"]) != row_count
                ):
                    raise ValueError("same projection receipt has conflicting content")
                return self._row_to_projection_reservation(existing)
            receipt_state = connection.execute(
                select(report_receipts.c.state).where(
                    report_receipts.c.receipt_id == receipt_id
                )
            ).scalar_one_or_none()
            if receipt_state is None:
                raise KeyError(receipt_id)
            if projection_version is None and receipt_state not in {
                ReportState.PROJECTING.value,
                ReportState.READY.value,
            }:
                raise ValueError("receipt is not authoritative for projection")
            if (
                projection_version is not None
                and receipt_state != ReportState.READY.value
            ):
                raise ValueError("rebuild requires a ready source receipt")
            data_version = int(version_row["next_data_version"])
            batch_id = hashlib.sha256(
                f"{selected_version}\x00{receipt_id}".encode()
            ).hexdigest()
            connection.execute(
                insert(insights_projection_batches).values(
                    projection_batch_id=batch_id,
                    projection_version=selected_version,
                    receipt_id=receipt_id,
                    data_version=data_version,
                    payload_checksum=payload_checksum,
                    row_count=row_count,
                    status="reserved",
                    created_at=now,
                    completed_at=None,
                )
            )
            connection.execute(
                update(insights_projection_versions)
                .where(
                    insights_projection_versions.c.projection_version
                    == selected_version
                )
                .values(next_data_version=data_version + 1)
            )
            return ProjectionReservation(
                projection_batch_id=batch_id,
                projection_version=selected_version,
                receipt_id=receipt_id,
                data_version=data_version,
                payload_checksum=payload_checksum,
                row_count=row_count,
                complete=False,
            )

    def complete_projection_batch(
        self,
        reservation: ProjectionReservation,
        *,
        mark_receipt_ready: bool = True,
    ) -> bool:
        now = _now()
        with self._engine.begin() as connection:
            version_row = (
                connection.execute(
                    select(insights_projection_versions)
                    .where(
                        insights_projection_versions.c.projection_version
                        == reservation.projection_version
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            batch = (
                connection.execute(
                    select(insights_projection_batches)
                    .where(
                        insights_projection_batches.c.projection_batch_id
                        == reservation.projection_batch_id
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                batch["payload_checksum"] != reservation.payload_checksum
                or int(batch["row_count"]) != reservation.row_count
            ):
                raise ValueError("projection reservation changed before completion")
            changed = batch["status"] != "complete"
            if changed:
                connection.execute(
                    update(insights_projection_batches)
                    .where(
                        insights_projection_batches.c.projection_batch_id
                        == reservation.projection_batch_id
                    )
                    .values(status="complete", completed_at=now)
                )
            first_incomplete = connection.execute(
                select(func.min(insights_projection_batches.c.data_version)).where(
                    insights_projection_batches.c.projection_version
                    == reservation.projection_version,
                    insights_projection_batches.c.status != "complete",
                )
            ).scalar_one()
            if first_incomplete is None:
                visible = int(version_row["next_data_version"]) - 1
            else:
                visible = int(first_incomplete) - 1
            connection.execute(
                update(insights_projection_versions)
                .where(
                    insights_projection_versions.c.projection_version
                    == reservation.projection_version
                )
                .values(visible_data_version=visible)
            )
            active = connection.execute(
                select(insights_projection_state.c.active_projection_version).where(
                    insights_projection_state.c.singleton_id == 1
                )
            ).scalar_one()
            if active == reservation.projection_version:
                connection.execute(
                    update(insights_projection_state)
                    .where(insights_projection_state.c.singleton_id == 1)
                    .values(visible_data_version=visible, updated_at=now)
                )
            if mark_receipt_ready:
                receipt_state = connection.execute(
                    select(report_receipts.c.state)
                    .where(report_receipts.c.receipt_id == reservation.receipt_id)
                    .with_for_update()
                ).scalar_one()
                if receipt_state == ReportState.PROJECTING.value:
                    connection.execute(
                        update(report_receipts)
                        .where(report_receipts.c.receipt_id == reservation.receipt_id)
                        .values(
                            state=ReportState.READY.value,
                            failure_reason=None,
                            updated_at=now,
                        )
                    )
                    self._insert_transition(
                        connection,
                        receipt_id=reservation.receipt_id,
                        from_state=ReportState.PROJECTING,
                        to_state=ReportState.READY,
                        reason=None,
                        now=now,
                    )
                    self._insert_outbox(
                        connection, reservation.receipt_id, ReportState.READY, now
                    )
                elif receipt_state != ReportState.READY.value:
                    raise ValueError("receipt is not authoritative for projection")
            return changed

    def create_projection_version(self, projection_version: str) -> None:
        self._require_projection_version(projection_version)
        now = _now()
        with self._engine.begin() as connection:
            self._ensure_default_projection(connection)
            existing = connection.execute(
                select(insights_projection_versions.c.status).where(
                    insights_projection_versions.c.projection_version
                    == projection_version
                )
            ).scalar_one_or_none()
            if existing is None:
                connection.execute(
                    insert(insights_projection_versions).values(
                        projection_version=projection_version,
                        status="building",
                        next_data_version=1,
                        visible_data_version=0,
                        verified_row_count=None,
                        verified_checksum=None,
                        created_at=now,
                        activated_at=None,
                    )
                )
            elif existing != "building":
                raise ValueError("target projection version is not rebuildable")

    def activate_projection_version(
        self,
        projection_version: str,
        *,
        row_count: int,
        checksum: str,
        expected_active_projection_version: str,
        expected_visible_data_version: int,
    ) -> None:
        now = _now()
        with self._engine.begin() as connection:
            preview = (
                connection.execute(
                    select(insights_projection_state).where(
                        insights_projection_state.c.singleton_id == 1
                    )
                )
                .mappings()
                .one()
            )
            preview_current = preview["active_projection_version"]
            connection.execute(
                select(insights_projection_versions.c.projection_version)
                .where(
                    insights_projection_versions.c.projection_version == preview_current
                )
                .with_for_update()
            ).one()
            state = (
                connection.execute(
                    select(insights_projection_state)
                    .where(insights_projection_state.c.singleton_id == 1)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            current = state["active_projection_version"]
            if current != preview_current:
                raise ValueError("active projection changed during rebuild")
            target = (
                connection.execute(
                    select(insights_projection_versions)
                    .where(
                        insights_projection_versions.c.projection_version
                        == projection_version
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if target["status"] != "building":
                raise ValueError("only a verified building projection can activate")
            if (
                current != expected_active_projection_version
                or int(state["visible_data_version"]) != expected_visible_data_version
            ):
                raise ValueError("active projection changed during rebuild")
            incomplete_source_batches = int(
                connection.execute(
                    select(func.count())
                    .select_from(insights_projection_batches)
                    .where(
                        insights_projection_batches.c.projection_version == current,
                        insights_projection_batches.c.status != "complete",
                    )
                ).scalar_one()
            )
            if incomplete_source_batches:
                raise ValueError("incomplete source projection batches prevent cutover")
            connection.execute(
                update(insights_projection_versions)
                .where(insights_projection_versions.c.projection_version == current)
                .values(status="retired")
            )
            connection.execute(
                update(insights_projection_versions)
                .where(
                    insights_projection_versions.c.projection_version
                    == projection_version
                )
                .values(
                    status="active",
                    verified_row_count=row_count,
                    verified_checksum=checksum,
                    activated_at=now,
                )
            )
            connection.execute(
                update(insights_projection_state)
                .where(insights_projection_state.c.singleton_id == 1)
                .values(
                    active_projection_version=projection_version,
                    visible_data_version=int(target["visible_data_version"]),
                    updated_at=now,
                )
            )

    def receipts_in_projection(
        self, projection_version: str, *, visible_data_version: int
    ) -> list[ReportReceipt]:
        if visible_data_version < 0:
            raise ValueError("visible_data_version must be non-negative")
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(report_receipts)
                .join(
                    insights_projection_batches,
                    insights_projection_batches.c.receipt_id
                    == report_receipts.c.receipt_id,
                )
                .where(
                    insights_projection_batches.c.projection_version
                    == projection_version,
                    insights_projection_batches.c.status == "complete",
                    insights_projection_batches.c.data_version <= visible_data_version,
                )
                .order_by(
                    report_receipts.c.project_id,
                    report_receipts.c.source_id,
                    report_receipts.c.external_run_id,
                    report_receipts.c.batch_id,
                    report_receipts.c.shard_id,
                    report_receipts.c.correction_no,
                    report_receipts.c.receipt_id,
                )
            ).mappings()
            return [self._row_to_receipt(row) for row in rows]

    @staticmethod
    def _require_projection_version(projection_version: str) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", projection_version) is None:
            raise ValueError("projection_version must be a bounded lowercase token")

    @staticmethod
    def _ensure_default_projection(connection: Connection) -> None:
        state = connection.execute(
            select(insights_projection_state.c.singleton_id).where(
                insights_projection_state.c.singleton_id == 1
            )
        ).scalar_one_or_none()
        if state is not None:
            return
        now = _now()
        connection.execute(
            insert(insights_projection_versions).values(
                projection_version="insights-v1",
                status="active",
                next_data_version=1,
                visible_data_version=0,
                verified_row_count=None,
                verified_checksum=None,
                created_at=now,
                activated_at=now,
            )
        )
        connection.execute(
            insert(insights_projection_state).values(
                singleton_id=1,
                active_projection_version="insights-v1",
                visible_data_version=0,
                updated_at=now,
            )
        )

    @staticmethod
    def _row_to_projection_reservation(row: RowMapping) -> ProjectionReservation:
        return ProjectionReservation(
            projection_batch_id=row["projection_batch_id"],
            projection_version=row["projection_version"],
            receipt_id=row["receipt_id"],
            data_version=int(row["data_version"]),
            payload_checksum=row["payload_checksum"],
            row_count=int(row["row_count"]),
            complete=row["status"] == "complete",
        )

    def _find_exact(
        self, identity_digest: str, checksum: str, manifest_digest: str
    ) -> ReportReceipt | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(report_receipts).where(
                        report_receipts.c.identity_digest == identity_digest,
                        report_receipts.c.checksum == checksum,
                        report_receipts.c.manifest_digest == manifest_digest,
                    )
                )
                .mappings()
                .first()
            )
        return self._row_to_receipt(row) if row is not None else None

    def _find_canonical(self, identity_digest: str) -> str | None:
        with self._engine.connect() as connection:
            return connection.execute(
                select(report_identity_claims.c.canonical_receipt_id).where(
                    report_identity_claims.c.identity_digest == identity_digest
                )
            ).scalar_one_or_none()

    def _insert_conflict(
        self,
        manifest: ReportManifest,
        raw: StoredObject,
        canonical_receipt_id: str,
    ) -> ReportReceipt:
        receipt_id = str(uuid.uuid4())
        now = _now()
        with self._engine.begin() as connection:
            completeness = self._batch_completeness(connection, manifest)
            connection.execute(
                insert(report_receipts).values(
                    receipt_id=receipt_id,
                    identity_digest=manifest.identity_digest,
                    manifest_digest=manifest.fingerprint,
                    project_id=manifest.project_id,
                    source_id=manifest.source_id,
                    external_run_id=manifest.external_run_id,
                    batch_id=manifest.batch_id,
                    shard_id=manifest.shard_id,
                    correction_no=manifest.correction_no,
                    checksum=raw.checksum,
                    size_bytes=raw.size_bytes,
                    raw_object_ref=raw.ref,
                    parser_version=PARSER_VERSION,
                    state=ReportState.CONFLICTED.value,
                    completeness=completeness.value,
                    manifest=manifest.to_dict(),
                    conflict_with_receipt_id=canonical_receipt_id,
                    failure_reason="same-identity-different-content",
                    created_at=now,
                    updated_at=now,
                )
            )
            self._insert_transition(
                connection,
                receipt_id=receipt_id,
                from_state=None,
                to_state=ReportState.CONFLICTED,
                reason="same-identity-different-content",
                now=now,
            )
            self._insert_outbox(connection, receipt_id, ReportState.CONFLICTED, now)
            row = (
                connection.execute(
                    select(report_receipts).where(
                        report_receipts.c.receipt_id == receipt_id
                    )
                )
                .mappings()
                .one()
            )
            return self._row_to_receipt(row)

    @staticmethod
    def _batch_completeness(
        connection: Connection, manifest: ReportManifest
    ) -> Completeness:
        predicates = (
            report_receipts.c.project_id == manifest.project_id,
            report_receipts.c.source_id == manifest.source_id,
            report_receipts.c.external_run_id == manifest.external_run_id,
            report_receipts.c.batch_id == manifest.batch_id,
            report_receipts.c.state != ReportState.CONFLICTED.value,
        )
        if manifest.expected_shards is None:
            return Completeness.UNKNOWN
        received = connection.execute(
            select(func.count(func.distinct(report_receipts.c.shard_id))).where(
                *predicates
            )
        ).scalar_one()
        current_shard_present = connection.execute(
            select(report_receipts.c.receipt_id)
            .where(*predicates, report_receipts.c.shard_id == manifest.shard_id)
            .limit(1)
        ).scalar_one_or_none()
        if current_shard_present is None:
            received += 1
        return (
            Completeness.COMPLETE
            if received >= manifest.expected_shards
            else Completeness.PARTIAL
        )

    @staticmethod
    def _insert_transition(
        connection: Connection,
        *,
        receipt_id: str,
        from_state: ReportState | None,
        to_state: ReportState,
        reason: str | None,
        now: datetime,
    ) -> None:
        connection.execute(
            insert(report_transitions).values(
                transition_id=str(uuid.uuid4()),
                receipt_id=receipt_id,
                from_state=from_state.value if from_state else None,
                to_state=to_state.value,
                reason=reason,
                created_at=now,
            )
        )

    @staticmethod
    def _insert_outbox(
        connection: Connection,
        receipt_id: str,
        state: ReportState,
        now: datetime,
    ) -> None:
        sequence_no = (
            connection.execute(
                select(func.count())
                .select_from(report_outbox)
                .where(report_outbox.c.receipt_id == receipt_id)
            ).scalar_one()
            + 1
        )
        connection.execute(
            insert(report_outbox).values(
                outbox_id=str(uuid.uuid4()),
                receipt_id=receipt_id,
                event_type=f"report.{state.value}",
                sequence_no=sequence_no,
                payload={"receipt_id": receipt_id, "state": state.value},
                created_at=now,
            )
        )

    @staticmethod
    def _row_to_receipt(
        row: RowMapping, *, completeness: Completeness | None = None
    ) -> ReportReceipt:
        return ReportReceipt(
            receipt_id=row["receipt_id"],
            project_id=row["project_id"],
            source_id=row["source_id"],
            external_run_id=row["external_run_id"],
            batch_id=row["batch_id"],
            shard_id=row["shard_id"],
            checksum=row["checksum"],
            parser_version=row["parser_version"],
            correction_no=row["correction_no"],
            raw_object_ref=row["raw_object_ref"],
            state=ReportState(row["state"]),
            completeness=completeness or Completeness(row["completeness"]),
            size_bytes=row["size_bytes"],
            conflict_with_receipt_id=row["conflict_with_receipt_id"],
            failure_reason=row["failure_reason"],
        )

    @staticmethod
    def _row_to_attempt(row: RowMapping) -> TestAttemptFact:
        evidence = row["evidence_refs"]
        missing = row["missing_reasons"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        if isinstance(missing, str):
            missing = json.loads(missing)
        return TestAttemptFact(
            source_test_identity=row["source_test_identity"],
            source_locator=row["source_locator"],
            stable_test_id=row["stable_test_id"],
            data_row=row["data_row"],
            attempt=row["attempt"],
            result=row["result"],
            duration_seconds=row["duration_seconds"],
            evidence_refs=tuple(evidence),
            missing_reasons=tuple(missing),
            first_attempt_eligible=bool(row["first_attempt_eligible"]),
        )


def _aware_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.utcoffset() is None
        else value.astimezone(UTC)
    )


def _query_result_payload(record: QueryRecord) -> dict[str, object]:
    return {
        "metrics": [_metric_payload(item) for item in record.metrics],
        "trends": [
            {
                "local_date": point.local_date,
                "metrics": [_metric_payload(item) for item in point.metrics],
            }
            for point in record.trends
        ],
        "runs": [
            {
                "run_id": item.run_id,
                "external_run_id": item.external_run_id,
                "source_id": item.source_id,
                "build_id": item.build_id,
                "branch": item.branch,
                "environment": item.environment,
                "configuration": item.configuration,
                "started_at": (
                    item.started_at.isoformat() if item.started_at is not None else None
                ),
                "instance_count": item.instance_count,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in record.runs
        ],
        "failures": [
            {
                "fact_key": item.fact_key,
                "run_id": item.run_id,
                "external_run_id": item.external_run_id,
                "source_id": item.source_id,
                "stable_test_id": item.stable_test_id,
                "source_test_identity": item.source_test_identity,
                "data_row": item.data_row,
                "result": item.result,
                "configuration": item.configuration,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in record.failures
        ],
        "attempts": [
            {
                "fact_key": item.fact_key,
                "run_id": item.run_id,
                "external_run_id": item.external_run_id,
                "stable_test_id": item.stable_test_id,
                "source_test_identity": item.source_test_identity,
                "data_row": item.data_row,
                "attempt": item.attempt,
                "result": item.result,
                "duration_seconds": item.duration_seconds,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in record.attempts
        ],
    }


def _metric_payload(item: MetricValue) -> dict[str, object]:
    return {
        "metric_id": item.metric_id.value,
        "numerator": item.numerator,
        "denominator": item.denominator,
        "value": item.value,
        "completeness": item.completeness,
        "missing_reasons": list(item.missing_reasons),
        "evidence_refs": list(item.evidence_refs),
    }


def _row_to_query_record(row: RowMapping) -> QueryRecord:
    filters = _json_value(row["filters"])
    payload = _json_value(row["result_payload"])
    query = MetricQuery(
        metric_ids=tuple(MetricId(item["metric_id"]) for item in payload["metrics"]),
        filters=QueryFilters(
            source_ids=tuple(filters["source_ids"]),
            run_ids=tuple(filters["run_ids"]),
            build_ids=tuple(filters.get("build_ids", ())),
            branches=tuple(filters.get("branches", ())),
            environments=tuple(filters["environments"]),
            configurations=tuple(filters["configurations"]),
        ),
        from_date=str(row["from_date"]),
        to_date=str(row["to_date"]),
        timezone=str(row["timezone"]),
        as_of=_aware_utc(row["as_of"]),
    )
    return QueryRecord(
        query_id=str(row["query_id"]),
        project_id=str(row["project_id"]),
        metric_version=str(row["metric_version"]),
        query=query,
        snapshot=ProjectionSnapshot(
            projection_version=str(row["projection_version"]),
            visible_data_version=int(row["visible_data_version"]),
        ),
        created_at=_aware_utc(row["created_at"]),
        metrics=tuple(_metric_value(item) for item in payload["metrics"]),
        trends=tuple(
            TrendPoint(
                local_date=str(point["local_date"]),
                metrics=tuple(_metric_value(item) for item in point["metrics"]),
            )
            for point in payload["trends"]
        ),
        runs=tuple(
            RunSummary(
                run_id=str(item["run_id"]),
                external_run_id=str(item.get("external_run_id", item["run_id"])),
                source_id=str(item["source_id"]),
                build_id=item.get("build_id"),
                branch=item.get("branch"),
                environment=str(item["environment"]),
                configuration=str(item["configuration"]),
                started_at=(
                    datetime.fromisoformat(item["started_at"])
                    if item["started_at"] is not None
                    else None
                ),
                instance_count=int(item["instance_count"]),
                evidence_refs=tuple(item["evidence_refs"]),
            )
            for item in payload["runs"]
        ),
        failures=tuple(
            FailureDetail(
                fact_key=str(item["fact_key"]),
                run_id=str(item["run_id"]),
                external_run_id=str(item.get("external_run_id", item["run_id"])),
                source_id=str(item["source_id"]),
                stable_test_id=item["stable_test_id"],
                source_test_identity=str(item["source_test_identity"]),
                data_row=item["data_row"],
                result=cast(Literal["fail", "error"], item["result"]),
                configuration=str(item["configuration"]),
                evidence_refs=tuple(item["evidence_refs"]),
            )
            for item in payload["failures"]
        ),
        attempts=tuple(
            AttemptDetail(
                fact_key=str(item["fact_key"]),
                run_id=str(item["run_id"]),
                external_run_id=str(item.get("external_run_id", item["run_id"])),
                stable_test_id=item["stable_test_id"],
                source_test_identity=str(item["source_test_identity"]),
                data_row=item["data_row"],
                attempt=(int(item["attempt"]) if item["attempt"] is not None else None),
                result=str(item["result"]),
                duration_seconds=(
                    float(item["duration_seconds"])
                    if item["duration_seconds"] is not None
                    else None
                ),
                evidence_refs=tuple(item["evidence_refs"]),
            )
            for item in payload["attempts"]
        ),
    )


def _metric_value(value: dict[str, Any]) -> MetricValue:
    return MetricValue(
        metric_id=MetricId(str(value["metric_id"])),
        numerator=(int(value["numerator"]) if value["numerator"] is not None else None),
        denominator=(
            int(value["denominator"]) if value["denominator"] is not None else None
        ),
        value=float(value["value"]) if value["value"] is not None else None,
        completeness=cast(
            Literal["complete", "empty", "unavailable"], value["completeness"]
        ),
        missing_reasons=tuple(value["missing_reasons"]),
        evidence_refs=tuple(value["evidence_refs"]),
    )


def _json_value(value: object) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("stored query JSON must be an object")
    return parsed
