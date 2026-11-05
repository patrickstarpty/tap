"""Runtime (Langfuse-backed) metrics status and degradation helpers: the
status vocabulary, the backend-unavailable error, one view's shared deadline
and structured concurrent gathering (Agent team view spec section 5.1)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Literal

RuntimeStatus = Literal["ok", "syncing", "unavailable"]


class RuntimeMetricsUnavailable(Exception):
    """The runtime metrics backend (Langfuse) could not answer the query."""


# Spec 5.1: Langfuse unavailable/stale ranks worse than merely behind, which
# ranks worse than caught up -- combining two statuses takes the worse one.
_STATUS_RANK: dict[RuntimeStatus, int] = {"unavailable": 0, "syncing": 1, "ok": 2}


def combine_status(a: RuntimeStatus, b: RuntimeStatus) -> RuntimeStatus:
    return a if _STATUS_RANK[a] <= _STATUS_RANK[b] else b


def view_deadline(seconds: float) -> Callable[[], float]:
    """Starts one view's shared runtime budget; returns how much is left."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds

    def remaining() -> float:
        return max(deadline - loop.time(), 0.0)

    return remaining


async def gather_runtime(*awaitables: Coroutine[Any, Any, Any]) -> list[Any]:
    """Runs one view's runtime queries concurrently; the first failure
    cancels the rest and is re-raised bare (not as an `ExceptionGroup`)."""
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(awaitable) for awaitable in awaitables]
    except ExceptionGroup as group_error:
        raise group_error.exceptions[0] from None
    return [task.result() for task in tasks]
