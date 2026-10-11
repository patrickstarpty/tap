"""Entrypoint adapter wiring Knowledge internals into the Graph module's
`CurrentRevisionsPort`. Graph is not allowed to import Knowledge internals
directly (see tests/architecture/test_module_boundaries.py), so this port
lives here instead, next to the other Knowledge-reading entrypoint adapters
(e.g. `prompt_suggestion_knowledge.py`)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_documents import knowledge_document_revision
from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
from tap.platform.db.project_scope import require_project_scope, scope_predicates

__all__ = ["MysqlCurrentRevisions", "MysqlPublishedChunkLocators"]

logger = logging.getLogger(__name__)

# `MysqlReadySources.list_sources` hard-caps its page at 100 rows; a merge
# that sees exactly this many ready sources may be silently missing some of
# the project's documents rather than happening to have precisely 100.
_READY_SOURCES_CAP = 100


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
        if len(page.items) == _READY_SOURCES_CAP:
            logger.warning(
                "project graph merge input capped: project_id=%s cap=%d",
                self._scope.project_id,
                _READY_SOURCES_CAP,
            )
        return frozenset(item.revision_id for item in page.items)


class MysqlPublishedChunkLocators:
    """Chunks-artifact locator of every currently-published revision (the same
    bounded revision set the merge reads), for the graph worker's startup
    backfill. A revision without a chunks artifact is omitted: it cannot be
    extracted."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)
        self._revisions = MysqlCurrentRevisions(sessions, scope=self._scope)

    async def published(self) -> dict[str, str]:
        revision_ids = await self._revisions.current_revision_ids(self._scope)
        if not revision_ids:
            return {}
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            knowledge_document_revision.c.revision_id,
                            knowledge_document_revision.c.chunks_blob_locator,
                        ).where(
                            *scope_predicates(knowledge_document_revision, self._scope),
                            knowledge_document_revision.c.revision_id.in_(sorted(revision_ids)),
                        )
                    )
                )
                .mappings()
                .all()
            )
        return {
            str(row["revision_id"]): str(row["chunks_blob_locator"])
            for row in rows
            if row["chunks_blob_locator"]
        }
