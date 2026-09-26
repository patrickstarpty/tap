"""Restart-safe report mapping worker."""

from __future__ import annotations

from typing import Protocol

from tap_platform.insights.adapters.junit import JUnitSecurityError, parse_junit
from tap_platform.insights.domain.reports import ReportState


class WorkerLedger(Protocol):
    def get_receipt(self, receipt_id: str): ...
    def get_manifest(self, receipt_id: str): ...
    def transition(self, receipt_id: str, **kwargs): ...


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
            self._ledger.transition(
                receipt_id,
                expected=ReportState.VALIDATING,
                target=ReportState.MAPPED,
                attempts=attempts,
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
            self._ledger.transition(
                receipt_id,
                expected=ReportState.PROJECTING,
                target=ReportState.READY,
            )
            return True
        return False

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
