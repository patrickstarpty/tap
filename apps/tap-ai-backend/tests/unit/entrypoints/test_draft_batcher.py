"""`DraftBatcher` batches raw model text deltas into redacted draft chunks
sent through a `DraftStream`, holding back enough tail context that a
pattern split across two `push()` calls is never exposed intact, and never
letting the `INSUFFICIENT_EVIDENCE` abstention sentinel reach Redis."""

from __future__ import annotations

import json
import re

import pytest

from tap.entrypoints.tapper_draft_batcher import DraftBatcher
from tap.modules.chat.application.ports import DraftStreamUnavailable
from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

_PHONE = re.compile(r"\d{11}")


class _FakeDrafts:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[tuple[str, int, int, str, bool]] = []
        self._fail_after = fail_after

    async def append(self, turn_id, *, attempt, seq, text, reset=False):
        if self._fail_after is not None and len(self.calls) >= self._fail_after:
            raise DraftStreamUnavailable("redis unavailable")
        self.calls.append((turn_id, attempt, seq, text, reset))

    async def replay(self, turn_id, *, attempt):  # pragma: no cover - unused here
        raise NotImplementedError

    async def read(self, keys, *, block_ms):  # pragma: no cover - unused here
        raise NotImplementedError

    async def partial_text(self, turn_id):  # pragma: no cover - unused here
        raise NotImplementedError


async def _identity_redact(text: str) -> str:
    return text


async def _phone_redact(text: str) -> str:
    return _PHONE.sub("[REDACTED_PHONE]", text)


def _texts(calls):
    return [call[3] for call in calls]


@pytest.mark.asyncio
async def test_flushes_on_32_chars_or_50ms():
    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=_identity_redact,
        max_delay_s=1000,
        max_chars=32,
        holdback_chars=0,
    )

    await batcher.push("short")
    assert drafts.calls == []

    await batcher.push("x" * 40)
    assert drafts.calls != []
    assert "".join(_texts(drafts.calls)) == "short" + "x" * 40


@pytest.mark.asyncio
async def test_flushes_after_max_delay_elapses():
    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=_identity_redact,
        max_delay_s=0.0,
        max_chars=1000,
        holdback_chars=0,
    )

    await batcher.push("a")
    assert drafts.calls != []


@pytest.mark.asyncio
async def test_redaction_holds_back_patterns_split_across_batches():
    """A phone number split across two `push()` calls must never appear,
    intact, anywhere in what was sent to Redis, even transiently: forcing a
    flush after each push (via `max_chars=1`) still never exposes it,
    because every flush re-redacts the *whole* accumulated raw text."""
    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=_phone_redact,
        max_delay_s=1000,
        max_chars=1,
        holdback_chars=4,
    )

    await batcher.push("联系 138123")
    await batcher.push("45678 处理")
    final_text = await batcher.close()

    assert "13812345678" not in "".join(_texts(drafts.calls))
    assert "13812345678" not in final_text
    assert "[REDACTED_PHONE]" in final_text


@pytest.mark.asyncio
async def test_degrades_silently_when_drafts_unavailable():
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=None,
        redact=_identity_redact,
        max_delay_s=0.0,
        max_chars=1,
    )

    await batcher.push("hello")
    final_text = await batcher.close()

    assert final_text == "hello"


@pytest.mark.asyncio
async def test_degrades_after_draft_stream_unavailable_mid_stream():
    drafts = _FakeDrafts(fail_after=0)
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=_identity_redact,
        max_delay_s=1000,
        max_chars=1,
        holdback_chars=0,
    )

    await batcher.push("hello")
    await batcher.push("world")
    final_text = await batcher.close()

    assert drafts.calls == []
    assert final_text == "helloworld"


@pytest.mark.asyncio
async def test_seq_is_monotonic_per_attempt():
    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=3,
        drafts=drafts,
        redact=_identity_redact,
        max_delay_s=1000,
        max_chars=1,
        holdback_chars=0,
    )

    await batcher.push("a")
    await batcher.push("b")
    await batcher.push("c")
    await batcher.close()

    seqs = [call[2] for call in drafts.calls]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert all(call[1] == 3 for call in drafts.calls)


@pytest.mark.asyncio
async def test_insufficient_evidence_sentinel_never_reaches_drafts():
    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=_identity_redact,
        max_delay_s=1000,
        max_chars=1,
        holdback_chars=0,
    )

    await batcher.push("INSUFFICIENT_EV")
    await batcher.push("IDENCE")
    final_text = await batcher.close()

    assert all("INSUFFICIENT_EVIDENCE" not in text for text in _texts(drafts.calls))
    assert final_text == ""


