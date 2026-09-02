from __future__ import annotations

import asyncio
import shutil
import signal

import pytest

from tap.entrypoints import relay_reconciler


def _athena_environment(**overrides: str) -> dict[str, str]:
    values = {
        "ATHENA_API_HOST": "127.0.0.1",
        "ATHENA_WEB_HOST": "127.0.0.1",
        "ATHENA_POLL_SECONDS": "0.01",
        "ATHENA_JOB_BATCH_SIZE": "10",
        "ATHENA_WORKER_ID": "relay-e2e-worker",
        "TAP_ATHENA_COMPOSE_PROJECT": "tap-athena-e2e",
        "TAP_DATABASE_URL": ("mysql+asyncmy://tap:test@127.0.0.1:13306/tap?charset=utf8mb4"),
        "TAP_ALEMBIC_DATABASE_URL": (
            "mysql+pymysql://tap:test@127.0.0.1:13306/tap?charset=utf8mb4"
        ),
        "TAP_REDIS_URL": "redis://127.0.0.1:16379/0",
        "TAP_REDIS_COMMAND_STREAM": "tap-athena-e2e:commands",
        "LITELLM_BASE_URL": "http://127.0.0.1:14000",
        "LITELLM_MODEL": "openai/test-chat",
        "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        "MILVUS_URI": "http://127.0.0.1:29530",
    }
    values.update(overrides)
    return values


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
        relay_reconciler.load_settings(_athena_environment(**environment))


def test_relay_settings_derive_provider_and_identity_values_from_athena_snapshot() -> None:
    settings = relay_reconciler.load_settings(
        _athena_environment(
            TAP_RELAY_BATCH_SIZE="37",
            TAP_RELAY_POLL_SECONDS="0.25",
            TAP_RELAY_WORKER_ID="obsolete-second-authority",
        )
    )

    assert settings.database_url.endswith("127.0.0.1:13306/tap?charset=utf8mb4")
    assert settings.redis_url == "redis://127.0.0.1:16379/0"
    assert settings.stream_name == "tap-athena-e2e:commands"
    assert settings.worker_id == "relay-e2e-worker"
    assert settings.batch_size == 37
    assert settings.poll_seconds == 0.25
    assert "tap:test" not in repr(settings)
    assert "13306" not in repr(settings)
    assert "16379" not in repr(settings)


