"""Durable report intake: persist bytes before committing receipt and outbox."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Protocol

from tap_platform.insights.adapters.objects import ObjectTooLarge, StoredObject
from tap_platform.insights.domain.reports import ReportManifest, ReportReceipt


class UploadTooLarge(ValueError):
    """The bounded raw report stream exceeded its configured limit."""


class ReportObjectStore(Protocol):
    def persist(self, chunks: Iterable[bytes], *, max_bytes: int) -> StoredObject: ...
    def intake_guard(self) -> AbstractContextManager[None]: ...


class ReportLedger(Protocol):
    def accept(self, manifest: ReportManifest, raw: StoredObject) -> ReportReceipt: ...


class ReportIntake:
    def __init__(
        self,
        *,
        ledger: ReportLedger,
        objects: ReportObjectStore,
        max_upload_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        self._ledger = ledger
        self._objects = objects
        self.max_upload_bytes = max_upload_bytes

    def receive(
        self, manifest: ReportManifest, chunks: Iterable[bytes]
    ) -> ReportReceipt:
        with self._objects.intake_guard():
            try:
                raw = self._objects.persist(chunks, max_bytes=self.max_upload_bytes)
            except ObjectTooLarge as exc:
                raise UploadTooLarge(str(exc)) from exc
            return self._ledger.accept(manifest, raw)
