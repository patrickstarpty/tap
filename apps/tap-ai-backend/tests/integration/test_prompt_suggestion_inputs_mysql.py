"""Knowledge- and usage-side adapters for prompt suggestions against real
MySQL: readiness/version/heading projection, graph evidence ranking, and
chat usage statistics that never leak another actor's question text."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.prompt_suggestion_knowledge import (
    KnowledgeSuggestionSources,
    SuggestionReadyProjection,
)
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.adapters.mysql_suggestion_usage import MysqlSuggestionUsage
from tap.modules.chat.adapters.mysql_suggestions import (
    MysqlSuggestionStore,
    prompt_suggestion_refresh,
)
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import FrozenResource, TurnInput
from tap.modules.chat.domain.suggestions import RefreshReason, SuggestionKey
from tap.modules.graph.adapters.mysql import MysqlGraphStore
from tap.modules.graph.adapters.mysql_project import publish_project_version
from tap.modules.graph.adapters.mysql_project_store import MysqlProjectGraphStore
from tap.modules.graph.domain.models import (
    Evidence,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.graph.domain.project import (
    EdgeEvidence,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
)
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_chunk_manifest,
    knowledge_document,
    knowledge_document_revision,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_managed_chunks import managed_documents
from tap.platform.db.project_scope import scope_predicates, scope_values
from tests.owned_mysql import owned_project_database_url

NOW = datetime(2026, 9, 30, 9, 0, 0, tzinfo=timezone.utc)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
OTHER_SCOPE = replace(VALIDATION_SCOPE, actor_id="tapper-other-user")


def _src(tag: str) -> str:
    """PublishedKnowledgeSource.source_id must match `^src_[0-9a-f]{32}$`."""
    return "src_" + hashlib.sha256(tag.encode()).hexdigest()[:32]


def _run(owned_project_mysql, scenario):
    url = owned_project_database_url(owned_project_mysql)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def wrapped():
        try:
            await scenario(sessions, engine)
        finally:
            await engine.dispose()

    asyncio.run(wrapped())


async def _insert_actor(engine, actor_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO actor_principal (enterprise_id, actor_id, principal_type) "
                "VALUES (:enterprise_id, :actor_id, 'VALIDATION')"
            ),
            {"enterprise_id": VALIDATION_SCOPE.enterprise_id, "actor_id": actor_id},
        )


async def _seed_document(
    session,
    *,
    source_id: str,
    document_id: str,
    revision_id: str,
    name: str,
    status: str = "ready",
    deleted_source: bool = False,
    deleted_document: bool = False,
    chunk_count: int = 1,
    headings: tuple[tuple[str, ...], ...] = (),
    source_content_hash: str = DIGEST_A,
) -> None:
    await session.execute(
        insert(knowledge_source).values(
            **scope_values(VALIDATION_SCOPE),
            source_id=source_id,
            name=name,
            created_at=NOW.replace(tzinfo=None),
            updated_at=NOW.replace(tzinfo=None),
            deleted_at=NOW.replace(tzinfo=None) if deleted_source else None,
        )
    )
    await session.execute(
        insert(knowledge_document).values(
            **scope_values(VALIDATION_SCOPE),
            document_id=document_id,
            source_id=source_id,
            filename=f"{document_id}.md",
            media_type="text/markdown",
            current_revision_id=None,
            source_content_hash=source_content_hash,
            dedupe_key=document_id + "-dedupe",
            staging_blob_locator=None,
            promoted_blob_locator=None,
            reservation_owner_token=None,
            reservation_expires_at=None,
            reservation_parser_version="tapper-parser-v1",
            reservation_chunker_version="tapper-chunker-v1",
            reservation_pipeline_version="tapper-ingestion-v1",
            status=status,
            stage=status,
            chunk_count=chunk_count,
            error_code=None,
            error_summary=None,
            activated_at=NOW.replace(tzinfo=None),
            created_at=NOW.replace(tzinfo=None),
            updated_at=NOW.replace(tzinfo=None),
            deleted_at=NOW.replace(tzinfo=None) if deleted_document else None,
        )
    )
    await _seed_revision(
        session,
        source_id=source_id,
        document_id=document_id,
        revision_id=revision_id,
        headings=headings,
        source_content_hash=source_content_hash,
        set_current=True,
    )


async def _seed_revision(
    session,
    *,
    source_id: str,
    document_id: str,
    revision_id: str,
    headings: tuple[tuple[str, ...], ...] = (),
    source_content_hash: str = DIGEST_A,
    set_current: bool = False,
) -> None:
    await session.execute(
        insert(knowledge_document_revision).values(
            **scope_values(VALIDATION_SCOPE),
            revision_id=revision_id,
            source_id=source_id,
            document_id=document_id,
            chunk_manifest_digest=DIGEST_A,
            projection_digest=DIGEST_A,
            source_content_hash=source_content_hash,
            original_blob_locator=f"tapper-originals/{revision_id}",
            normalized_blob_locator=f"tapper-artifacts/{revision_id}.normalized",
            chunks_blob_locator=f"tapper-artifacts/{revision_id}.chunks",
            embeddings_blob_locator=None,
            parser_version="tapper-parser-v1",
            chunker_version="tapper-chunker-v1",
            pipeline_version="tapper-ingestion-v1",
            parse_inventory_attempt=1,
            parser_config_digest=DIGEST_A,
            parse_inventory_digest=DIGEST_A,
            created_at=NOW.replace(tzinfo=None),
        )
    )
    for ordinal, heading_path in enumerate(headings):
        chunk_id = f"{revision_id}-chunk-{ordinal}"
        await session.execute(
            insert(knowledge_chunk_manifest).values(
                **scope_values(VALIDATION_SCOPE),
                chunk_id=chunk_id,
                logical_chunk_id=chunk_id,
                revision_id=revision_id,
                ordinal=ordinal,
                root_id=f"{revision_id}-root",
                parent_id=None,
                anchor_json={
                    "type": "document",
                    "headingPath": list(heading_path),
                    "startOffset": ordinal * 10,
                    "endOffset": ordinal * 10 + 5,
                },
                chunk_content_hash=DIGEST_A,
                embedding_model_version="test-embed-v1",
                index_version="test-index-v1",
                created_at=NOW.replace(tzinfo=None),
            )
        )
    if set_current:
        await session.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == document_id)
            .values(current_revision_id=revision_id)
        )


async def _seed_managed_document(
    session, *, document_id: str, revision_id: str, version: int, index_status: str = "ready"
) -> None:
    await session.execute(
        insert(managed_documents).values(
            **scope_values(VALIDATION_SCOPE),
            document_id=document_id,
            original_revision_id=revision_id,
            version=version,
            settings_json={},
            chunks_json=[],
            index_status=index_status,
            index_error=None,
            purge_revision_id=None,
        )
    )


def test_current_sources_exclude_deleted_and_unready_documents(owned_project_mysql):
    async def scenario(sessions, engine):
        async with sessions() as session, session.begin():
            await _seed_document(
                session,
                source_id=_src("ready"),
                document_id="doc-ready",
                revision_id="rev-ready",
                name="Ready Guide",
            )
            await _seed_document(
                session,
                source_id=_src("deleted"),
                document_id="doc-deleted",
                revision_id="rev-deleted",
                name="Deleted Guide",
                deleted_source=True,
            )
            await _seed_document(
                session,
                source_id=_src("processing"),
                document_id="doc-processing",
                revision_id="rev-processing",
                name="Processing Guide",
                status="processing",
            )

        knowledge = KnowledgeSuggestionSources(sessions, scope=VALIDATION_SCOPE)
        current = await knowledge.current_sources(VALIDATION_SCOPE.actor_id)

        assert set(current) == {_src("ready")}
        assert current[_src("ready")].version == "rev-ready:0"
        assert current[_src("ready")].name == "Ready Guide"

    _run(owned_project_mysql, scenario)


def test_current_sources_uncapped_but_topics_capped_at_30(owned_project_mysql):
    async def scenario(sessions, engine):
        total = 31
        async with sessions() as session, session.begin():
            for index in range(total):
                await _seed_document(
                    session,
                    source_id=_src(f"bulk-{index}"),
                    document_id=f"doc-bulk-{index}",
                    revision_id=f"rev-bulk-{index}",
                    name=f"Bulk Guide {index}",
                )

        knowledge = KnowledgeSuggestionSources(sessions, scope=VALIDATION_SCOPE)
        current = await knowledge.current_sources(VALIDATION_SCOPE.actor_id)
        topics = await knowledge.topics(VALIDATION_SCOPE.actor_id)

        assert len(current) == total
        assert len(topics) == 30

    _run(owned_project_mysql, scenario)


def test_source_version_changes_with_new_revision_and_chunk_edit(owned_project_mysql):
    async def scenario(sessions, engine):
        async with sessions() as session, session.begin():
            await _seed_document(
                session,
                source_id=_src("managed"),
                document_id="doc-managed",
                revision_id="rev-1",
                name="Managed Guide",
            )
            await _seed_managed_document(
                session, document_id="doc-managed", revision_id="rev-1", version=1
            )

        knowledge = KnowledgeSuggestionSources(sessions, scope=VALIDATION_SCOPE)
        current = await knowledge.current_sources(VALIDATION_SCOPE.actor_id)
        first = current[_src("managed")].version
        assert first == "rev-1:1"

        async with sessions() as session, session.begin():
            await session.execute(
                update(managed_documents)
                .where(
                    *scope_predicates(managed_documents, VALIDATION_SCOPE),
                    managed_documents.c.document_id == "doc-managed",
                )
                .values(version=2)
            )
        after_edit = (await knowledge.current_sources(VALIDATION_SCOPE.actor_id))[
            _src("managed")
        ].version
        assert after_edit == "rev-1:2"
        assert after_edit != first

        async with sessions() as session, session.begin():
            await _seed_revision(
                session,
                source_id=_src("managed"),
                document_id="doc-managed",
                revision_id="rev-2",
                source_content_hash=DIGEST_B,
                set_current=True,
            )
        after_republish = (await knowledge.current_sources(VALIDATION_SCOPE.actor_id))[
            _src("managed")
        ].version
        assert after_republish == "rev-2:2"
        assert after_republish != after_edit

    _run(owned_project_mysql, scenario)


def test_topics_list_headings_in_document_order(owned_project_mysql):
    async def scenario(sessions, engine):
        async with sessions() as session, session.begin():
            await _seed_document(
                session,
                source_id=_src("topics"),
                document_id="doc-topics",
                revision_id="rev-topics",
                name="Topics Guide",
                headings=(
                    ("Intro", "Overview"),
                    ("Intro", "Overview"),  # duplicate leaf, must be deduped
                    ("Intro", "Setup"),
                    ("Advanced", "Tuning"),
                ),
            )

        knowledge = KnowledgeSuggestionSources(sessions, scope=VALIDATION_SCOPE)
        topics = await knowledge.topics(VALIDATION_SCOPE.actor_id)

        assert [topic.source_id for topic in topics] == [_src("topics")]
        assert topics[0].headings == ("Overview", "Setup", "Tuning")
        assert topics[0].version == "rev-topics:0"

    _run(owned_project_mysql, scenario)


def test_main_entities_rank_domain_entities_by_evidence(owned_project_mysql):
    async def scenario(sessions, engine):
        async with sessions() as session, session.begin():
            await _seed_document(
                session,
                source_id=_src("graph"),
                document_id="doc-graph",
                revision_id="rev-graph",
                name="Graph Guide",
            )

        evidence = (
            Evidence(
                "evidence-1",
                "snapshot-1",
                "rev-graph",
                "doc-rev-graph",
                "chunk-1",
                {"kind": "text", "start": 0, "end": 5},
                DIGEST_A,
            ),
            Evidence(
                "evidence-2",
                "snapshot-1",
                "rev-graph",
                "doc-rev-graph",
                "chunk-2",
                {"kind": "text", "start": 5, "end": 10},
                DIGEST_A,
            ),
            Evidence(
                "evidence-3",
                "snapshot-1",
                "rev-graph",
                "doc-rev-graph",
                "chunk-3",
                {"kind": "text", "start": 10, "end": 15},
                DIGEST_A,
            ),
        )
        draft = GraphSnapshotDraft(
            GraphSnapshot.create(
                snapshot_id="snapshot-1",
                project_id=VALIDATION_SCOPE.project_id,
                source_revision_ids=("rev-graph",),
                document_revision_ids=("doc-rev-graph",),
            ),
            (
                GraphNode(
                    "node-popular",
                    "snapshot-1",
                    "Popular Entity",
                    "ENTITY",
                    "popular-entity",
                    ("evidence-1", "evidence-2"),
                ),
                GraphNode(
                    "node-rare",
                    "snapshot-1",
                    "Rare Entity",
                    "ENTITY",
                    "rare-entity",
                    ("evidence-3",),
                ),
                # Structural placeholder nodes (one per document / chunk) are not domain
                # entities even when they carry the most evidence.
                GraphNode(
                    "node-document",
                    "snapshot-1",
                    "rev-graph",
                    "ENTITY",
                    "document:rev-graph",
                    ("evidence-1", "evidence-2", "evidence-3"),
                ),
                GraphNode(
                    "node-chunk",
                    "snapshot-1",
                    "Section 1",
                    "CONCEPT",
                    "chunk:h_" + "c" * 64,
                    ("evidence-1", "evidence-2", "evidence-3"),
                ),
            ),
            (),
            evidence,
            (),
        )
        await MysqlGraphStore(sessions).publish(VALIDATION_SCOPE, draft)

        knowledge = KnowledgeSuggestionSources(sessions, scope=VALIDATION_SCOPE)
        entities = await knowledge.main_entities(VALIDATION_SCOPE.actor_id, limit=10)

        assert entities == ("Popular Entity", "Rare Entity")

        limited = await knowledge.main_entities(VALIDATION_SCOPE.actor_id, limit=1)
        assert limited == ("Popular Entity",)

    _run(owned_project_mysql, scenario)


def test_main_relations_rank_by_degree_and_confidence_within_ready_sources(owned_project_mysql):
    async def scenario(sessions, engine):
        async with sessions() as session, session.begin():
            await _seed_document(
                session,
                source_id=_src("relations"),
                document_id="doc-relations",
                revision_id="rev-ready",
                name="Relations Guide",
            )

        anchor = {"kind": "text", "start": 0, "end": 5}

        def node(node_id: str, label: str, degree: int) -> ProjectNode:
            return ProjectNode(
                node_id=node_id,
                label=label,
                node_type="ENTITY",
                canonical_key=label.lower(),
                degree=degree,
            )

        def edge(
            edge_id: str,
            source_node_id: str,
            target_node_id: str,
            relation_type: str,
            confidence: float,
        ) -> ProjectEdge:
            return ProjectEdge(
                edge_id=edge_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation_type=relation_type,
                relation_label=relation_type.lower(),
                origin=RelationOrigin.EXTRACTED,
                confidence=confidence,
            )

        def evidence(
            edge_id: str, chunk_id: str, source_revision_id: str = "rev-ready"
        ) -> EdgeEvidence:
            return EdgeEvidence(
                edge_id=edge_id,
                source_revision_id=source_revision_id,
                document_revision_id="doc-rev-relations",
                chunk_id=chunk_id,
                anchor=anchor,
                content_digest=DIGEST_A,
                fragment_snapshot_id="fragment-snapshot-1",
                fragment_edge_id=f"fragment-{edge_id}",
            )

        draft = ProjectGraphDraft(
            fragment_digest="sha256:" + "c" * 64,
            nodes=(
                node("node-a", "A", 2),
                node("node-b", "B", 2),
                node("node-c", "C", 1),
                node("node-d", "D", 1),
                node("node-e", "E", 3),
                node("node-f", "F", 3),
                node("node-g", "G", 10),
                node("node-h", "H", 10),
            ),
            edges=(
                # degree sum 4 * confidence 0.9 = 3.6 (highest)
                edge("edge-ab", "node-a", "node-b", "REQUIRES", 0.9),
                # degree sum 2 * confidence 0.95 = 1.9 (lowest)
                edge("edge-cd", "node-c", "node-d", "PRECEDES", 0.95),
                # degree sum 6 * confidence 0.5 = 3.0 (middle)
                edge("edge-ef", "node-e", "node-f", "USES", 0.5),
                # highest raw degree sum, but evidence sits only in an unready
                # source revision, so it must never appear.
                edge("edge-gh", "node-g", "node-h", "RELATED_TO", 1.0),
            ),
            node_sources=(),
            edge_evidence=(
                evidence("edge-ab", "chunk-ab"),
                evidence("edge-cd", "chunk-cd"),
                evidence("edge-ef", "chunk-ef"),
                evidence("edge-gh", "chunk-gh", source_revision_id="rev-unready"),
            ),
            aliases=(),
            communities=(),
            merge_log=(),
        )

        async with sessions() as session, session.begin():
            await publish_project_version(
                session, VALIDATION_SCOPE, draft, version=1, now=NOW.replace(tzinfo=None)
            )

        knowledge = KnowledgeSuggestionSources(
            sessions,
            scope=VALIDATION_SCOPE,
            project_graph=MysqlProjectGraphStore(sessions),
        )

        relations = await knowledge.main_relations(VALIDATION_SCOPE.actor_id, limit=20)

        assert [(r.subject, r.relation_type, r.object) for r in relations] == [
            ("A", "REQUIRES", "B"),
            ("E", "USES", "F"),
            ("C", "PRECEDES", "D"),
        ]

        limited = await knowledge.main_relations(VALIDATION_SCOPE.actor_id, limit=2)
        assert [(r.subject, r.relation_type, r.object) for r in limited] == [
            ("A", "REQUIRES", "B"),
            ("E", "USES", "F"),
        ]

    _run(owned_project_mysql, scenario)


def _turn_input(message: str, *, source_id: str) -> TurnInput:
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        resolved_resources=(
            FrozenResource(
                source_id=source_id,
                document_id=f"{source_id}-doc",
                revision_id=f"{source_id}-rev",
                source_content_hash=DIGEST_A,
            ),
        ),
        retrieval_policy_digest=DIGEST_A,
    )


async def _complete_turn(
    sessions, scope, *, conversation_id: str, turn_id: str, message: str, source_id: str
) -> None:
    service = ConversationService(MysqlConversationRepository(sessions, scope=scope), scope=scope)
    turn_input = _turn_input(message, source_id=source_id)
    await service.create(conversation_id, turn_id, turn_id + "-request", turn_input)
    await service.complete_for_test(
        conversation_id, turn_id, answer="ok", graph_status="NOT_REQUESTED"
    )


async def _queue_turn(
    sessions, scope, *, conversation_id: str, turn_id: str, message: str, source_id: str
) -> None:
    service = ConversationService(MysqlConversationRepository(sessions, scope=scope), scope=scope)
    turn_input = _turn_input(message, source_id=source_id)
    await service.create(conversation_id, turn_id, turn_id + "-request", turn_input)


def test_personal_usage_reads_only_completed_turns_of_actor(owned_project_mysql):
    async def scenario(sessions, engine):
        await _complete_turn(
            sessions,
            VALIDATION_SCOPE,
            conversation_id="conv-personal-done",
            turn_id="turn-done",
            message="What is the refund window?",
            source_id="src-personal-done",
        )
        await _queue_turn(
            sessions,
            VALIDATION_SCOPE,
            conversation_id="conv-personal-queued",
            turn_id="turn-queued",
            message="Still running question",
            source_id="src-personal-queued",
        )

        usage = MysqlSuggestionUsage(sessions, scope=VALIDATION_SCOPE)
        questions, source_ids = await usage.personal(VALIDATION_SCOPE.actor_id, limit=20)

        assert questions == ("What is the refund window?",)
        assert source_ids == ("src-personal-done",)

    _run(owned_project_mysql, scenario)


def test_popular_sources_count_other_actors_without_their_questions(owned_project_mysql):
    async def scenario(sessions, engine):
        await _insert_actor(engine, OTHER_SCOPE.actor_id)
        await _complete_turn(
            sessions,
            OTHER_SCOPE,
            conversation_id="conv-other",
            turn_id="turn-other",
            message="OTHER-USER-SECRET",
            source_id="src-popular",
        )
        await _complete_turn(
            sessions,
            VALIDATION_SCOPE,
            conversation_id="conv-self",
            turn_id="turn-self",
            message="My own question",
            source_id="src-self-only",
        )

        usage = MysqlSuggestionUsage(sessions, scope=VALIDATION_SCOPE)
        popular = await usage.popular_sources(
            excluding_actor_id=VALIDATION_SCOPE.actor_id, limit=10
        )

        assert popular == (("src-popular", 1),)

        own_questions, _ = await usage.personal(VALIDATION_SCOPE.actor_id, limit=20)
        assert "OTHER-USER-SECRET" not in own_questions

    _run(owned_project_mysql, scenario)


def test_ready_projection_requests_refresh_for_existing_readers(owned_project_mysql):
    async def scenario(sessions, engine):
        store = MysqlSuggestionStore(sessions, scope=VALIDATION_SCOPE)
        key = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="en")
        t0 = NOW
        t1 = NOW + timedelta(minutes=5)
        # Simulate an existing reader: a refresh already completed, so due_at
        # is cleared and only a later trigger (this knowledge publish) should
        # schedule the next one.
        await store.request_refresh(key, RefreshReason.MISSING, now=t0)
        (claim,) = await store.claim_due(
            worker_id="w1", now=t0, limit=10, lease_duration=timedelta(minutes=5)
        )
        await store.complete_refresh(claim, (), now=t0)

        projection = SuggestionReadyProjection(store)
        async with sessions() as session, session.begin():
            await projection.after_ready(
                session, VALIDATION_SCOPE, {}, now=t1, ingestion_job_id="job-1"
            )

        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            prompt_suggestion_refresh.c.due_at,
                            prompt_suggestion_refresh.c.last_reason,
                        ).where(
                            *scope_predicates(prompt_suggestion_refresh, VALIDATION_SCOPE),
                            prompt_suggestion_refresh.c.actor_id == key.actor_id,
                            prompt_suggestion_refresh.c.locale == key.locale,
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert row["due_at"] == t1.replace(tzinfo=None)
        assert row["last_reason"] == RefreshReason.KNOWLEDGE_PUBLISHED.value

    _run(owned_project_mysql, scenario)
