"""Process-local LRU cache of immutable snapshot indexes with single-flight loads."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from tap.modules.graph.domain.index import SnapshotIndex
from tap.platform.telemetry import span

_LOGGER = logging.getLogger(__name__)

SnapshotKey = tuple[str, str]


class CachedSnapshotIndexes:
    """Cache READY snapshot indexes keyed by ``(project_id, snapshot_id)``.

    Capacity counts index entries (nodes + edges + evidence). Callers keep
    performing scope and authorization checks before every lookup.
    """

    def __init__(self, *, max_entries: int = 2_000_000) -> None:
        if max_entries < 1:
            raise ValueError("graph index cache capacity must be positive")
        self._max_entries = max_entries
        self._indexes: OrderedDict[SnapshotKey, SnapshotIndex] = OrderedDict()
        self._loading: dict[SnapshotKey, asyncio.Future[SnapshotIndex]] = {}
        self._entries = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    async def get_or_load(
        self, key: SnapshotKey, loader: Callable[[], Awaitable[SnapshotIndex]]
    ) -> SnapshotIndex:
        with span("graph.index.load", {"tap.graph.snapshot_id": key[1]}) as current:
            index, hit = await self._get_or_load(key, loader)
            current.set_attribute("tap.graph.cache_hit", hit)
            current.set_attribute("tap.graph.entries", index.size)
            return index

    async def _get_or_load(
        self, key: SnapshotKey, loader: Callable[[], Awaitable[SnapshotIndex]]
    ) -> tuple[SnapshotIndex, bool]:
        while True:
            cached = self._indexes.get(key)
            if cached is not None:
                self._indexes.move_to_end(key)
                self._hits += 1
                return cached, True
            pending = self._loading.get(key)
            if pending is None:
                break
            try:
                index = await asyncio.shield(pending)
            except asyncio.CancelledError:
                if pending.cancelled():
                    continue
                raise
            self._hits += 1
            return index, True
        future: asyncio.Future[SnapshotIndex] = asyncio.get_running_loop().create_future()
        self._loading[key] = future
        self._misses += 1
        try:
            index = await loader()
        except asyncio.CancelledError:
            future.cancel()
            raise
        except BaseException as error:
            future.set_exception(error)
            # Mark retrieved so an unawaited failure never logs as unhandled.
            future.exception()
            raise
        finally:
            del self._loading[key]
        self._store(key, index)
        future.set_result(index)
        return index, False

    def _store(self, key: SnapshotKey, index: SnapshotIndex) -> None:
        if index.snapshot.status != "READY":
            return
        if index.size > self._max_entries:
            _LOGGER.warning(
                "graph snapshot index exceeds cache capacity; serving uncached",
                extra={"snapshot_id": key[1], "entries": index.size},
            )
            return
        self._indexes[key] = index
        self._entries += index.size
        while self._entries > self._max_entries:
            _, evicted = self._indexes.popitem(last=False)
            self._entries -= evicted.size
            self._evictions += 1

    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": self._entries,
            "evictions": self._evictions,
        }
