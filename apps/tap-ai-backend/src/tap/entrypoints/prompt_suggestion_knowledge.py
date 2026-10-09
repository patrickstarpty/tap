"""Entrypoint adapters wiring Knowledge internals into the chat prompt
suggestion ports. chat is not allowed to import Knowledge internals directly
(see tests/architecture/test_module_boundaries.py), so these three ports —
current sources, topics/entities, and grounding — live here instead, next to
the ReadyRevisionProjection that triggers a refresh when Knowledge publishes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.http import (
    PublishedKnowledgeSource,
    ResourceMode,
    ResourceRef,
    RetrievalAnswerRequest,
    SourceFamily,
)
from tap.interfaces.http.conversation_selection import freeze_conversation_selection
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql_suggestions import MysqlSuggestionStore
from tap.modules.chat.application.plan_answer import planning_input
from tap.modules.chat.application.suggestion_ports import CurrentSource
from tap.modules.chat.domain.conversations import TurnInput, TurnInputSnapshot, content_digest
from tap.modules.chat.domain.suggestions import MainRelation, RefreshReason, TopicSource
from tap.modules.graph.adapters.mysql import graph_node, graph_node_evidence
from tap.modules.graph.adapters.mysql_project import (
    graph_project_edge,
    graph_project_edge_evidence,
    graph_project_node,
)
from tap.modules.graph.ports.project_store import ProjectGraphStorePort
from tap.modules.knowledge.adapters.mysql_documents import (
    ReadyRevisionProjection,
    knowledge_chunk_manifest,
)
from tap.modules.knowledge.adapters.mysql_managed_chunks import managed_documents
from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
from tap.modules.knowledge.ports.errors import AnswerUnavailable
from tap.platform.db.project_scope import require_project_scope, scope_predicates

# Placeholder graph extraction emits one node per document and per chunk; they name
# revisions and sections, not domain entities, so they never reach suggestion prompts.
_STRUCTURAL_NODE_KEY_PREFIXES = ("document:", "chunk:")

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
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
        project_graph: ProjectGraphStorePort | None = None,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)
        self._ready_sources = MysqlReadySources(sessions, self._scope)
        self._project_graph = project_graph

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
                            *(
                                ~graph_node.c.canonical_key.startswith(prefix)
                                for prefix in _STRUCTURAL_NODE_KEY_PREFIXES
                            ),
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

    async def main_relations(self, actor_id: str, *, limit: int) -> tuple[MainRelation, ...]:
        """Main relations for the prompt-suggestion generator, read from the merged
        Project graph (not the per-fragment graph main_entities() reads). None or
        non-READY current version yields no relations; otherwise edges are ranked
        by (subject.degree + object.degree) * confidence, ties broken by edge_id,
        restricted to edges with at least one evidence row in a currently-ready
        source revision, and deduped by (subject, relation_type, object)."""
        del actor_id
        if self._project_graph is None:
            return ()
        current = await self._project_graph.get_current(self._scope)
        if current is None or current.status != "READY":
            return ()
        items = await self._ready_items()
        revision_ids = [item.revision_id for item in items]
        if not revision_ids:
            return ()
        version = current.version
        subject_node = graph_project_node.alias("prompt_suggestion_relation_subject")
        object_node = graph_project_node.alias("prompt_suggestion_relation_object")
        has_ready_evidence = (
            select(graph_project_edge_evidence.c.edge_id)
            .where(
                *scope_predicates(graph_project_edge_evidence, self._scope),
                graph_project_edge_evidence.c.version == version,
                graph_project_edge_evidence.c.edge_id == graph_project_edge.c.edge_id,
                graph_project_edge_evidence.c.source_revision_id.in_(revision_ids),
            )
            .exists()
        )
        degree_score = (
            subject_node.c.degree + object_node.c.degree
        ) * graph_project_edge.c.confidence
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            graph_project_edge.c.edge_id,
                            subject_node.c.label.label("subject_label"),
                            graph_project_edge.c.relation_type,
                            graph_project_edge.c.relation_label,
                            object_node.c.label.label("object_label"),
                        )
                        .select_from(
                            graph_project_edge.join(
                                subject_node,
                                (subject_node.c.version == graph_project_edge.c.version)
                                & (subject_node.c.node_id == graph_project_edge.c.source_node_id),
                            ).join(
                                object_node,
                                (object_node.c.version == graph_project_edge.c.version)
                                & (object_node.c.node_id == graph_project_edge.c.target_node_id),
                            )
                        )
                        .where(
                            *scope_predicates(graph_project_edge, self._scope),
                            *scope_predicates(subject_node, self._scope),
                            *scope_predicates(object_node, self._scope),
                            graph_project_edge.c.version == version,
                            subject_node.c.version == version,
                            object_node.c.version == version,
                            has_ready_evidence,
                            *(
                                ~subject_node.c.canonical_key.startswith(prefix)
                                for prefix in _STRUCTURAL_NODE_KEY_PREFIXES
                            ),
                            *(
                                ~object_node.c.canonical_key.startswith(prefix)
                                for prefix in _STRUCTURAL_NODE_KEY_PREFIXES
                            ),
                        )
                        .order_by(degree_score.desc(), graph_project_edge.c.edge_id.asc())
                    )
                )
                .mappings()
                .all()
            )
        seen: set[tuple[str, str, str]] = set()
        relations: list[MainRelation] = []
        for row in rows:
            subject_label = cast(str, row["subject_label"])
            object_label = cast(str, row["object_label"])
            relation_type = cast(str, row["relation_type"])
            key = (subject_label, relation_type, object_label)
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                MainRelation(
                    subject=subject_label,
                    relation_type=relation_type,
                    relation_label=cast(str, row["relation_label"]),
                    object=object_label,
                )
            )
            if len(relations) >= limit:
                break
        return tuple(relations)


class ConversationGroundingCheck:
    """Grounding runs the candidate through the same planner and answer pipeline as a chat
    turn (without persisting a conversation), scoped to exactly the sources the candidate
    cites, so a suggestion is shown only when clicking it yields a cited answer."""

    def __init__(
        self,
        knowledge: Any,
        *,
        ready_sources: Any,
        scope: ProjectScopeContext,
        model_alias: str,
    ) -> None:
        self._knowledge = knowledge
        self._ready_sources = ready_sources
        self._scope = require_project_scope(scope)
        self._model_alias = model_alias

    async def is_grounded(self, actor_id: str, question: str, source_ids: tuple[str, ...]) -> bool:
        current = {
            item.source_id: item.revision_id
            for item in (await self._ready_sources.list_sources()).items
        }
        if any(source_id not in current for source_id in source_ids):
            return False
        revision_ids = tuple(current[source_id] for source_id in source_ids)
        selection = await freeze_conversation_selection(
            self._knowledge, source_revision_ids=revision_ids
        )
        value = TurnInput(
            message=question,
            actor_id=actor_id,
            identity_mode=self._scope.identity_mode.value,
            model_alias=self._model_alias,
            source_revision_ids=revision_ids,
            resolved_resources=selection.resolved_resources,
            acl_digest=selection.acl_digest,
            retrieval_policy_digest=selection.retrieval_policy_digest,
        )
        turn_id = uuid4().hex
        snapshot = TurnInputSnapshot(
            snapshot_id=uuid4().hex,
            project_id=self._scope.project_id,
            turn_id=turn_id,
            value=value,
            digest=content_digest(
                value.material(project_id=self._scope.project_id, turn_id=turn_id)
            ),
            created_at=datetime.now(UTC),
        )
        planner = getattr(self._knowledge, "answer_planner", None)
        plan = None
        if planner is not None:
            await self._knowledge.authorize_planning(snapshot)
            plan = await planner.plan(planning_input(snapshot))
        request = RetrievalAnswerRequest(
            query=question,
            sources=[SourceFamily.DOC],
            resource_refs=[
                ResourceRef(
                    family=SourceFamily.DOC, source_id=item.source_id, mode=ResourceMode.SCOPE
                )
                for item in value.resolved_resources
            ],
        )
        try:
            answer = await self._knowledge.answer_conversation(
                request,
                value,
                **({"answer_plan": plan, "authorize": _always_authorized} if plan else {}),
            )
        except AnswerUnavailable as error:
            # An invalid answer means the question cannot be answered reliably,
            # so the candidate is not grounded; an outage still fails the refresh.
            if str(error) == _MODEL_UNAVAILABLE:
                raise
            return False
        return not answer.abstained and bool(answer.citations)


async def _always_authorized() -> None:
    """A dry-run turn holds no lease that could be lost while answering."""


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
