from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from redis import Redis as SyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from tap.platform.telemetry import TraceBindingProcessor

if TYPE_CHECKING:
    from scripts.migration_support import IsolatedMysql

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


@pytest.fixture(scope="session")
def _span_exporter() -> InMemorySpanExporter:
    """Register the trace binding processor and an in-memory exporter once per process.

    OpenTelemetry allows exactly one global TracerProvider per process, so this
    fixture reuses whichever provider is already installed (a real SDK provider
    from an earlier `configure_tracing` call) instead of replacing it.
    """
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(TraceBindingProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture
def span_recorder(_span_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    """Yield an in-memory span exporter cleared before and after each test."""
    _span_exporter.clear()
    yield _span_exporter
    _span_exporter.clear()


# New Project scenarios own a fresh database even inside the broad wrapper.
# Caller URLs are restored before the scenario and never selected for its SQL.
@pytest.fixture
def owned_project_mysql(monkeypatch: pytest.MonkeyPatch) -> Iterator[IsolatedMysql]:
    from scripts.migration_support import isolated_mysql

    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    with ExitStack() as resources:
        with monkeypatch.context() as environment:
            environment.delenv("TAP_DATABASE_URL", raising=False)
            environment.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
            database = resources.enter_context(isolated_mysql())
            database.upgrade("head")
        yield database


def _remove_owned_redis(docker, name, env):
    result = subprocess.run(
        [*docker, "rm", "--force", name], capture_output=True, env=env, timeout=20
    )
    if result.returncode:
        raise RuntimeError("owned Redis cleanup failed")


@pytest.fixture
def owned_redis_url():
    """A disposable, isolated Redis container on a random host port.

    Moved here (unchanged) from `tests/unit/operations/test_redis_stream_recovery.py`
    so other suites (e.g. `tests/integration/test_redis_drafts_integration.py`) can
    reuse the same gated, self-cleaning fixture instead of depending on a fixed
    external Redis URL/port.
    """
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
                except RedisConnectionError:
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


def validation_http_services(knowledge=None, readiness=None, traces=None):
    """Explicit in-memory trusted authority for HTTP tests; never install it globally."""
    from tap.interfaces.http.dependencies import HttpServices
    from tap.modules.access.adapters.validation import (
        VALIDATION_SCOPE,
        ValidationAuthorizationPolicy,
        ValidationScopeProvider,
    )
    from tap.modules.access.domain.authorization import ActorPrincipal

    class Registry:
        async def get_principal(self, enterprise_id, project_id, actor_id):
            assert (enterprise_id, project_id, actor_id) == (
                "local",
                "tapper-demo",
                "tapper-local-user",
            )
            return ActorPrincipal(
                enterprise_id=enterprise_id,
                actor_id=actor_id,
                principal_type="VALIDATION",
                enabled=True,
            )

    return HttpServices(
        knowledge=knowledge,
        readiness=readiness,
        traces=traces,
        scope=VALIDATION_SCOPE,
        scope_provider=ValidationScopeProvider(),
        authorization_policy=ValidationAuthorizationPolicy(Registry()),
    )
