"""Project binding must fail closed before adapters touch external resources."""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import AnonymousContext, IdentityMode, PlatformScopeContext
from tap.modules.chat.adapters.mysql import MysqlTurnRepository, OutboxStore
from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator
from tap.platform.messaging.redis_dispatch import RedisDispatchPublisher
from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer


@pytest.mark.parametrize("repository", [MysqlTurnRepository, OutboxStore])
def test_repository_rejects_missing_scope_before_connecting(repository) -> None:
    with pytest.raises(TypeError, match="scope"):
        repository(object())


@pytest.mark.parametrize(
    "scope",
    [
        None,
        AnonymousContext(enterprise_id="local"),
        PlatformScopeContext(
            enterprise_id="local", actor_id="admin", identity_mode=IdentityMode.PRODUCT
        ),
    ],
)
@pytest.mark.parametrize("repository", [MysqlTurnRepository, OutboxStore])
def test_repository_rejects_non_project_scope(repository, scope) -> None:
    with pytest.raises(TypeError, match="ProjectScopeContext"):
        repository(object(), scope=scope)


def test_projection_requires_project_scope_before_acquiring_a_connection() -> None:
    engine = create_async_engine("mysql+asyncmy://unused:unused@127.0.0.1:1/unused")
    with pytest.raises(TypeError, match="scope"):
        MysqlProjectionCoordinator(engine)
    with pytest.raises(TypeError, match="ProjectScopeContext"):
        MysqlProjectionCoordinator(engine, scope=None)


@pytest.mark.parametrize(
    "adapter, kwargs",
    [
        (RedisDispatchPublisher, {"dedup_ttl": timedelta(seconds=60)}),
        (
            RedisWakeupConsumer,
            {"group_name": "group", "consumer_name": "consumer", "aggregate_type": "chat_turn"},
        ),
    ],
)
def test_redis_adapters_require_trusted_project_scope(adapter, kwargs) -> None:
    with pytest.raises(TypeError, match="scope"):
        adapter(redis=object(), stream_name="stream", **kwargs)
    with pytest.raises(TypeError, match="ProjectScopeContext"):
        adapter(redis=object(), stream_name="stream", scope=None, **kwargs)


def test_repository_exposes_immutable_scope_binding() -> None:
    repository = MysqlTurnRepository(object(), scope=VALIDATION_SCOPE)
    assert repository.scope is VALIDATION_SCOPE
    with pytest.raises(AttributeError):
        repository.scope = VALIDATION_SCOPE


@pytest.mark.asyncio
async def test_redis_dedup_and_hints_are_project_bound() -> None:
    import json
    from dataclasses import replace

    from tap.modules.chat.application.ports import DispatchMessage
    from tap.modules.chat.domain.models import CommandId
    from tap.platform.messaging.redis_wakeup import _decode_wakeup

    class Redis:
        def __init__(self):
            self.keys = set()
            self.payloads = []

        async def eval(self, script, number_of_keys, *values):
            key, _, _, _, payload = values
            if key in self.keys:
                return 0
            self.keys.add(key)
            self.payloads.append(payload)
            return 1

    redis = Redis()
    other = replace(VALIDATION_SCOPE, project_id="scope-other")
    first_publisher = RedisDispatchPublisher(
        redis=redis, stream_name="stream", dedup_ttl=timedelta(seconds=60), scope=VALIDATION_SCOPE
    )
    other_publisher = RedisDispatchPublisher(
        redis=redis, stream_name="stream", dedup_ttl=timedelta(seconds=60), scope=other
    )
    message = DispatchMessage(
        command_id=CommandId("same"),
        outbox_id="first",
        aggregate_type="chat_turn",
        aggregate_id="turn",
        sequence=None,
        enterprise_id="local",
        project_id="tapper-demo",
    )
    assert await first_publisher.publish_once(message) is True
    assert await first_publisher.publish_once(message) is False
    assert (
        await other_publisher.publish_once(
            replace(message, project_id="scope-other", outbox_id="second")
        )
        is True
    )
    with pytest.raises(ValueError, match="scope"):
        await other_publisher.publish_once(message)
    assert len(redis.payloads) == 2
    first = json.loads(redis.payloads[0])
    assert first["projectId"] == "tapper-demo"
    assert first["enterpriseId"] == "local"
    assert "payload" not in first
    assert (
        _decode_wakeup(
            "1-0",
            {"payload": redis.payloads[0]},
            aggregate_type="chat_turn",
            scope=VALIDATION_SCOPE,
        ).aggregate_id
        == "turn"
    )
    assert (
        _decode_wakeup(
            "1-0", {"payload": redis.payloads[0]}, aggregate_type="chat_turn", scope=other
        )
        is None
    )
    assert (
        _decode_wakeup(
            "1-0",
            {"payload": '{"aggregateType":"chat_turn","aggregateId":"turn"}'},
            aggregate_type="chat_turn",
            scope=other,
        )
        is None
    )
