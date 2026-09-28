"""Direct chunk lifecycle retains current-document authority for test design."""

from datetime import UTC, datetime

import pytest

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Session:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries = []

    async def execute(self, query):
        self.queries.append(str(query))
        return Rows(next(self.rows))


@pytest.mark.asyncio
async def test_direct_scope_requires_current_ready_revision_and_uses_actual_inventory():
    repository = object.__new__(MysqlTestPlanRepository)
    repository._knowledge_requires_publication = False
    scope = ProjectScopeContext(
        enterprise_id="enterprise",
        project_id="project",
        actor_id="actor",
        identity_mode=IdentityMode.VALIDATION,
    )
    session = Session(
        [
            [{"revision_id": "revision"}],
            [{"item_id": "chunk", "source_revision_id": "revision", "locator": "paragraph:1"}],
        ]
    )
    snapshot, items = await repository._current_requirement_scope(
        session, scope, {"revision"}, datetime.now(UTC)
    )
    assert items == {("revision", "chunk")}
    assert snapshot.scope_id.startswith("rs_")
    assert "knowledge_document.status" in session.queries[0]
    assert "knowledge_document.current_revision_id" in session.queries[0]
    assert "knowledge_source.deleted_at IS NULL" in session.queries[0]


@pytest.mark.asyncio
async def test_direct_scope_rejects_stale_or_disabled_document():
    repository = object.__new__(MysqlTestPlanRepository)
    repository._knowledge_requires_publication = False
    scope = ProjectScopeContext(
        enterprise_id="enterprise",
        project_id="project",
        actor_id="actor",
        identity_mode=IdentityMode.VALIDATION,
    )
    with pytest.raises(ValueError, match="current ready"):
        await repository._current_requirement_scope(
            Session([[]]), scope, {"stale"}, datetime.now(UTC)
        )
