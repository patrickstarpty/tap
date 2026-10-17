"""`DraftBatcher` batches raw model text deltas into redacted draft chunks
sent through a `DraftStream`, holding back enough tail context that a
pattern split across two `push()` calls is never exposed intact, and never
letting the `INSUFFICIENT_EVIDENCE` abstention sentinel reach Redis."""

from __future__ import annotations

import re

import pytest

from tap.entrypoints.tapper_draft_batcher import DraftBatcher
from tap.modules.chat.application.ports import DraftStreamUnavailable

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
