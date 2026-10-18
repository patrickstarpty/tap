from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tap.platform.telemetry import TraceBindingProcessor

if TYPE_CHECKING:
    from scripts.migration_support import IsolatedMysql

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

# pymilvus.settings calls load_dotenv() on import, which would leak the repository
# .env (including shared database URLs) into every test. Import it once here and
# restore the caller environment so tests only see explicitly provided values.
_environment_before_pymilvus_import = dict(os.environ)
importlib.import_module("pymilvus.settings")
os.environ.clear()
os.environ.update(_environment_before_pymilvus_import)
del _environment_before_pymilvus_import


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


# Ledger and recovery scenarios clean their tables between tests, so one owned
# head-migrated database per module is enough; never fall back to caller URLs.
@pytest.fixture(scope="module")
def owned_module_database_url() -> Iterator[str]:
    from scripts.migration_support import isolated_mysql

    from tests.owned_mysql import owned_project_database_url

    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    with ExitStack() as resources:
        with pytest.MonkeyPatch.context() as environment:
            environment.delenv("TAP_DATABASE_URL", raising=False)
            environment.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
            database = resources.enter_context(isolated_mysql())
            database.upgrade("head")
        yield owned_project_database_url(database)


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
