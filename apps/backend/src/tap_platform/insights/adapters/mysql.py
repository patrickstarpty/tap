"""TAP-owned SQLAlchemy receipt ledger with MySQL transaction semantics."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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

from tap_platform.insights.adapters.junit import PARSER_VERSION
from tap_platform.insights.adapters.objects import StoredObject
from tap_platform.insights.domain.reports import (
    Completeness,
    ReportManifest,
    ReportReceipt,
    ReportState,
    TestAttemptFact,
)


metadata = MetaData()

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


_ALLOWED_TRANSITIONS: dict[ReportState, set[ReportState]] = {
    ReportState.RECEIVED: {ReportState.VALIDATING},
    ReportState.VALIDATING: {
        ReportState.MAPPED,
        ReportState.REJECTED,
        ReportState.FAILED,
    },
    ReportState.MAPPED: {ReportState.PROJECTING},
    ReportState.PROJECTING: {ReportState.READY, ReportState.FAILED},
    ReportState.FAILED: {ReportState.VALIDATING, ReportState.PROJECTING},
}


def _now() -> datetime:
    return datetime.now(UTC)


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
