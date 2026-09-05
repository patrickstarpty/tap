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
