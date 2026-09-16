from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from scripts.migration_support import IsolatedMysql

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


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


def validation_http_services(knowledge=None, readiness=None):
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
        scope=VALIDATION_SCOPE,
        scope_provider=ValidationScopeProvider(),
        authorization_policy=ValidationAuthorizationPolicy(Registry()),
    )
