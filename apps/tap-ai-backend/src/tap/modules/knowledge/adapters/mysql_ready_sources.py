"""Current indexed sources for the direct chunk lifecycle."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.http import PublishedKnowledgeSource, PublishedKnowledgeSourcePage
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_source,
    managed_chunks_visible,
)
from tap.platform.db.project_scope import scope_predicates


@dataclass(frozen=True, slots=True)
class ReadySourceRevision:
    source_id: str
    revision_id: str
    filename: str


class MysqlReadySources:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], scope: ProjectScopeContext):
        self._sessions = sessions
        self._scope = scope

    async def list_sources(self) -> PublishedKnowledgeSourcePage:
        rows = await self._ready_rows(100)
        return PublishedKnowledgeSourcePage(
            items=[
                PublishedKnowledgeSource(
                    source_id=row["source_id"],
                    document_id=row["document_id"],
                    revision_id=row["current_revision_id"],
                    source_name=row["source_name"],
                    filename=row["filename"],
                    publication_id=None,
                    expires_at=None,
                    approved_item_count=0,
                    inventory_item_count=row["chunk_count"],
                    partial=False,
                )
                for row in rows
            ]
        )

    async def list_revisions(self, *, limit: int) -> tuple[ReadySourceRevision, ...]:
        """Every ready current revision, newest first, bounded by an explicit ``limit``."""
        rows = await self._ready_rows(limit)
        return tuple(
            ReadySourceRevision(row["source_id"], row["current_revision_id"], row["filename"])
            for row in rows
        )

    async def _ready_rows(self, limit: int) -> Any:
        async with self._sessions() as session:
            return (
                (
                    await session.execute(
                        select(knowledge_document, knowledge_source.c.name.label("source_name"))
                        .select_from(
                            knowledge_document.join(
                                knowledge_source,
                                knowledge_source.c.source_id == knowledge_document.c.source_id,
                            )
                        )
                        .where(
                            *scope_predicates(knowledge_document, self._scope),
                            *scope_predicates(knowledge_source, self._scope),
                            knowledge_source.c.deleted_at.is_(None),
                            knowledge_document.c.deleted_at.is_(None),
                            knowledge_document.c.status == "ready",
                            managed_chunks_visible(self._scope),
                            knowledge_document.c.chunk_count > 0,
                            knowledge_document.c.current_revision_id.is_not(None),
                        )
                        .order_by(
                            knowledge_document.c.updated_at.desc(), knowledge_document.c.document_id
                        )
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
