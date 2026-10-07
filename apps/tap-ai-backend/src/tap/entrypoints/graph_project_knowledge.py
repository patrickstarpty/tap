"""Entrypoint adapter wiring Knowledge internals into the Graph module's
`CurrentRevisionsPort`. Graph is not allowed to import Knowledge internals
directly (see tests/architecture/test_module_boundaries.py), so this port
lives here instead, next to the other Knowledge-reading entrypoint adapters
(e.g. `prompt_suggestion_knowledge.py`)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
from tap.platform.db.project_scope import require_project_scope

__all__ = ["MysqlCurrentRevisions"]


class MysqlCurrentRevisions:
    """The currently-published, non-deleted document revision ids a Project
    graph merge may read fragments for (bounded to `MysqlReadySources`'s own
    cap of 100 ready sources)."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._scope = require_project_scope(scope)
        self._ready_sources = MysqlReadySources(sessions, self._scope)

    async def current_revision_ids(self, scope: ProjectScopeContext) -> frozenset[str]:
        del scope
        page = await self._ready_sources.list_sources()
        return frozenset(item.revision_id for item in page.items)
