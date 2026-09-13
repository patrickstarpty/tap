"""Recovery contract with optional disposable Redis for Lua atomicity."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.platform.messaging import redis_wakeup


def _recovery(redis):
    from tap.platform.messaging.redis_recovery import RedisStreamRecovery

    return RedisStreamRecovery(
        redis=redis, scope=VALIDATION_SCOPE, stream_name="recovery", aggregate_type="chat_turn"
    )


def _payload(project="tapper-demo", kind="chat_turn"):
    return {
        "payload": json.dumps(
            {
                "enterpriseId": "local",
                "projectId": project,
                "aggregateType": kind,
                "aggregateId": "turn-1",
            }
        )
    }


def test_recovery_reclaim_filters_scope_and_acknowledges_only_its_group():
    class Client:
        def __init__(self):
            self.acked = []

        async def xautoclaim(self, name, group, consumer, min_idle_time, start_id, *, count):
            assert (name, group, consumer, min_idle_time, count) == (
                "recovery",
                "ours",
                "new",
                1000,
                4,
            )
            return [
                "0-0",
                [
                    ("1-0", _payload()),
                    ("2-0", _payload("other")),
                    ("3-0", {"payload": "legacy"}),
                    ("4-0", _payload(kind="other")),
                ],
                [],
            ]

        async def xack(self, name, group, *ids):
            self.acked.append((name, group, ids))

    async def run():
        client = Client()
        result = await _recovery(client).reclaim_pending("ours", "new", timedelta(seconds=1), 4)
        assert [item.stream_id for item in result] == ["1-0"]
        assert client.acked == [
            ("recovery", "ours", ("2-0",)),
            ("recovery", "ours", ("3-0",)),
            ("recovery", "ours", ("4-0",)),
        ]

    asyncio.run(run())


def test_recovery_redis_outage_remains_an_optional_hint():
    class Client:
        async def xautoclaim(self, *args, **kwargs):
            raise ConnectionError("private credential")

        async def eval(self, *args):
            raise ConnectionError("private credential")

    async def run():
        recovery = _recovery(Client())
        assert await recovery.reclaim_pending("ours", "new", timedelta(seconds=1), 2) == []
        assert await recovery.trim_acknowledged(10) == 0
        for invalid in (0, -1, True, 501):
            with pytest.raises(ValueError):
                await recovery.reclaim_pending("ours", "new", timedelta(seconds=1), invalid)

    asyncio.run(run())


def test_recovery_wait_reclaims_before_waiting_for_new_hints():
    class Client:
        async def xgroup_create(self, *args, **kwargs):
            pass

        async def xautoclaim(self, *args, **kwargs):
            return ["0-0", [("1-0", _payload())], []]

        async def xreadgroup(self, *args, **kwargs):
            return []

    async def run():
        consumer = redis_wakeup.RedisWakeupConsumer(
            redis=Client(),
            scope=VALIDATION_SCOPE,
            stream_name="recovery",
            group_name="ours",
            consumer_name="new",
            aggregate_type="chat_turn",
        )
        item = await consumer.wait(max_wait_seconds=0.01)
        assert item is not None and item.stream_id == "1-0"

    asyncio.run(run())


def _remove_owned_redis(docker, name, env):
    result = subprocess.run(
        [*docker, "rm", "--force", name], capture_output=True, env=env, timeout=20
    )
    if result.returncode:
        raise RuntimeError("owned Redis cleanup failed")


@pytest.fixture
def owned_redis_url():
    if os.getenv("TAP_RUN_REDIS_INTEGRATION") != "1":
        pytest.skip("requires owned disposable Redis")
    from scripts.migration_support import _local_environment, _run

    env = _local_environment()
    context = _run(["docker", "context", "show"], env=env)
    endpoint = _run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ],
        env=env,
    )
    if not endpoint.startswith("unix://"):
        raise ValueError("recovery tests require a local Docker socket")
    docker = ["docker", "--context", context]
    name = "tap-recovery-test-" + uuid4().hex[:12]
    event = {"event": "owned-redis", "identity": name, "state": "started"}
    print(json.dumps(event), file=sys.stderr, flush=True)
    try:
        subprocess.run(
            [
                *docker,
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "-p",
                "127.0.0.1::6379",
                "redis:7.4.7",
            ],
            check=True,
            capture_output=True,
            env=env,
            timeout=60,
        )
        port = (
            subprocess.check_output(
                [*docker, "port", name, "6379/tcp"], text=True, env=env, timeout=10
            )
            .strip()
            .rsplit(":", 1)[1]
        )
        url = f"redis://127.0.0.1:{port}/0"
        client = SyncRedis.from_url(url)
        try:
            for _ in range(100):
                try:
                    if client.ping():
                        break
                except ConnectionError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("owned Redis did not start")
        finally:
            client.close()
        yield url
    finally:
        try:
            _remove_owned_redis(docker, name, env)
            event["state"] = "complete"
        except BaseException:
            event["state"] = "failed"
            raise
        finally:
            print(json.dumps(event), file=sys.stderr, flush=True)


def test_recovery_trim_preserves_pending_and_unread_across_all_groups(owned_redis_url):
    async def run():
        client = Redis.from_url(owned_redis_url, decode_responses=True)
        try:
            for n in range(1, 7):
                await client.xadd("recovery", _payload(), id=f"{n}-0")
            await client.xgroup_create("recovery", "ours", "0")
            await client.xgroup_create("recovery", "foreign", "0")
            await client.xreadgroup("ours", "c", {"recovery": ">"}, count=6)
            await client.xack("recovery", "ours", *[f"{n}-0" for n in range(1, 7)])
            await client.xreadgroup("foreign", "c", {"recovery": ">"}, count=3)
            await client.xack("recovery", "foreign", "1-0", "2-0")
            recovery = _recovery(client)
            assert await recovery.trim_acknowledged(1) == 2
            assert [row[0] for row in await client.xrange("recovery")] == [
                "3-0",
                "4-0",
                "5-0",
                "6-0",
            ]
            await client.xack("recovery", "foreign", "3-0")
            assert await recovery.trim_acknowledged(1) == 1
            assert [row[0] for row in await client.xrange("recovery")] == ["4-0", "5-0", "6-0"]
            await client.xreadgroup("foreign", "c", {"recovery": ">"}, count=3)
            await client.xack("recovery", "foreign", "4-0", "5-0", "6-0")
            assert await recovery.trim_acknowledged(1) == 2
            assert await recovery.trim_acknowledged(1) == 0
        finally:
            await client.aclose()

    asyncio.run(run())


def test_recovery_real_reclaim_moves_only_expired_pending_hints(owned_redis_url):
    async def run():
        client = Redis.from_url(owned_redis_url, decode_responses=True)
        try:
            await client.xadd("recovery", _payload(), id="1-0")
            await client.xadd("recovery", _payload("foreign"), id="2-0")
            await client.xgroup_create("recovery", "ours", "0")
            await client.xgroup_create("recovery", "foreign", "0")
            await client.xreadgroup("ours", "old", {"recovery": ">"}, count=2)
            await client.xreadgroup("foreign", "other", {"recovery": ">"}, count=2)
            recovery = _recovery(client)
            assert await recovery.reclaim_pending("ours", "new", timedelta(hours=1), 2) == []
            await client.xclaim("recovery", "ours", "old", 0, ["1-0", "2-0"], idle=2000)
            hints = await recovery.reclaim_pending("ours", "new", timedelta(seconds=1), 2)
            assert [hint.stream_id for hint in hints] == ["1-0"]
            assert (await client.xpending("recovery", "ours"))["pending"] == 1
            assert (await client.xpending("recovery", "foreign"))["pending"] == 2
            assert await recovery.trim_acknowledged(1) == 0
        finally:
            await client.aclose()

    asyncio.run(run())


def test_recovery_publisher_frees_acknowledged_capacity_without_dropping_pending(owned_redis_url):
    from tap.modules.chat.application.ports import DispatchMessage
    from tap.modules.chat.domain.models import CommandId
    from tap.platform.messaging.redis_dispatch import RedisDispatchPublisher, StreamCapacityExceeded

    async def run():
        client = Redis.from_url(owned_redis_url, decode_responses=True)
        try:
            await client.xadd("recovery", _payload(), id="1-0")
            await client.xgroup_create("recovery", "ours", "0")
            await client.xreadgroup("ours", "old", {"recovery": ">"}, count=1)
            publisher = RedisDispatchPublisher(
                redis=client,
                scope=VALIDATION_SCOPE,
                stream_name="recovery",
                dedup_ttl=timedelta(minutes=5),
                max_stream_length=1,
            )
            message = DispatchMessage(
                enterprise_id="local",
                project_id="tapper-demo",
                command_id=CommandId("fresh"),
                outbox_id="fresh",
                aggregate_type="chat_turn",
                aggregate_id="turn-2",
                sequence=None,
            )
            with pytest.raises(StreamCapacityExceeded):
                await publisher.publish_once(message)
            assert await client.xlen("recovery") == 1
            await client.xack("recovery", "ours", "1-0")
            assert await publisher.publish_once(message) is True
            assert await publisher.publish_once(message) is False
            rows = await client.xrange("recovery")
            assert len(rows) == 1 and json.loads(rows[0][1]["payload"])["commandId"] == "fresh"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_owned_redis_cleanup_failure_is_not_silently_successful(monkeypatch):
    from apps.backend.tests.unit.operations import test_redis_stream_recovery as current

    def failed_remove(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, b"", b"private daemon details")

    monkeypatch.setattr(subprocess, "run", failed_remove)
    with pytest.raises(RuntimeError, match="owned Redis cleanup failed"):
        current._remove_owned_redis(
            ["docker", "--context", "local"], "tap-recovery-test-0123456789ab", {}
        )
