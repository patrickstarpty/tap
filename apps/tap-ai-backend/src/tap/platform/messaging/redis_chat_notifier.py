"""Redis Pub/Sub conversation event notifications."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.application.notifier import conversation_channel

_LOGGER = logging.getLogger(__name__)


class RedisConversationEventNotifier:
    """Publishes on `tap:chat:{project_id}:{chat_id}`; waits on an independent subscription.

    A failed wait returns False so the reader falls back to reading the database.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def publish(self, scope: ProjectScopeContext, chat_id: str) -> None:
        await self._redis.publish(conversation_channel(scope, chat_id), "1")

    async def wait(self, scope: ProjectScopeContext, chat_id: str, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(conversation_channel(scope, chat_id))
            while (remaining := deadline - loop.time()) > 0:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=remaining
                )
                if message is not None and message.get("type") == "message":
                    return True
            return False
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the reader re-reads the database
            _LOGGER.warning("conversation notification wait failed: %s", type(error).__name__)
            return False
        finally:
            try:
                await pubsub.aclose()
            except Exception as error:  # noqa: BLE001 - cleanup must not mask the result
                _LOGGER.warning(
                    "conversation notification cleanup failed: %s", type(error).__name__
                )
