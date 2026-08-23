from __future__ import annotations

import asyncio

import pytest

from tap.entrypoints import relay_reconciler


@pytest.mark.parametrize(
    "environment",
    [
        {"TAP_RELAY_BATCH_SIZE": "0"},
        {"TAP_RELAY_BATCH_SIZE": "-1"},
        {"TAP_RELAY_BATCH_SIZE": "501"},
        {"TAP_RELAY_POLL_SECONDS": "0"},
        {"TAP_RELAY_POLL_SECONDS": "-1"},
        {"TAP_RELAY_POLL_SECONDS": "61"},
        {"TAP_RELAY_POLL_SECONDS": "nan"},
    ],
)
def test_relay_settings_reject_unbounded_or_non_positive_values(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        relay_reconciler.load_settings(environment)


def test_redis_client_has_finite_pool_and_operation_timeouts() -> None:
    client = relay_reconciler.create_redis_client("redis://127.0.0.1:6379/0")
    try:
        pool = client.connection_pool
        assert pool.max_connections == 20
        assert pool.connection_kwargs["socket_connect_timeout"] == 5.0
        assert pool.connection_kwargs["socket_timeout"] == 5.0
        assert pool.connection_kwargs["socket_keepalive"] is True
        assert pool.connection_kwargs["health_check_interval"] == 30
    finally:
        asyncio.run(client.aclose())
