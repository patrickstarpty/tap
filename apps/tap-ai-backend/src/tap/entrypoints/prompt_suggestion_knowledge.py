"""Entrypoint adapters wiring Knowledge internals into the chat prompt
suggestion ports. chat is not allowed to import Knowledge internals directly
(see tests/architecture/test_module_boundaries.py), so these three ports —
current sources, topics/entities, and grounding — live here instead, next to
the ReadyRevisionProjection that triggers a refresh when Knowledge publishes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.http import PublishedKnowledgeSource
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql_suggestions import MysqlSuggestionStore
from tap.modules.chat.application.suggestion_ports import CurrentSource
from tap.modules.chat.domain.suggestions import RefreshReason, TopicSource
from tap.modules.graph.adapters.mysql import graph_node, graph_node_evidence
from tap.modules.knowledge.adapters.mysql_documents import (
    ReadyRevisionProjection,
    knowledge_chunk_manifest,
)
from tap.modules.knowledge.adapters.mysql_managed_chunks import managed_documents
from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
from tap.modules.knowledge.application.answers import AnswerService
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    ResourceMode,
    ResourceRef,
    SourceFamily,
)
from tap.modules.knowledge.ports.errors import AnswerUnavailable
from tap.platform.db.project_scope import require_project_scope, scope_predicates

# The answer adapter reports an unreachable model with this exact message; every
# other AnswerUnavailable means the model answered but the answer failed validation.
_MODEL_UNAVAILABLE = "model-unavailable"

_MAX_TOPIC_SOURCES = 30
_MAX_HEADINGS_PER_SOURCE = 8


def _source_version(revision_id: str, managed_version: int) -> str:
    return f"{revision_id}:{managed_version}"


class KnowledgeSuggestionSources:
    """Reads currently-ready knowledge sources, their opaque version and
    section headings, and the graph's most-evidenced entities. The runtime
    currently binds one demo Project scope per actor, so ``actor_id`` is
    accepted for port symmetry but does not change which rows are read."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)
        self._ready_sources = MysqlReadySources(sessions, self._scope)

    async def _ready_items(self) -> Sequence[PublishedKnowledgeSource]:
        """All currently-ready sources, uncapped (bounded only by
        MysqlReadySources.list_sources()'s own limit of 100). current_sources()
        and main_entities() must see every ready source — the read-time filter
        in PromptSuggestionService.list() drops a cached suggestion whenever
        one of its source ids is missing from current_sources(), so an
        arbitrary cap there would incorrectly discard otherwise-valid cached
        suggestions once a project has more than that many ready sources.
        Only topics() (the model-input side, bounded by prompt size) caps to
        _MAX_TOPIC_SOURCES."""
        page = await self._ready_sources.list_sources()
        return page.items

    async def _managed_versions(self, document_ids: Sequence[str]) -> Mapping[str, int]:
        if not document_ids:
            return {}
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(managed_documents.c.document_id, managed_documents.c.version).where(
                            *scope_predicates(managed_documents, self._scope),
                            managed_documents.c.document_id.in_(document_ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
        return {row["document_id"]: cast(int, row["version"]) for row in rows}

    async def _headings(self, revision_ids: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
        if not revision_ids:
            return {}
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            knowledge_chunk_manifest.c.revision_id,
                            knowledge_chunk_manifest.c.anchor_json,
                        )
                        .where(
                            *scope_predicates(knowledge_chunk_manifest, self._scope),
                            knowledge_chunk_manifest.c.revision_id.in_(revision_ids),
                        )
                        .order_by(
                            knowledge_chunk_manifest.c.revision_id,
                            knowledge_chunk_manifest.c.ordinal,
                        )
                    )
                )
                .mappings()
                .all()
            )
        by_revision: dict[str, list[str]] = {}
        seen: dict[str, set[str]] = {}
        for row in rows:
            revision_id = cast(str, row["revision_id"])
            anchor = row["anchor_json"] or {}
            heading_path = anchor.get("headingPath") or []
            if not heading_path:
                continue
            leaf = heading_path[-1]
            headings = by_revision.setdefault(revision_id, [])
            dedupe = seen.setdefault(revision_id, set())
            if leaf in dedupe or len(headings) >= _MAX_HEADINGS_PER_SOURCE:
                continue
            dedupe.add(leaf)
            headings.append(leaf)
        return {revision_id: tuple(values) for revision_id, values in by_revision.items()}

    async def current_sources(self, actor_id: str) -> Mapping[str, CurrentSource]:
        del actor_id
        items = await self._ready_items()
        versions = await self._managed_versions([item.document_id for item in items])
        return {
            item.source_id: CurrentSource(
                source_id=item.source_id,
                name=item.source_name,
                version=_source_version(item.revision_id, versions.get(item.document_id, 0)),
            )
            for item in items
        }

    async def topics(self, actor_id: str) -> tuple[TopicSource, ...]:
        del actor_id
        items = (await self._ready_items())[:_MAX_TOPIC_SOURCES]
        versions = await self._managed_versions([item.document_id for item in items])
        headings = await self._headings([item.revision_id for item in items])
        return tuple(
            TopicSource(
                source_id=item.source_id,
                name=item.source_name,
                version=_source_version(item.revision_id, versions.get(item.document_id, 0)),
                headings=headings.get(item.revision_id, ()),
            )
            for item in items
        )

    async def main_entities(self, actor_id: str, *, limit: int) -> tuple[str, ...]:
        del actor_id
        items = await self._ready_items()
        revision_ids = [item.revision_id for item in items]
        if not revision_ids:
            return ()
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            graph_node.c.label,
                            func.count(graph_node_evidence.c.chunk_id).label("evidence_count"),
                        )
                        .select_from(
                            graph_node_evidence.join(
                                graph_node,
                                (graph_node.c.snapshot_id == graph_node_evidence.c.snapshot_id)
                                & (graph_node.c.node_id == graph_node_evidence.c.node_id),
                            )
                        )
                        .where(
                            *scope_predicates(graph_node_evidence, self._scope),
                            *scope_predicates(graph_node, self._scope),
                            graph_node_evidence.c.source_revision_id.in_(revision_ids),
                        )
                        .group_by(
                            graph_node.c.snapshot_id, graph_node.c.node_id, graph_node.c.label
                        )
                        .order_by(func.count(graph_node_evidence.c.chunk_id).desc())
                    )
                )
                .mappings()
                .all()
            )
        seen: set[str] = set()
        entities: list[str] = []
        for row in rows:
            label = cast(str, row["label"])
            if label in seen:
                continue
            seen.add(label)
            entities.append(label)
            if len(entities) >= limit:
                break
        return tuple(entities)


