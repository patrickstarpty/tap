"""Bounded optional hint recovery; durable MySQL work remains authoritative."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol, cast

from redis.exceptions import RedisError

from tap.modules.access.domain.context import ProjectScopeContext
from tap.platform.db.project_scope import require_project_scope
from tap.platform.messaging.redis_dispatch import AsyncRedis, DispatchWakeup

# Inspect every group's delivered boundary and pending minimum in the same Lua
# invocation as deletion. No concurrent XREADGROUP/XGROUP can race the decision.
_TRIM_ACKNOWLEDGED = """
local function less(a, b)
  local am, as = string.match(a, '^(%d+)%-(%d+)$')
  local bm, bs = string.match(b, '^(%d+)%-(%d+)$')
  if #am ~= #bm then return #am < #bm end
  if am ~= bm then return am < bm end
  if #as ~= #bs then return #as < #bs end
  return as < bs
end
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local excess = redis.call('XLEN', KEYS[1]) - tonumber(ARGV[1])
if excess <= 0 then return 0 end
local groups = redis.call('XINFO', 'GROUPS', KEYS[1])
if #groups == 0 then return 0 end
local boundaries = {}
for _, group in ipairs(groups) do
  local name, delivered
  for i=1,#group,2 do
    if group[i] == 'name' then name = group[i+1] end
    if group[i] == 'last-delivered-id' then delivered = group[i+1] end
  end
  if not name or not delivered then return 0 end
  local pending = redis.call('XPENDING', KEYS[1], name)
  table.insert(boundaries, {delivered, pending[2]})
end
local entries = redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', math.min(excess, 500))
local removable = {}
for _, entry in ipairs(entries) do
  local id = entry[1]
  for _, boundary in ipairs(boundaries) do
    if less(boundary[1], id) or (boundary[2] and not less(id, boundary[2])) then
      if #removable == 0 then return 0 end
      return redis.call('XDEL', KEYS[1], unpack(removable))
    end
  end
  table.insert(removable, id)
end
if #removable == 0 then return 0 end
return redis.call('XDEL', KEYS[1], unpack(removable))
""".strip()


class RecoveryRedis(Protocol):
    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str,
        *,
        count: int,
    ) -> object: ...

    async def xack(self, name: str, groupname: str, *ids: str) -> object: ...

    async def eval(self, script: str, number_of_keys: int, *values: object) -> int: ...


class RedisStreamRecovery:
    def __init__(
        self,
        *,
        redis: RecoveryRedis,
        scope: ProjectScopeContext,
        stream_name: str,
        aggregate_type: str,
    ) -> None:
        self._scope = require_project_scope(scope)
        if not stream_name.strip() or not aggregate_type.strip():
            raise ValueError("stream and aggregate type must be nonblank")
        self._redis = redis
        self._stream_name = stream_name
        self._aggregate_type = aggregate_type
        self._cursors: dict[str, str] = {}

    async def reclaim_pending(
        self, group: str, consumer: str, idle_for: timedelta, limit: int
    ) -> list[DispatchWakeup]:
        from tap.platform.messaging.redis_wakeup import _decode_wakeup

        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if not group.strip() or not consumer.strip() or idle_for.total_seconds() <= 0:
            raise ValueError("group, consumer and positive idle duration required")
        result: list[DispatchWakeup] = []
        try:
            response = await self._redis.xautoclaim(
                self._stream_name,
                group,
                consumer,
                max(1, int(idle_for.total_seconds() * 1000)),
                self._cursors.get(group, "0-0"),
                count=limit,
            )
            if not isinstance(response, (list, tuple)) or len(response) < 2:
                return []
            cursor, messages = response[:2]
            if not isinstance(cursor, str) or not isinstance(messages, list):
                return []
            self._cursors[group] = cursor
            for item in messages[:limit]:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                stream_id, fields = item
                if not isinstance(stream_id, str) or not isinstance(fields, dict):
                    continue
                wakeup = _decode_wakeup(
                    stream_id,
                    cast(dict[str, object], fields),
                    aggregate_type=self._aggregate_type,
                    scope=self._scope,
                )
                if wakeup is None:
                    await self._redis.xack(self._stream_name, group, stream_id)
                else:
                    result.append(wakeup)
        except RedisError:
            return result
        return result

    async def trim_acknowledged(self, max_length: int) -> int:
        return await trim_acknowledged(self._redis, self._stream_name, max_length)


async def trim_acknowledged(redis: AsyncRedis, stream_name: str, max_length: int) -> int:
    if type(max_length) is not int or not 0 <= max_length <= 1_000_000:
        raise ValueError("max_length must be between 0 and 1000000")
    try:
        return await redis.eval(_TRIM_ACKNOWLEDGED, 1, stream_name, max_length)
    except RedisError:
        return 0
