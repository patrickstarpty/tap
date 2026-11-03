"""Conversation event notifications that wake streaming readers after commits."""

from __future__ import annotations

import asyncio
import threading
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext


def conversation_channel(scope: ProjectScopeContext, chat_id: str) -> str:
    return f"tap:chat:{scope.project_id}:{chat_id}"


class ConversationEventNotifier(Protocol):
    async def publish(self, scope: ProjectScopeContext, chat_id: str) -> None: ...

    async def wait(self, scope: ProjectScopeContext, chat_id: str, timeout: float) -> bool: ...


class InMemoryConversationEventNotifier:
    """Single-process notifier; safe across event loops and threads.

    Waiters are woken with `call_soon_threadsafe`, so a publish from another
    loop (for example a test client portal) still reaches the reader.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiters: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}

    async def publish(self, scope: ProjectScopeContext, chat_id: str) -> None:
        with self._lock:
            waiters = tuple(self._waiters.get(conversation_channel(scope, chat_id), ()))
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                continue

    async def wait(self, scope: ProjectScopeContext, chat_id: str, timeout: float) -> bool:
        channel = conversation_channel(scope, chat_id)
        entry = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._waiters.setdefault(channel, set()).add(entry)
        try:
            async with asyncio.timeout(max(0.0, timeout)):
                await entry[1].wait()
            return True
        except TimeoutError:
            return False
        finally:
            with self._lock:
                waiters = self._waiters.get(channel)
                if waiters is not None:
                    waiters.discard(entry)
                    if not waiters:
                        del self._waiters[channel]