class AnswerGroundingCheck:
    """Grounding is "the current user's AnswerService call does not abstain",
    scoped to exactly the sources the candidate cites."""

    def __init__(self, answers: AnswerService) -> None:
        self._answers = answers

    async def is_grounded(self, actor_id: str, question: str, source_ids: tuple[str, ...]) -> bool:
        del actor_id  # actor identity is bound by the AnswerService's own scope
        request = AnswerRequest(
            query=question,
            resource_refs=tuple(
                ResourceRef(SourceFamily.DOC, source_id, ResourceMode.SCOPE)
                for source_id in source_ids
            ),
        )
        try:
            response = await self._answers.answer(request)
        except AnswerUnavailable as error:
            # An invalid answer means the question cannot be answered reliably,
            # so the candidate is not grounded; an outage still fails the refresh.
            if str(error) == _MODEL_UNAVAILABLE:
                raise
            return False
        return not response.abstained


class SuggestionReadyProjection:
    """Requests a project-wide prompt suggestion refresh inside the same
    transaction that marks a Knowledge revision ready, so a newly published
    source is reflected without waiting for the daily fallback."""

    def __init__(self, store: MysqlSuggestionStore) -> None:
        self._store = store

    async def after_ready(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        revision: RowMapping,
        *,
        now: datetime,
        ingestion_job_id: str,
    ) -> None:
        del scope, revision, ingestion_job_id
        await self._store.request_refresh_for_project_in_transaction(
            session, RefreshReason.KNOWLEDGE_PUBLISHED, now=now
        )


class CompositeReadyProjection:
    """Runs several ReadyRevisionProjection implementations in sequence
    inside the same READY transaction (e.g. graph extraction and prompt
    suggestion refresh)."""

    def __init__(self, *projections: ReadyRevisionProjection) -> None:
        self._projections = projections

    async def after_ready(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        revision: RowMapping,
        *,
        now: datetime,
        ingestion_job_id: str,
    ) -> None:
        for projection in self._projections:
            await projection.after_ready(
                session, scope, revision, now=now, ingestion_job_id=ingestion_job_id
            )
