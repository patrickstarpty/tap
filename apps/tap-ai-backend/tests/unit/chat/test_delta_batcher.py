"""DeltaBatcher coalesces answer text into bounded `answer.delta` writes."""

from __future__ import annotations

import pytest

from tap.entrypoints.tapper_generation_worker import DeltaBatcher


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _batcher(**kwargs) -> tuple[DeltaBatcher, list[str], _Clock]:
    flushed: list[str] = []
    clock = _Clock()

    async def flush(text: str) -> None:
        flushed.append(text)

    return DeltaBatcher(flush, clock=clock, **kwargs), flushed, clock


@pytest.mark.asyncio
async def test_flushes_once_the_character_threshold_is_reached():
    batcher, flushed, _clock = _batcher(max_chars=5)

    await batcher.add("abc")
    assert flushed == []
    await batcher.add("defg")

    assert flushed == ["abcdefg"]
    await batcher.close()
    assert flushed == ["abcdefg"]


@pytest.mark.asyncio
async def test_flushes_once_the_time_threshold_elapses():
    batcher, flushed, clock = _batcher(max_chars=200, max_interval=0.15)

    await batcher.add("first")
    assert flushed == []
    clock.now = 0.1
    await batcher.add(" second")
    assert flushed == []
    clock.now = 0.16
    await batcher.add(" third")

    assert flushed == ["first second third"]


@pytest.mark.asyncio
async def test_interval_restarts_after_each_flush():
    batcher, flushed, clock = _batcher(max_chars=200, max_interval=0.15)

    await batcher.add("a")
    clock.now = 0.2
    await batcher.add("b")
    assert flushed == ["ab"]
    clock.now = 0.3
    await batcher.add("c")
    assert flushed == ["ab"]
    clock.now = 0.36
    await batcher.add("d")
    assert flushed == ["ab", "cd"]


@pytest.mark.asyncio
async def test_close_flushes_the_remainder_once():
    batcher, flushed, _clock = _batcher()

    await batcher.add("tail")
    await batcher.close()
    await batcher.close()

    assert flushed == ["tail"]


@pytest.mark.asyncio
async def test_empty_text_never_flushes():
    batcher, flushed, clock = _batcher(max_chars=1)

    await batcher.add("")
    clock.now = 10
    await batcher.add("")
    await batcher.close()

    assert flushed == []


@pytest.mark.asyncio
async def test_lone_surrogates_are_replaced_before_flush():
    batcher, flushed, _clock = _batcher()

    await batcher.add("a\ud800b")
    await batcher.add("\udc00c")
    await batcher.close()

    assert flushed == ["a\ufffdb\ufffdc"]
    flushed[0].encode("utf-8")


def test_rejects_invalid_thresholds():
    async def flush(_text: str) -> None:
        return None

    with pytest.raises(ValueError):
        DeltaBatcher(flush, max_chars=0)
    with pytest.raises(ValueError):
        DeltaBatcher(flush, max_interval=0)
