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

`redact` raising `RedactionUnavailable` (a half-written credential value or
private-key block that has not yet been closed out by more raw text) never
raises into the `model` node either: a non-final flush simply sends nothing
and keeps waiting for more context; a final flush (`close()`) instead
degrades the batcher permanently (nothing more is ever redacted or sent --
the authoritative, fail-closed redaction decision for the complete answer
stays with `KnowledgeHttpService.finalize_conversation_answer`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable

from opentelemetry.trace import get_current_span

from tap.modules.chat.application.ports import DraftStream, DraftStreamUnavailable
from tap.modules.knowledge.ports.redaction import RedactionUnavailable
from tap.platform.redaction_patterns import EMAIL_LOCAL_TRAILING_RUN

_INSUFFICIENT_EVIDENCE_SENTINEL = "INSUFFICIENT_EVIDENCE"

# Mirrors `PatternEgressRedactor`'s placeholder markers -> the hit-count key
# `KnowledgeHttpService._redact_answer_response` records on
# `tap.guard.redaction.output_hits` for the final answer. The batcher never
# sees the redactor's own `redact_with_report` counts (its `redact` callable
# only returns text), so hit counts here are approximated by counting the
# placeholders actually present in the last successfully redacted text.
_REDACTION_MARKERS = {
    "[REDACTED_PRIVATE_KEY]": "private_key",
    "[REDACTED_SECRET]": "secret",
    "[REDACTED_ID]": "id_number",
    "[REDACTED_PHONE]": "phone",
    "[REDACTED_EMAIL]": "email",
}


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
        # Set only when `_redact` raised `RedactionUnavailable` on a *final*
        # flush (`close()`): no more raw text is ever coming for this
        # attempt, so the half-written pattern that made redaction fail can
        # never complete -- every later `_flush` (there should be none) is a
        # no-op, and `redact` is never called again for this instance.
        # `finalize`'s own fail-closed redaction remains the sole
        # authoritative decision for the saved/returned answer either way.
        self._redaction_unavailable = False
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
        if self._redaction_unavailable:
            # Degraded by a `RedactionUnavailable` that never got the chance
            # to redact this attempt's complete answer (this call's own
            # final flush, or an earlier one): `_last_redacted` is stale --
            # at best a partial redaction of an earlier, shorter raw text --
            # and must never be returned as if it were this attempt's
            # answer, nor counted into its redaction-hit stats.
            # `KnowledgeHttpService.finalize_conversation_answer` remains the
            # sole authoritative, fail-closed redaction decision either way.
            return ""
        self._record_redaction_hits()
        return self._last_redacted

    async def _flush(self, *, final: bool) -> None:
        if self._redaction_unavailable:
            self._last_flush_at = time.monotonic()
            self._unflushed_chars = 0
            return
        # The sentinel is only ever the model's *entire* output (never mixed
        # into a real answer), so stripping it out of the full raw text before
        # redaction keeps it from ever reaching Redis or the caller, at any
        # flush -- including a final one where the whole answer is nothing
        # but the sentinel.
        visible_raw = self._raw.replace(_INSUFFICIENT_EVIDENCE_SENTINEL, "")
        try:
            redacted = await self._redact(visible_raw)
        except RedactionUnavailable:
            self._last_flush_at = time.monotonic()
            self._unflushed_chars = 0
            if final:
                # No more raw text is ever coming for this attempt (a
                # half-written `password:`, an unterminated quoted
                # credential value, a `-----BEGIN ... PRIVATE KEY-----`
                # block with no `END` marker, ...): this attempt can never
                # again produce a safe redacted draft, so nothing more is
                # ever sent -- never the raw text.
                self._redaction_unavailable = True
                self._mark_degraded()
            # Non-final: send nothing this flush and keep the current sent
            # position; more raw text may still complete the pattern (close
            # the quote, reach the credential block's `END` marker, ...),
            # letting a later flush redact successfully.
            return
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

        if final:
            upper = len(redacted)
        else:
            upper = max(len(self._sent_text), len(redacted) - self._holdback_chars)
            # M-1: an email's local part has no fixed length, so a trailing
            # run of its allowed characters that is not yet terminated (no
            # `@` has completed it, or anything else has ruled it out) could
            # still grow past `holdback_chars` before it is known whether it
            # is part of an email at all -- never send into that run. Only
            # extends an actual (non-zero) holdback margin: `holdback_chars
            # == 0` means the caller already opted out of any holdback
            # safety net.
            if self._holdback_chars:
                trailing_run = EMAIL_LOCAL_TRAILING_RUN.search(redacted)
                if trailing_run is not None:
                    upper = min(upper, trailing_run.start())
                upper = max(upper, len(self._sent_text))
        new_text = redacted[len(self._sent_text) : upper]
        if new_text:
            await self._append(text=new_text, reset=False)
            self._sent_text += new_text

    def _record_redaction_hits(self) -> None:
        # Matches `KnowledgeHttpService._redact_answer_response`'s
        # `tap.guard.redaction.output_hits` style (spec SS4.5): only set when
        # there is something to report. Named distinctly (`draft_hits`) since
        # this counts placeholders in the batcher's own last redacted draft,
        # not the redactor's authoritative `redact_with_report` counts.
        if not self._last_redacted:
            return
        hits = {
            kind: self._last_redacted.count(marker)
            for marker, kind in _REDACTION_MARKERS.items()
            if marker in self._last_redacted
        }
        if hits:
            get_current_span().set_attribute("tap.guard.redaction.draft_hits", json.dumps(hits))

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