def test_relay_parses_codex_selection_without_discovery(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def forbidden_discovery(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("relay performed Codex discovery")

    monkeypatch.setattr(shutil, "which", forbidden_discovery)

    settings = relay_reconciler.load_settings(_athena_environment(ATHENA_ANSWER_BACKEND="codex"))

    assert settings.worker_id == "relay-e2e-worker"


def test_relay_invalid_provider_settings_fail_before_any_constructor(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    called = False

    def forbidden(_settings):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True

    monkeypatch.setattr(relay_reconciler, "_open_database", forbidden, raising=False)

    with pytest.raises(ValueError, match="TAP_DATABASE_URL"):
        relay_reconciler.main(
            _athena_environment(
                TAP_DATABASE_URL=(
                    "mysql+asyncmy://tap:test@remote.example:3306/tap?charset=utf8mb4"
                )
            )
        )

    assert called is False


def test_relay_cli_redacts_runtime_primary_after_internal_cleanup(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []

    async def fail_after_cleanup(**_kwargs: object) -> None:
        events.extend(["close:redis", "close:engine"])
        raise RuntimeError("relay-provider-secret-primary")

    monkeypatch.setattr(relay_reconciler, "run", fail_after_cleanup)

    result = relay_reconciler.cli(_athena_environment())
    output = capsys.readouterr()

    assert result == 1
    assert events == ["close:redis", "close:engine"]
    assert output.out == ""
    assert output.err == "Athena relay failed; check local provider configuration.\n"
    assert "provider-secret" not in output.err
    assert "Traceback" not in output.err


def test_redis_client_has_finite_pool_and_operation_timeouts() -> None:
    client = relay_reconciler.create_redis_client("redis://127.0.0.1:16379/0")
    try:
        pool = client.connection_pool
        assert pool.max_connections == 20
        assert pool.connection_kwargs["socket_connect_timeout"] == 5.0
        assert pool.connection_kwargs["socket_timeout"] == 5.0
        assert pool.connection_kwargs["socket_keepalive"] is True
        assert pool.connection_kwargs["health_check_interval"] == 30
    finally:
        asyncio.run(client.aclose())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["redis", "relay"])
async def test_relay_partial_construction_closes_every_prior_owner(
    monkeypatch,
    failure_point: str,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    primary = RuntimeError(f"{failure_point}-construction-failed")

    class Engine:
        async def dispose(self) -> None:
            events.append("engine")

    class Redis:
        async def aclose(self) -> None:
            events.append("redis")

    monkeypatch.setattr(
        relay_reconciler,
        "_open_database",
        lambda _settings: (Engine(), object()),
        raising=False,
    )

    if failure_point == "redis":
        monkeypatch.setattr(
            relay_reconciler,
            "create_redis_client",
            lambda _url: (_ for _ in ()).throw(primary),
        )
    else:
        monkeypatch.setattr(relay_reconciler, "create_redis_client", lambda _url: Redis())
        monkeypatch.setattr(
            relay_reconciler,
            "_build_relay",
            lambda *_args: (_ for _ in ()).throw(primary),
            raising=False,
        )

    with pytest.raises(RuntimeError) as captured:
        await relay_reconciler.run(settings=relay_reconciler.load_settings(_athena_environment()))

    assert captured.value is primary
    assert events == (["engine"] if failure_point == "redis" else ["redis", "engine"])


@pytest.mark.asyncio
async def test_relay_cancellation_and_close_failures_settle_all_without_masking(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    primary = asyncio.CancelledError("relay-cancelled")

    class Engine:
        async def dispose(self) -> None:
            events.append("engine")

    class Redis:
        async def aclose(self) -> None:
            events.append("redis")
            raise RuntimeError("redis-close-failed")

    class Relay:
        async def publish_pending(self, _limit: int) -> int:
            events.append("publish")
            raise primary

    monkeypatch.setattr(
        relay_reconciler,
        "_open_database",
        lambda _settings: (Engine(), object()),
        raising=False,
    )
    monkeypatch.setattr(relay_reconciler, "create_redis_client", lambda _url: Redis())
    monkeypatch.setattr(
        relay_reconciler,
        "_build_relay",
        lambda *_args: Relay(),
        raising=False,
    )

    with pytest.raises(BaseExceptionGroup) as captured:
        await relay_reconciler.run(
            settings=relay_reconciler.load_settings(_athena_environment()),
            signal_installer=lambda _stop: None,
        )

    assert events == ["publish", "redis", "engine"]
    assert captured.value.exceptions[0] is primary
    assert str(captured.value.exceptions[1]) == "redis-close-failed"


@pytest.mark.asyncio
async def test_relay_signal_install_failure_closes_complete_runtime(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    primary = RuntimeError("signal-install-failed")

    class Engine:
        async def dispose(self) -> None:
            events.append("engine")

    class Redis:
        async def aclose(self) -> None:
            events.append("redis")

    monkeypatch.setattr(
        relay_reconciler,
        "_open_database",
        lambda _settings: (Engine(), object()),
    )
    monkeypatch.setattr(relay_reconciler, "create_redis_client", lambda _url: Redis())
    monkeypatch.setattr(relay_reconciler, "_build_relay", lambda *_args: object())

    def fail_install(_stop: asyncio.Event):
        raise primary

    with pytest.raises(RuntimeError) as captured:
        await relay_reconciler.run(
            settings=relay_reconciler.load_settings(_athena_environment()),
            signal_installer=fail_install,
        )

    assert captured.value is primary
    assert events == ["redis", "engine"]


@pytest.mark.asyncio
async def test_relay_remove_failure_still_closes_every_runtime_owner(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            events.append("engine")

    class Redis:
        async def aclose(self) -> None:
            events.append("redis")

    class Relay:
        async def publish_pending(self, _limit: int) -> int:
            events.append("publish")
            return 1

    monkeypatch.setattr(
        relay_reconciler,
        "_open_database",
        lambda _settings: (Engine(), object()),
    )
    monkeypatch.setattr(relay_reconciler, "create_redis_client", lambda _url: Redis())
    monkeypatch.setattr(relay_reconciler, "_build_relay", lambda *_args: Relay())

    def install(_stop: asyncio.Event):
        def remove() -> None:
            events.append("remove")
            raise ExceptionGroup(
                "remove failed",
                [RuntimeError("remove-term"), RuntimeError("remove-int")],
            )

        return remove

    with pytest.raises(ExceptionGroup) as captured:
        await relay_reconciler.run(
            settings=relay_reconciler.load_settings(_athena_environment()),
            signal_installer=install,
            max_iterations=1,
        )

    assert events == ["publish", "remove", "redis", "engine"]
    assert tuple(str(error) for error in captured.value.exceptions) == (
        "remove-term",
        "remove-int",
    )


def test_relay_signal_install_rolls_back_and_normal_remove_attempts_both(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []

    class Loop:
        def __init__(self, *, fail_add: bool) -> None:
            self.fail_add = fail_add

        def add_signal_handler(self, signum, callback) -> None:  # type: ignore[no-untyped-def]
            del callback
            events.append(f"add:{signum.name}")
            if self.fail_add and signum is signal.SIGTERM:
                raise RuntimeError("signal-install-failed")

        def remove_signal_handler(self, signum) -> bool:  # type: ignore[no-untyped-def]
            events.append(f"remove:{signum.name}")
            if not self.fail_add:
                raise RuntimeError(f"remove-failed:{signum.name}")
            return True

    monkeypatch.setattr(relay_reconciler.asyncio, "get_running_loop", lambda: Loop(fail_add=True))
    with pytest.raises(RuntimeError, match="signal-install-failed"):
        relay_reconciler.install_signal_handlers(asyncio.Event())
    assert events == ["add:SIGINT", "add:SIGTERM", "remove:SIGINT"]

    events.clear()
    monkeypatch.setattr(
        relay_reconciler.asyncio,
        "get_running_loop",
        lambda: Loop(fail_add=False),
    )
    remove = relay_reconciler.install_signal_handlers(asyncio.Event())
    with pytest.raises(ExceptionGroup) as captured:
        remove()
    assert events == [
        "add:SIGINT",
        "add:SIGTERM",
        "remove:SIGTERM",
        "remove:SIGINT",
    ]
    assert tuple(str(error) for error in captured.value.exceptions) == (
        "remove-failed:SIGTERM",
        "remove-failed:SIGINT",
    )


@pytest.mark.asyncio
async def test_relay_signal_stop_removes_handlers_and_closes_reverse(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            events.append("engine")

    class Redis:
        async def aclose(self) -> None:
            events.append("redis")

    class Relay:
        async def publish_pending(self, _limit: int) -> int:
            events.append("unexpected-publish")
            return 0

    def install(stop: asyncio.Event):
        events.append("install")
        stop.set()

        def remove() -> None:
            events.append("remove")

        return remove

    monkeypatch.setattr(
        relay_reconciler,
        "_open_database",
        lambda _settings: (Engine(), object()),
        raising=False,
    )
    monkeypatch.setattr(relay_reconciler, "create_redis_client", lambda _url: Redis())
    monkeypatch.setattr(
        relay_reconciler,
        "_build_relay",
        lambda *_args: Relay(),
        raising=False,
    )

    await relay_reconciler.run(
        settings=relay_reconciler.load_settings(_athena_environment()),
        signal_installer=install,
    )

    assert events == ["install", "remove", "redis", "engine"]
