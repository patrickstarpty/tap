"""Replayable report-to-ClickHouse projection and verified version rebuild."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol, TypedDict, cast

from tap_platform.insights.adapters.clickhouse import (
    ClickHouseInsightsStore,
    ProjectionBatch,
)
from tap_platform.insights.adapters.junit import parse_junit
from tap_platform.insights.domain.projection import (
    ProjectionReservation,
    ProjectionSnapshot,
    RebuildResult,
)
from tap_platform.insights.domain.reports import (
    ReportManifest,
    ReportReceipt,
    ReportState,
    TestAttemptFact,
)


class ProjectionLedger(Protocol):
    def get_receipt(self, receipt_id: str) -> ReportReceipt: ...
    def get_manifest(self, receipt_id: str) -> ReportManifest: ...
    def attempts_for(self, receipt_id: str) -> list[TestAttemptFact]: ...
    def projection_snapshot(
        self, projection_version: str | None = None
    ) -> ProjectionSnapshot: ...
    def reserve_projection_batch(
        self,
        *,
        receipt_id: str,
        payload_checksum: str,
        row_count: int,
        projection_version: str | None = None,
    ) -> ProjectionReservation: ...
    def complete_projection_batch(
        self,
        reservation: ProjectionReservation,
        *,
        mark_receipt_ready: bool = True,
    ) -> bool: ...
    def create_projection_version(self, projection_version: str) -> None: ...
    def activate_projection_version(
        self,
        projection_version: str,
        *,
        row_count: int,
        checksum: str,
        expected_active_projection_version: str,
        expected_visible_data_version: int,
    ) -> None: ...
    def receipts_in_projection(
        self, projection_version: str
    ) -> list[ReportReceipt]: ...


class ProjectionObjects(Protocol):
    def read(self, ref: str) -> bytes: ...


class SemanticProjection(TypedDict):
    scope: dict[str, object]
    attempts: list[dict[str, object]]
    evidence: list[dict[str, object]]
    run_dimension: dict[str, object]
    configuration_dimension: dict[str, object]


class ProjectionCoordinator:
    def __init__(
        self, *, ledger: ProjectionLedger, store: ClickHouseInsightsStore
    ) -> None:
        self._ledger = ledger
        self._store = store

    def project_receipt(self, receipt_id: str) -> bool:
        receipt = self._ledger.get_receipt(receipt_id)
        if receipt.state not in {ReportState.PROJECTING, ReportState.READY}:
            raise ValueError("projection requires an authoritative projecting receipt")
        manifest = self._ledger.get_manifest(receipt_id)
        attempts = self._ledger.attempts_for(receipt_id)
        return self._project(
            receipt=receipt,
            manifest=manifest,
            attempts=attempts,
            projection_version=None,
            mark_receipt_ready=True,
        )

    def _project(
        self,
        *,
        receipt: ReportReceipt,
        manifest: ReportManifest,
        attempts: list[TestAttemptFact],
        projection_version: str | None,
        mark_receipt_ready: bool,
    ) -> bool:
        semantic = _semantic_projection(receipt, manifest, attempts)
        payload_checksum = _checksum(semantic)
        reservation = self._ledger.reserve_projection_batch(
            receipt_id=receipt.receipt_id,
            payload_checksum=payload_checksum,
            row_count=len(attempts),
            projection_version=projection_version,
        )
        if reservation.complete:
            return False
        batch = _materialize_batch(
            semantic=semantic,
            payload_checksum=payload_checksum,
            reservation=reservation,
        )
        self._store.append_details(batch)
        self._store.append_marker(batch)
        return self._ledger.complete_projection_batch(
            reservation, mark_receipt_ready=mark_receipt_ready
        )


class ProjectionRebuilder:
    def __init__(
        self,
        *,
        ledger: ProjectionLedger,
        objects: ProjectionObjects,
        store: ClickHouseInsightsStore,
    ) -> None:
        self._ledger = ledger
        self._objects = objects
        self._store = store

    def rebuild(self, *, target_version: str) -> RebuildResult:
        source = self._ledger.projection_snapshot()
        if target_version == source.projection_version:
            raise ValueError("rebuild target must differ from the active projection")
        receipts = self._ledger.receipts_in_projection(source.projection_version)
        self._ledger.create_projection_version(target_version)
        coordinator = ProjectionCoordinator(ledger=self._ledger, store=self._store)
        semantic_batches: list[SemanticProjection] = []
        for receipt in receipts:
            manifest = self._ledger.get_manifest(receipt.receipt_id)
            attempts = parse_junit(self._objects.read(receipt.raw_object_ref), manifest)
            semantic = _semantic_projection(receipt, manifest, attempts)
            semantic_batches.append(semantic)
            coordinator._project(
                receipt=receipt,
                manifest=manifest,
                attempts=attempts,
                projection_version=target_version,
                mark_receipt_ready=False,
            )
        snapshot = self._ledger.projection_snapshot(target_version)
        expected_checksums = _effective_oracle_checksums(semantic_batches)
        oracle_checksum = hashlib.sha256(
            "\n".join(expected_checksums).encode()
        ).hexdigest()
        clickhouse_attempts = self._store.effective_attempts(snapshot)
        clickhouse_checksum = self._store.effective_checksum(snapshot)
        if len(clickhouse_attempts) != len(expected_checksums):
            raise RuntimeError("rebuild row-count oracle mismatch")
        if clickhouse_checksum != oracle_checksum:
            raise RuntimeError("rebuild checksum oracle mismatch")
        self._ledger.activate_projection_version(
            target_version,
            row_count=len(expected_checksums),
            checksum=oracle_checksum,
            expected_active_projection_version=source.projection_version,
            expected_visible_data_version=source.visible_data_version,
        )
        return RebuildResult(
            projection_version=target_version,
            row_count=len(expected_checksums),
            oracle_checksum=oracle_checksum,
            clickhouse_checksum=clickhouse_checksum,
        )


def _semantic_projection(
    receipt: ReportReceipt,
    manifest: ReportManifest,
    attempts: list[TestAttemptFact],
) -> SemanticProjection:
    scope = {
        "receipt_id": receipt.receipt_id,
        "project_id": receipt.project_id,
        "source_id": receipt.source_id,
        "external_run_id": receipt.external_run_id,
        "report_batch_id": receipt.batch_id,
        "shard_id": receipt.shard_id,
        "correction_no": receipt.correction_no,
        "raw_object_ref": receipt.raw_object_ref,
        "raw_checksum": receipt.checksum,
        "parser_version": receipt.parser_version,
        "application_commit": manifest.application_commit,
        "script_commit": manifest.script_commit,
        "environment": manifest.environment,
        "configuration": manifest.configuration,
        "timezone": manifest.timezone,
    }
    attempt_rows: list[dict[str, object]] = []
    evidence_rows: list[dict[str, object]] = []
    for fact in attempts:
        logical_identity = [
            scope[field]
            for field in (
                "project_id",
                "source_id",
                "external_run_id",
                "report_batch_id",
                "shard_id",
                "application_commit",
                "script_commit",
                "environment",
                "configuration",
                "timezone",
            )
        ] + [
            receipt.correction_no,
            fact.stable_test_id or fact.source_test_identity,
            fact.data_row,
            fact.attempt,
        ]
        fact_key = _checksum(logical_identity)
        value = {
            **scope,
            "fact_key": fact_key,
            "source_test_identity": fact.source_test_identity,
            "source_locator": fact.source_locator,
            "stable_test_id": fact.stable_test_id or "",
            "stable_test_id_present": int(fact.stable_test_id is not None),
            "data_row": fact.data_row or "",
            "data_row_present": int(fact.data_row is not None),
            "attempt": fact.attempt or 0,
            "attempt_present": int(fact.attempt is not None),
            "result": fact.result,
            "duration_seconds": fact.duration_seconds,
            "missing_reasons": list(fact.missing_reasons),
            "first_attempt_eligible": int(fact.first_attempt_eligible),
        }
        value["fact_checksum"] = _checksum(value)
        attempt_rows.append(value)
        for evidence_ref in fact.evidence_refs:
            evidence = {
                **scope,
                "fact_key": fact_key,
                "evidence_ref": evidence_ref,
            }
            evidence["evidence_checksum"] = _checksum(evidence)
            evidence_rows.append(evidence)
    run_dimension = {
        **scope,
        "job_id": manifest.job_id,
        "build_id": manifest.build_id,
        "branch": manifest.branch,
        "business_cycle_id": manifest.business_cycle_id,
        "started_at": manifest.started_at,
        "finished_at": manifest.finished_at,
    }
    run_dimension["dimension_checksum"] = _checksum(run_dimension)
    configuration_dimension = {
        **scope,
        "configuration_key": _checksum(
            [
                receipt.project_id,
                manifest.application_commit,
                manifest.script_commit,
                manifest.environment,
                manifest.configuration,
                manifest.timezone,
            ]
        ),
    }
    configuration_dimension["dimension_checksum"] = _checksum(configuration_dimension)
    return {
        "scope": scope,
        "attempts": attempt_rows,
        "evidence": evidence_rows,
        "run_dimension": run_dimension,
        "configuration_dimension": configuration_dimension,
    }


def _materialize_batch(
    *,
    semantic: SemanticProjection,
    payload_checksum: str,
    reservation: ProjectionReservation,
) -> ProjectionBatch:
    transport = {
        "projection_version": reservation.projection_version,
        "data_version": reservation.data_version,
        "projection_batch_id": reservation.projection_batch_id,
    }
    scope = dict(semantic["scope"])
    marker = {**transport, **scope, "payload_checksum": payload_checksum}
    marker["is_deleted"] = int(not semantic["attempts"])
    marker["marker_checksum"] = _checksum(marker)

    def attach(row: dict[str, object]) -> dict[str, object]:
        return {**transport, **row}

    return ProjectionBatch(
        reservation=reservation,
        marker=marker,
        attempts=tuple(attach(row) for row in semantic["attempts"]),
        evidence=tuple(attach(row) for row in semantic["evidence"]),
        run_dimension=attach(semantic["run_dimension"]),
        configuration_dimension=attach(semantic["configuration_dimension"]),
    )


def _effective_oracle_checksums(
    batches: list[SemanticProjection],
) -> list[str]:
    selected: dict[tuple[object, ...], SemanticProjection] = {}
    fields = (
        "project_id",
        "source_id",
        "external_run_id",
        "report_batch_id",
        "shard_id",
        "application_commit",
        "script_commit",
        "environment",
        "configuration",
        "timezone",
    )
    for batch in batches:
        scope = batch["scope"]
        key = tuple(scope[field] for field in fields)
        current = selected.get(key)
        if current is None or cast(int, scope["correction_no"]) > cast(
            int, current["scope"]["correction_no"]
        ):
            selected[key] = batch
    return sorted(
        str(row["fact_checksum"])
        for batch in selected.values()
        for row in batch["attempts"]
    )


def _checksum(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
