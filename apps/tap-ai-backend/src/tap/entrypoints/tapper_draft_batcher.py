"""Batches a `model` graph node's raw text deltas into redacted Redis draft
chunks.

`DraftBatcher` sits between `on_text` (fed one raw, unredacted delta at a
time as the model streams) and `DraftStream.append` (Task 4): it accumulates
the full raw answer seen so far, periodically re-redacts *all* of it (never
just the newest delta -- a pattern like a phone number can straddle two
deltas, so only redacting the whole accumulated text on every flush can ever
catch it), and sends only the newly-safe suffix. A `holdback_chars` margin at
the tail of the redacted text is never sent until `close()`, giving a
not-yet-finished pattern more raw context to complete before its region is
ever exposed. If redacting more context ever rewrites a prefix already sent
(the redactor's classification of earlier text changed once more text
arrived), a `reset: true` chunk is sent first so readers discard everything
sent so far for this attempt, then the newly (correctly) redacted text is
resent from the start.

The `INSUFFICIENT_EVIDENCE` abstention sentinel (the knowledge gateway's
"no grounded answer" marker, parsed by `parse_inline_answer` after the
stream completes) must never reach Redis: it is stripped from the raw text
before redaction/send on every flush, so a turn that abstains after
streaming nothing but the sentinel sends and returns an empty draft.

Any `DraftStreamUnavailable` from `drafts.append` (or `drafts` being `None`)
degrades the batcher to accumulate-only for the rest of this instance's life
(never raises into the `model` node) and records `tap.stream.degraded` on
the current span once.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from opentelemetry.trace import get_current_span

from tap.modules.chat.application.ports import DraftStream, DraftStreamUnavailable

_INSUFFICIENT_EVIDENCE_SENTINEL = "INSUFFICIENT_EVIDENCE"


class DraftBatcher:
    """One instance per `model` node invocation (one Turn attempt)."""

    def __init__(
        self,
        *,
        turn_id: str,
        attempt: int,
        drafts: DraftStream | None,
        redact: Callable[[str], Awaitable[str]],
        max_delay_s: float = 0.05,
        max_chars: int = 32,
        holdback_chars: int = 48,
    ) -> None:
        self._turn_id = turn_id
        self._attempt = attempt
        self._drafts = drafts
        self._redact = redact
        self._max_delay_s = max_delay_s
        self._max_chars = max_chars
        self._holdback_chars = holdback_chars
        self._raw = ""
        self._unflushed_chars = 0
        self._last_flush_at = time.monotonic()
        self._sent_text = ""
        self._seq = 0
        self._degraded = drafts is None
        self._last_redacted = ""

    async def push(self, text: str) -> None:
        if not text:
            return
        self._raw += text
        self._unflushed_chars += len(text)
        now = time.monotonic()
        if (
            self._unflushed_chars >= self._max_chars
            or (now - self._last_flush_at) >= self._max_delay_s
        ):
            await self._flush(final=False)

    async def close(self) -> str:
        await self._flush(final=True)
        return self._last_redacted

    async def _flush(self, *, final: bool) -> None:
        # The sentinel is only ever the model's *entire* output (never mixed
        # into a real answer), so stripping it out of the full raw text before
        # redaction keeps it from ever reaching Redis or the caller, at any
        # flush -- including a final one where the whole answer is nothing
        # but the sentinel.
        visible_raw = self._raw.replace(_INSUFFICIENT_EVIDENCE_SENTINEL, "")
        redacted = await self._redact(visible_raw)
        self._last_redacted = redacted
        self._last_flush_at = time.monotonic()
        self._unflushed_chars = 0

        if self._sent_text and not redacted.startswith(self._sent_text):
            # More context changed the redactor's classification of an
            # already-sent prefix (e.g. a pattern that straddled two
            # batches): the previously-sent text can no longer be trusted,
            # so every reader must discard it and the correctly redacted
            # text is resent from the start.
            await self._append(text="", reset=True)
            self._sent_text = ""

        upper = (
            len(redacted)
            if final
            else max(len(self._sent_text), len(redacted) - self._holdback_chars)
        )
        new_text = redacted[len(self._sent_text) : upper]
        if new_text:
            await self._append(text=new_text, reset=False)
            self._sent_text += new_text

    async def _append(self, *, text: str, reset: bool) -> None:
        if self._degraded or self._drafts is None:
            self._mark_degraded()
            return
        self._seq += 1
        try:
            await self._drafts.append(
                self._turn_id, attempt=self._attempt, seq=self._seq, text=text, reset=reset
            )
        except DraftStreamUnavailable:
            self._mark_degraded()

    def _mark_degraded(self) -> None:
        if not self._degraded:
            get_current_span().set_attribute("tap.stream.degraded", True)
        self._degraded = True
