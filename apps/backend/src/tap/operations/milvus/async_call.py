"""Cancellation-safe adapters for blocking Milvus provider calls."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TerminalOutcome[T]:
    """The terminal result of a task plus cancellation requests seen while settling it."""

    value: T | None
    error: BaseException | None
    cancellations: tuple[asyncio.CancelledError, ...]


async def await_task_terminal[T](
    task: asyncio.Task[T],
    *,
    initial_cancellations: tuple[asyncio.CancelledError, ...] = (),
) -> TerminalOutcome[T]:
    """Await ``task`` to completion without consuming caller cancellation state."""

    cancellations = list(initial_cancellations)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            cancellations.append(cancellation)
        except BaseException:
            # Retrieve the terminal exception exactly once below.
            break
    try:
        return TerminalOutcome(task.result(), None, tuple(cancellations))
    except BaseException as error:
        return TerminalOutcome(None, error, tuple(cancellations))


async def cancellation_safe_bounded_call[T](
    operation: Callable[[], T],
    *,
    timeout_seconds: float,
) -> T:
    """Run a blocking call with a deadline and never abandon its worker thread."""

    worker = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.wait_for(asyncio.shield(worker), timeout_seconds)
    except asyncio.CancelledError as cancellation:
        outcome = await await_task_terminal(
            worker,
            initial_cancellations=(cancellation,),
        )
        raise outcome.cancellations[0]
    except TimeoutError:
        outcome = await await_task_terminal(worker)
        if outcome.cancellations:
            raise outcome.cancellations[0]
        raise
