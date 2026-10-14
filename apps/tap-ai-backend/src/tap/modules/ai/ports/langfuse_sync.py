"""Port for the Langfuse sync worker's MySQL reads; a system store, not Project-scoped."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tap.modules.ai.domain.langfuse_sync import FeedbackRecord, SyncKind, TraceBundle


class LangfuseSyncStore(Protocol):
    async def scan_traces(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[list[str], datetime | None]: ...

    async def scan_feedback(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[list[FeedbackRecord], datetime | None]: ...

    async def advance(self, name: SyncKind, position: datetime) -> None: ...

    async def load_trace(self, trace_id: str) -> TraceBundle | None: ...

    async def load_feedback(self, feedback_id: str) -> FeedbackRecord | None: ...

    async def failures(self, kind: SyncKind, limit: int) -> list[str]: ...

    async def record_failure(self, kind: SyncKind, subject_id: str, error: str) -> None: ...

    async def clear_failure(self, kind: SyncKind, subject_id: str) -> None: ...


__all__ = ["LangfuseSyncStore"]
