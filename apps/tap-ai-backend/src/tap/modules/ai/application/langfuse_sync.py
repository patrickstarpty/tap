"""`LangfuseSyncService`: scan cursors, push through a `LangfuseSink`, record failures,
advance cursors. No SDK and no DB here — `LangfuseSyncStore`/`LangfuseSink` are ports;
the MySQL store and the Langfuse-SDK sink are wired in elsewhere.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from tap.modules.ai.application.export_redaction import ExportRedactor
from tap.modules.ai.application.langfuse_mapping import feedback_score_id, to_langfuse_trace
from tap.modules.ai.domain.langfuse_sync import FeedbackRecord, LangfuseScore
from tap.modules.ai.ports.langfuse_sync import LangfuseSink, LangfuseSyncStore

_UP = "up"
_SCORE_NAME = "user_feedback"


@dataclass(frozen=True, slots=True)
class SyncReport:
    traces_ok: int
    traces_failed: int
    feedback_ok: int
    feedback_failed: int


class LangfuseSyncService:
    def __init__(
        self,
        store: LangfuseSyncStore,
        sink: LangfuseSink,
        redactor: ExportRedactor,
        *,
        environment: str,
        settle_seconds: int = 60,
        batch_size: int = 200,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._sink = sink
        self._redactor = redactor
        self._environment = environment
        self._settle_seconds = settle_seconds
        self._batch_size = batch_size
        self._clock = clock

    async def run_once(self) -> SyncReport:
        traces_ok, traces_failed = await self._sync_traces()
        feedback_ok, feedback_failed = await self._sync_feedback()
        return SyncReport(
            traces_ok=traces_ok,
            traces_failed=traces_failed,
            feedback_ok=feedback_ok,
            feedback_failed=feedback_failed,
        )

    async def _sync_traces(self) -> tuple[int, int]:
        ok = 0
        failed = 0
        cutoff = self._clock() - timedelta(seconds=self._settle_seconds)

        for trace_id in await self._store.failures("trace", self._batch_size):
            outcome = await self._push_trace(trace_id)
            if outcome is True:
                ok += 1
            elif outcome is False:
                failed += 1

        trace_ids, position = await self._store.scan_traces(cutoff=cutoff, limit=self._batch_size)
        for trace_id in trace_ids:
            outcome = await self._push_trace(trace_id)
            if outcome is True:
                ok += 1
            elif outcome is False:
                failed += 1

        # The scan/store call above did not raise, so this page's rows are fully
        # accounted for (pushed or recorded as a failure to retry); a per-item push
        # failure never blocks the cursor from advancing past that item.
        if position is not None:
            await self._store.advance("trace", position)
        return ok, failed

    async def _push_trace(self, trace_id: str) -> bool | None:
        """Returns True (pushed), False (failed, recorded for retry), or None (gone)."""
        try:
            bundle = await self._store.load_trace(trace_id)
        except Exception as error:  # noqa: BLE001 - one bad trace must not block others
            await self._store.record_failure("trace", trace_id, str(error))
            return False
        if bundle is None:
            await self._store.clear_failure("trace", trace_id)
            return None
        try:
            trace = to_langfuse_trace(bundle, self._redactor, environment=self._environment)
            await self._sink.push_trace(trace)
        except Exception as error:  # noqa: BLE001 - one bad trace must not block others
            await self._store.record_failure("trace", trace_id, str(error))
            return False
        await self._store.clear_failure("trace", trace_id)
        return True

    async def _sync_feedback(self) -> tuple[int, int]:
        ok = 0
        failed = 0
        cutoff = self._clock() - timedelta(seconds=self._settle_seconds)

        for feedback_id in await self._store.failures("feedback", self._batch_size):
            outcome = await self._retry_feedback(feedback_id)
            if outcome is True:
                ok += 1
            elif outcome is False:
                failed += 1

        records, position = await self._store.scan_feedback(cutoff=cutoff, limit=self._batch_size)
        for record in records:
            if await self._push_feedback(record):
                ok += 1
            else:
                failed += 1

        if position is not None:
            await self._store.advance("feedback", position)
        return ok, failed

    async def _retry_feedback(self, feedback_id: str) -> bool | None:
        try:
            record = await self._store.load_feedback(feedback_id)
        except Exception as error:  # noqa: BLE001 - one bad row must not block others
            await self._store.record_failure("feedback", feedback_id, str(error))
            return False
        if record is None:
            await self._store.clear_failure("feedback", feedback_id)
            return None
        return await self._push_feedback(record)

    async def _push_feedback(self, record: FeedbackRecord) -> bool:
        score_id = feedback_score_id(record.turn_id, record.actor_id)
        try:
            if record.rating is None:
                await self._sink.delete_score(score_id)
            else:
                if record.trace_id is None:
                    raise ValueError(f"feedback {record.feedback_id} has no trace_id yet")
                comment = (
                    self._redactor.redact(record.comment) if record.comment is not None else None
                )
                score = LangfuseScore(
                    id=score_id,
                    trace_id=record.trace_id,
                    name=_SCORE_NAME,
                    value=1.0 if record.rating == _UP else 0.0,
                    comment=comment,
                    timestamp=record.created_at,
                )
                await self._sink.upsert_score(score)
        except Exception as error:  # noqa: BLE001 - one bad row must not block others
            await self._store.record_failure("feedback", record.feedback_id, str(error))
            return False
        await self._store.clear_failure("feedback", record.feedback_id)
        return True


__all__ = ["LangfuseSyncService", "SyncReport"]
