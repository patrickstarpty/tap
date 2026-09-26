"""Restart-safe report mapping worker."""

from __future__ import annotations

import os
import time
from pathlib import Path
from collections.abc import Iterable
from typing import Protocol

from sqlalchemy import create_engine

from tap_platform.insights.adapters.junit import JUnitSecurityError, parse_junit
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.domain.reports import (
    ReportManifest,
    ReportReceipt,
    ReportState,
    TestAttemptFact,
)


class WorkerLedger(Protocol):
    def get_receipt(self, receipt_id: str) -> ReportReceipt: ...
    def get_manifest(self, receipt_id: str) -> ReportManifest: ...
    def transition(
        self,
        receipt_id: str,
        *,
        expected: ReportState,
        target: ReportState,
        reason: str | None = None,
        attempts: Iterable[TestAttemptFact] | None = None,
    ) -> ReportReceipt: ...
    def next_processable(self, *, limit: int = 100) -> list[str]: ...


class WorkerObjects(Protocol):
    def read(self, ref: str) -> bytes: ...


class ReportWorker:
    def __init__(self, *, ledger: WorkerLedger, objects: WorkerObjects) -> None:
        self._ledger = ledger
        self._objects = objects

    def process_one(self, receipt_id: str) -> bool:
        receipt = self._ledger.get_receipt(receipt_id)
        if receipt.state is ReportState.RECEIVED:
            self._ledger.transition(
                receipt_id,
                expected=ReportState.RECEIVED,
                target=ReportState.VALIDATING,
            )
            return True
        if receipt.state is ReportState.VALIDATING:
            try:
                attempts = parse_junit(
                    self._objects.read(receipt.raw_object_ref),
                    self._ledger.get_manifest(receipt_id),
                )
            except JUnitSecurityError as exc:
                self._ledger.transition(
                    receipt_id,
                    expected=ReportState.VALIDATING,
                    target=ReportState.REJECTED,
                    reason=exc.reason,
                )
                return True
            except Exception:
                self._ledger.transition(
                    receipt_id,
                    expected=ReportState.VALIDATING,
                    target=ReportState.FAILED,
                    reason="mapping-failed",
                )
                return True
            try:
                self._ledger.transition(
                    receipt_id,
                    expected=ReportState.VALIDATING,
                    target=ReportState.MAPPED,
                    attempts=attempts,
                )
            except Exception:
                self._ledger.transition(
                    receipt_id,
                    expected=ReportState.VALIDATING,
                    target=ReportState.FAILED,
                    reason="mapping-persistence-failed",
                )
            return True
        if receipt.state is ReportState.MAPPED:
            self._ledger.transition(
                receipt_id,
                expected=ReportState.MAPPED,
                target=ReportState.PROJECTING,
            )
            return True
        if receipt.state is ReportState.PROJECTING:
            return False
        return False

    def process_available(self, *, limit: int = 100) -> int:
        processed = 0
        for receipt_id in self._ledger.next_processable(limit=limit):
            try:
                processed += int(self.process_one(receipt_id))
            except Exception:
                # A durable receipt remains discoverable on the next scan. One
                # unavailable or malformed record must not stop the worker loop.
                continue
        return processed

    def confirm_projection(self, receipt_id: str) -> bool:
        """Mark ready only after the projection owner confirms durable success."""
        receipt = self._ledger.get_receipt(receipt_id)
        if receipt.state is not ReportState.PROJECTING:
            return False
        updated = self._ledger.transition(
            receipt_id,
            expected=ReportState.PROJECTING,
            target=ReportState.READY,
        )
        return updated.state is ReportState.READY

    def retry_failed(self, receipt_id: str) -> bool:
        """Retry mapping of the stored artifact; never trigger the source test run."""
        receipt = self._ledger.get_receipt(receipt_id)
        if receipt.state is not ReportState.FAILED:
            return False
        self._ledger.transition(
            receipt_id,
            expected=ReportState.FAILED,
            target=ReportState.VALIDATING,
            reason="mapping-retry-requested",
        )
        return True


def main() -> None:
    database_url = os.getenv("TAP_DATABASE_URL")
    object_root = os.getenv("TAP_REPORT_OBJECT_ROOT")
    if not database_url or not object_root:
        raise RuntimeError(
            "TAP_DATABASE_URL and TAP_REPORT_OBJECT_ROOT are required for the worker"
        )
    poll_seconds = float(os.getenv("TAP_REPORT_WORKER_POLL_SECONDS", "1"))
    batch_size = int(os.getenv("TAP_REPORT_WORKER_BATCH_SIZE", "100"))
    if not 0.05 <= poll_seconds <= 60:
        raise ValueError("TAP_REPORT_WORKER_POLL_SECONDS must be between 0.05 and 60")
    worker = ReportWorker(
        ledger=SqlAlchemyReportLedger(create_engine(database_url, pool_pre_ping=True)),
        objects=FileReportObjectStore(Path(object_root)),
    )
    while True:
        if worker.process_available(limit=batch_size) == 0:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