@pytest.mark.parametrize(
    "half_written",
    [
        "联系方式 password:",
        'the token is password="hun',
        "key block -----BEGIN PRIVATE KEY----- abc",
    ],
    ids=["trailing-assignment", "unterminated-quote", "unterminated-private-key"],
)
@pytest.mark.asyncio
async def test_redaction_unavailable_mid_stream_sends_nothing_and_keeps_position(half_written):
    """M-Important: a half-written credential at a flush boundary must never
    fail the Turn. A non-final flush that cannot redact sends nothing and
    keeps the current sent position -- it is retried (and may succeed) once
    more raw text arrives."""
    redactor = PatternEgressRedactor(personal_data=True)
    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=redactor.redact_text,
        max_delay_s=1000,
        max_chars=1,
        holdback_chars=0,
    )

    await batcher.push(half_written)  # must not raise

    assert drafts.calls == []
    assert all(half_written not in text for text in _texts(drafts.calls))


@pytest.mark.parametrize(
    "half_written",
    [
        "联系方式 password:",
        'the token is password="hun',
        "key block -----BEGIN PRIVATE KEY----- abc",
    ],
    ids=["trailing-assignment", "unterminated-quote", "unterminated-private-key"],
)
@pytest.mark.asyncio
async def test_redaction_unavailable_at_close_degrades_without_raising(half_written):
    """M-Important: if the credential never completes (end of the Turn's
    output), `close()` must not raise either -- it marks the span degraded
    and sends nothing further; `finalize`'s fail-closed redaction remains
    the authoritative decision for the saved/returned answer."""
    redactor = PatternEgressRedactor(personal_data=True)
    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=redactor.redact_text,
        max_delay_s=1000,
        max_chars=1000,
        holdback_chars=0,
    )

    await batcher.push(half_written)
    final_text = await batcher.close()  # must not raise

    assert drafts.calls == []
    assert half_written not in final_text
    assert batcher._degraded is True
    assert batcher._redaction_unavailable is True


@pytest.mark.asyncio
async def test_holdback_covers_an_unterminated_email_local_part_longer_than_holdback():
    """M-1: an email's local part has no fixed length, so a trailing run of
    characters the `EMAIL` pattern's local part accepts must never be sent
    while it is still growing, even once it exceeds `holdback_chars` -- only
    `close()` (no more text can ever complete it) may release it."""

    async def _email_redact(text: str) -> str:
        return await PatternEgressRedactor().redact_text(text)

    drafts = _FakeDrafts()
    batcher = DraftBatcher(
        turn_id="turn-1",
        attempt=1,
        drafts=drafts,
        redact=_email_redact,
        max_delay_s=1000,
        max_chars=1,
        holdback_chars=48,
    )

    for _ in range(80):
        await batcher.push("a")
    sent_before_at = "".join(_texts(drafts.calls))
    assert "a" * 60 not in sent_before_at

    await batcher.push("@example.com")
    final_text = await batcher.close()

    replayed = ""
    for call in drafts.calls:
        if call[4]:
            replayed = ""
        replayed += call[3]
    assert "a" * 80 not in replayed
    assert "[REDACTED_EMAIL]" in replayed
    assert "[REDACTED_EMAIL]" in final_text


@pytest.mark.asyncio
async def test_close_records_draft_redaction_hit_counts_on_the_current_span(span_recorder):
    """M-8 (partial): the batcher's own last redacted draft's placeholder
    counts land on the current span at `close()`, matching the style
    `KnowledgeHttpService._redact_answer_response` uses for the final
    answer (`tap.guard.redaction.output_hits`)."""
    from tap.platform.telemetry.tracing import span

    drafts = _FakeDrafts()
    with span("test.draft_batcher_hits") as current:
        batcher = DraftBatcher(
            turn_id="turn-1",
            attempt=1,
            drafts=drafts,
            redact=_phone_redact,
            max_delay_s=1000,
            max_chars=1000,
            holdback_chars=0,
        )
        await batcher.push("电话 13812345678 结束")
        await batcher.close()
        span_id = current.get_span_context().span_id

    finished = next(s for s in span_recorder.get_finished_spans() if s.context.span_id == span_id)
    hits = json.loads(finished.attributes["tap.guard.redaction.draft_hits"])
    assert hits == {"phone": 1}


@pytest.mark.asyncio
async def test_close_records_nothing_when_no_redaction_hits(span_recorder):
    from tap.platform.telemetry.tracing import span

    drafts = _FakeDrafts()
    with span("test.draft_batcher_no_hits") as current:
        batcher = DraftBatcher(
            turn_id="turn-1",
            attempt=1,
            drafts=drafts,
            redact=_identity_redact,
            max_delay_s=1000,
            max_chars=1000,
            holdback_chars=0,
        )
        await batcher.push("hello world")
        await batcher.close()
        span_id = current.get_span_context().span_id

    finished = next(s for s in span_recorder.get_finished_spans() if s.context.span_id == span_id)
    assert "tap.guard.redaction.draft_hits" not in finished.attributes
