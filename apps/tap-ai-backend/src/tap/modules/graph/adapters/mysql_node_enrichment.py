"""Best-effort `GET /nodes/{node_id}` enrichment: resolves a source's display
name and an evidence chunk's text snippet through the existing Knowledge
read paths (the `knowledge_source`/`knowledge_document_revision` tables and
the same `ArtifactStore.read_chunks` the extraction worker itself reads from).
Both lookups are scoped to the caller's enterprise/Project and resolve to
`None` -- never an error -- whenever nothing matches, since this is cosmetic
enrichment layered onto an already-complete response, not a correctness-
bearing read."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.adapters.mysql import graph_extraction_job
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document_revision,
    knowledge_source,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator, ArtifactStore
from tap.modules.knowledge.ports.errors import ArtifactError
from tap.platform.db.project_scope import require_project_scope, scope_predicates

__all__ = ["MysqlGraphNodeEnrichment"]

_SNIPPET_MAX = 300


class MysqlGraphNodeEnrichment:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], artifacts: ArtifactStore
    ) -> None:
        self._sessions = sessions
        self._artifacts = artifacts

    async def source_name(self, scope: ProjectScopeContext, source_revision_id: str) -> str | None:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(knowledge_source.c.name)
                        .select_from(
                            knowledge_document_revision.join(
                                knowledge_source,
                                knowledge_source.c.source_id
                                == knowledge_document_revision.c.source_id,
                            )
                        )
                        .where(
                            *scope_predicates(knowledge_document_revision, scope),
                            *scope_predicates(knowledge_source, scope),
                            knowledge_document_revision.c.revision_id == source_revision_id,
                            knowledge_source.c.deleted_at.is_(None),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else str(row["name"])

    async def snippet(
        self, scope: ProjectScopeContext, source_revision_id: str, chunk_id: str
    ) -> str | None:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(graph_extraction_job.c.chunks_locator).where(
                            *scope_predicates(graph_extraction_job, scope),
                            graph_extraction_job.c.revision_id == source_revision_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        locator = row["chunks_locator"]
        if not isinstance(locator, str) or not locator:
            return None
        try:
            chunks = await self._artifacts.read_chunks(ArtifactLocator(locator))
        except ArtifactError:
            return None
        for chunk in chunks:
            if str(chunk.chunk_id) == chunk_id:
                return chunk.content[:_SNIPPET_MAX] if chunk.content else None
        return None
