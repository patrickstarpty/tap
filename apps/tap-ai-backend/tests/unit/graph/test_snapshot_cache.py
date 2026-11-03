from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from tap.modules.graph.application.cache import CachedSnapshotIndexes
from tap.modules.graph.domain.index import SnapshotIndex
from tests.unit.graph.test_snapshot_index import handcrafted_draft

_DRAFT = handcrafted_draft()
_BASE = SnapshotIndex.build(replace(_DRAFT, snapshot=replace(_DRAFT.snapshot, status="READY")))
_SIZE = _BASE.size  # 31 entries


class _Loader:
    def __init__(self, index: SnapshotIndex = _BASE, *, delay: float = 0.0) -> None:
        self.calls = 0
        self._index = index
        self._delay = delay

    async def __call__(self) -> SnapshotIndex:
        self.calls += 1
        await asyncio.sleep(self._delay)
        return self._index


@pytest.mark.asyncio
async def test_hit_does_not_call_loader_again():
    cache = CachedSnapshotIndexes()
    loader = _Loader()
    first = await cache.get_or_load(("project-1", "snapshot-1"), loader)
    second = await cache.get_or_load(("project-1", "snapshot-1"), loader)
    assert first is second is _BASE
    assert loader.calls == 1
    assert cache.stats() == {"hits": 1, "misses": 1, "entries": _SIZE, "evictions": 0}


@pytest.mark.asyncio
async def test_project_is_part_of_the_key():
    cache = CachedSnapshotIndexes()
    loader = _Loader()
    await cache.get_or_load(("project-1", "snapshot-1"), loader)
    await cache.get_or_load(("project-2", "snapshot-1"), loader)
    assert loader.calls == 2


@pytest.mark.asyncio
async def test_capacity_evicts_least_recently_used():
    cache = CachedSnapshotIndexes(max_entries=_SIZE * 2)
    loaders = {name: _Loader() for name in ("a", "b", "c")}
    await cache.get_or_load(("p", "a"), loaders["a"])
    await cache.get_or_load(("p", "b"), loaders["b"])
    await cache.get_or_load(("p", "a"), loaders["a"])
    await cache.get_or_load(("p", "c"), loaders["c"])
    assert cache.stats()["evictions"] == 1
    assert cache.stats()["entries"] == _SIZE * 2
    await cache.get_or_load(("p", "a"), loaders["a"])
    await cache.get_or_load(("p", "b"), loaders["b"])
    assert loaders["a"].calls == 1
    assert loaders["b"].calls == 2


@pytest.mark.asyncio
async def test_oversized_index_is_returned_but_not_cached():
    cache = CachedSnapshotIndexes(max_entries=_SIZE - 1)
    loader = _Loader()
    assert await cache.get_or_load(("p", "big"), loader) is _BASE
    assert await cache.get_or_load(("p", "big"), loader) is _BASE
    assert loader.calls == 2
    assert cache.stats()["entries"] == 0
    assert cache.stats()["evictions"] == 0


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_load():
    cache = CachedSnapshotIndexes()
    loader = _Loader(delay=0.01)
    results = await asyncio.gather(*(cache.get_or_load(("p", "s"), loader) for _ in range(10)))
    assert loader.calls == 1
    assert all(result is _BASE for result in results)
    assert cache.stats()["misses"] == 1


@pytest.mark.asyncio
async def test_loader_failure_propagates_to_all_waiters_and_is_not_cached():
    cache = CachedSnapshotIndexes()
    calls = 0

    async def failing() -> SnapshotIndex:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise LookupError("boom")

    results = await asyncio.gather(
        *(cache.get_or_load(("p", "s"), failing) for _ in range(10)), return_exceptions=True
    )
    assert calls == 1
    assert all(isinstance(result, LookupError) for result in results)
    assert cache.stats()["entries"] == 0
    loader = _Loader()
    assert await cache.get_or_load(("p", "s"), loader) is _BASE
    assert loader.calls == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_the_shared_load():
    cache = CachedSnapshotIndexes()
    loader = _Loader(delay=0.02)
    owner = asyncio.ensure_future(cache.get_or_load(("p", "s"), loader))
    await asyncio.sleep(0)
    waiter = asyncio.ensure_future(cache.get_or_load(("p", "s"), loader))
    await asyncio.sleep(0)
    waiter.cancel()
    assert await owner is _BASE
    assert loader.calls == 1


@pytest.mark.asyncio
async def test_non_ready_snapshot_is_rejected():
    cache = CachedSnapshotIndexes()
    candidate = SnapshotIndex.build(_DRAFT)
    assert candidate.snapshot.status == "CANDIDATE"
    loader = _Loader(candidate)
    assert await cache.get_or_load(("p", "s"), loader) is candidate
    await cache.get_or_load(("p", "s"), loader)
    assert loader.calls == 2
    assert cache.stats()["entries"] == 0


@pytest.mark.asyncio
async def test_load_emits_span_with_hit_and_entry_attributes(span_recorder):
    cache = CachedSnapshotIndexes()
    await cache.get_or_load(("p", "snapshot-x"), _Loader())
    await cache.get_or_load(("p", "snapshot-x"), _Loader())
    spans = [s for s in span_recorder.get_finished_spans() if s.name == "graph.index.load"]
    assert [s.attributes["tap.graph.cache_hit"] for s in spans] == [False, True]
    assert all(s.attributes["tap.graph.snapshot_id"] == "snapshot-x" for s in spans)
    assert all(s.attributes["tap.graph.entries"] == _SIZE for s in spans)
