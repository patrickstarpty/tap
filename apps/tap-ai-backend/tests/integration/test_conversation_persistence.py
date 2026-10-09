import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationIntegrityError,
    ConversationService,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    FrozenResource,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
    citation_evidence_digest,
)
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.ports.answers import AnswerSnapshot, CitationSnapshot
from tests.integration.test_citation_snapshot_transaction import (
    ANCHOR_JSON,
    CHUNK_HASH,
    SOURCE_HASH,
    seed_ready,
)
from tests.owned_mysql import owned_project_database_url


def _input(message="Persist this", *, resolved_resources=()):
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        resolved_resources=resolved_resources,
        agent_revision_id="validation-knowledge-agent-v1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("validation-citation-skill-v1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )


def test_conversation_first_turn_restart_and_double_snapshot_are_durable(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owned = (
            "outbox",
            "turn_artifact_link",
            "turn_answer_evidence_snapshot",
            "turn_input_snapshot",
            "turn_snapshot",
            "chat_event",
            "chat_turn",
            "conversation",
        )
        async with engine.begin() as connection:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in owned:
                await connection.execute(text(f"DELETE FROM {table}"))
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        try:
            service = ConversationService(
                MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
                scope=VALIDATION_SCOPE,
            )
            accepted = await service.create("conversation-1", "turn-1", "request-1", _input())
            original_digest = accepted.input_snapshot.digest
            restarted = ConversationService(
                MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
                scope=VALIDATION_SCOPE,
            )
            loaded = await restarted.load("conversation-1")
            assert loaded.turns[0].input_snapshot.digest == original_digest
            completed = await restarted.complete_for_test(
                "conversation-1", "turn-1", answer="Grounded", graph_status="NOT_REQUESTED"
            )
            assert completed.answer_snapshot.input_snapshot_digest == original_digest
            queued = await restarted.append(
                "conversation-1", "turn-2", "request-2", _input("lease me")
            )
            assert queued.attempt == 0
            first_claim = (await restarted.repository.claim_queued(limit=1))[0][1]
            assert first_claim.attempt == 1 and first_claim.lease_token
            assert await restarted.repository.claim_queued(limit=1) == ()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE chat_turn SET processing_lease_expires_at=UTC_TIMESTAMP(6) "
                        "- INTERVAL 1 SECOND WHERE turn_id='turn-2'"
                    )
                )
            with pytest.raises(ConversationConflict, match="lease"):
                await restarted.complete_evidence(
                    "conversation-1",
                    "turn-2",
                    AnswerEvidence(
                        "expired",
                        "completed",
                        RetrievalSummary("completed"),
                        GraphContextStatus.NOT_REQUESTED,
                    ),
                    lease_token=first_claim.lease_token,
                )
            second_claim = (await restarted.repository.claim_queued(limit=1))[0][1]
            assert second_claim.attempt == 2
            assert second_claim.lease_token != first_claim.lease_token
            closed = AnswerEvidence(
                "recovered",
                "completed",
                RetrievalSummary("completed"),
                GraphContextStatus.NOT_REQUESTED,
            )
            with pytest.raises(ConversationConflict, match="lease"):
                await restarted.emit(
                    "conversation-1",
                    "turn-2",
                    "answer.delta",
                    {"text": "stale"},
                    lease_token=first_claim.lease_token,
                )
            await restarted.emit(
                "conversation-1",
                "turn-2",
                "answer.delta",
                {"text": "current"},
                lease_token=second_claim.lease_token,
            )
            canceled = await restarted.cancel("conversation-1", "turn-2")
            assert canceled.state == "canceled"
            stale_completion = await restarted.complete_evidence(
                "conversation-1",
                "turn-2",
                closed,
                lease_token=second_claim.lease_token,
            )
            assert stale_completion.state == "canceled"
            await restarted.append(
                "conversation-1", "turn-3", "request-3", _input("reject fake evidence")
            )
            with pytest.raises(ValueError, match="frozen Turn resources|trusted persisted fact"):
                await restarted.complete_evidence(
                    "conversation-1",
                    "turn-3",
                    AnswerEvidence(
                        "unsafe",
                        "completed",
                        RetrievalSummary("completed", trace_id="missing-trace"),
                        GraphContextStatus.NOT_REQUESTED,
                        citations=(CitationEvidence("missing", "sha256:" + "d" * 64),),
                    ),
                )
            async with engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            text("SELECT event_type, payload FROM chat_event ORDER BY sequence")
                        )
                    )
                    .mappings()
                    .all()
                )
                requested_payload = json.loads(rows[0]["payload"])
                completed_payload = json.loads(rows[1]["payload"])
                assert set(requested_payload) == {
                    "conversationId",
                    "turnId",
                    "inputSnapshotDigest",
                }
                assert set(completed_payload) == {
                    "turnId",
                    "answerEvidenceSnapshotId",
                    "answerEvidenceSnapshotDigest",
                    "outcome",
                }
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM outbox WHERE message_type IN "
                            "('conversation.turn.requested','conversation.turn.completed')"
                        )
                    )
                    == 5
                )
            await restarted.create("conversation-race", "turn-race", "request-race", _input("race"))
            race_claim = (await restarted.repository.claim_queued(limit=10))[-1][1]
            await asyncio.gather(
                restarted.cancel("conversation-race", "turn-race"),
                restarted.complete_evidence(
                    "conversation-race",
                    "turn-race",
                    closed,
                    lease_token=race_claim.lease_token,
                ),
            )
            race = await restarted.load("conversation-race")
            assert race.turns[0].state in {"completed", "canceled"}
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM turn_answer_evidence_snapshot "
                            "WHERE turn_id='turn-race'"
                        )
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM chat_event WHERE turn_id='turn-race' "
                            "AND event_type='conversation.turn.completed'"
                        )
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM project_audit WHERE action IN "
                            "('conversation-turn-requested','conversation-turn-completed')"
                        )
                    )
                    == 7
                )
        finally:
            async with engine.begin() as connection:
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                for table in owned:
                    await connection.execute(text(f"DELETE FROM {table}"))
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            await engine.dispose()

    asyncio.run(scenario())


def test_turn_without_input_snapshot_is_an_integrity_error(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            await service.create("snapshotless", "snapshotless-turn", "snapshotless", _input())
            async with engine.begin() as connection:
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                await connection.execute(
                    text("DELETE FROM turn_input_snapshot WHERE turn_id='snapshotless-turn'")
                )
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))

            with pytest.raises(ConversationIntegrityError, match="input snapshot is missing"):
                await service.load("snapshotless")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_graph_context_ready_stream_event_persists_and_reloads(owned_project_mysql):
    """PR 3 Task 7 review fix: a `graph.context_ready` stream event must
    persist end-to-end against real MySQL, not only against the in-memory
    repository fakes the worker unit tests use."""

    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            accepted = await service.create(
                "graph-context-conversation", "graph-context-turn", "request-1", _input()
            )
            claim = (await service.repository.claim_queued(limit=10))[-1][1]
            assert claim.turn_id == accepted.turn_id
            await service.complete_evidence(
                "graph-context-conversation",
                "graph-context-turn",
                AnswerEvidence(
                    "Grounded",
                    "completed",
                    RetrievalSummary("completed", trace_id="trace-1"),
                    GraphContextStatus.APPLIED,
                    graph_snapshot_id="7",
                ),
                lease_token=claim.lease_token,
                terminal_event=("turn.completed", {"answer": {"answer": "Grounded"}}),
                stream_events=(
                    (
                        "graph.context_ready",
                        {
                            "status": "APPLIED",
                            "graphVersion": "7",
                            "seedCount": 2,
                            "paths": [["核保流程", "健康告知"]],
                            "relationCount": 1,
                        },
                    ),
                ),
            )
            detail = await service.load("graph-context-conversation")
            graph_context_ready_events = [
                event for event in detail.events if event.event_type == "graph.context_ready"
            ]
            assert len(graph_context_ready_events) == 1
            assert graph_context_ready_events[0].payload["graphVersion"] == "7"
            async with engine.connect() as connection:
                stored_payload = json.loads(
                    await connection.scalar(
                        text(
                            "SELECT payload FROM chat_event WHERE turn_id=:turn_id "
                            "AND event_type='graph.context_ready'"
                        ),
                        {"turn_id": "graph-context-turn"},
                    )
                )
                assert stored_payload["status"] == "APPLIED"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_claim_fails_snapshotless_queued_turn_and_keeps_claiming_others(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            await service.create("broken", "broken-turn", "broken", _input("Broken"))
            await service.create("healthy", "healthy-turn", "healthy", _input("Healthy"))
            async with engine.begin() as connection:
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                await connection.execute(
                    text("DELETE FROM turn_input_snapshot WHERE turn_id='broken-turn'")
                )
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))

            claimed = await repository.claim_queued(limit=5)

            assert [(chat_id, turn.turn_id) for chat_id, turn in claimed] == [
                ("healthy", "healthy-turn")
            ]
            assert await repository.claim_queued(limit=5) == ()
            async with engine.connect() as connection:
                state = await connection.scalar(
                    text("SELECT state FROM chat_turn WHERE turn_id='broken-turn'")
                )
                lease = await connection.scalar(
                    text("SELECT processing_lease_token FROM chat_turn WHERE turn_id='broken-turn'")
                )
                events = (
                    await connection.execute(
                        text(
                            "SELECT event_type, payload FROM chat_event "
                            "WHERE turn_id='broken-turn' ORDER BY sequence"
                        )
                    )
                ).all()
            assert state == "failed"
            assert lease is None
            assert events[-1][0] == "turn.failed"
            payload = events[-1][1]
            payload = json.loads(payload) if isinstance(payload, str) else payload
            assert payload["problem"]["type"].endswith("/conversation-integrity")
            assert payload["problem"]["retryable"] is False
            assert "turn.started" not in [event_type for event_type, _ in events]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_applied_graph_context_validates_edge_citation_graph_version(owned_project_mysql):
    """PR 3 Task 8 preflight ruling: when an answer's graph context is
    APPLIED, every edge citation's persisted `graph_version` must equal the
    Turn's `graph_snapshot_id` (which, per the plan's
    `graph_snapshot_id = graph_version` design, is the same string) or
    `complete()` must raise -- a stale or forged edge citation must not ride
    along with a graph-grounded answer. This checks the persisted
    `knowledge_citation_snapshot.graph_version` column, never
    `graph_project_version` (pruned old versions must not block an answer)."""

    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            document_repository = MysqlDocumentRepository(
                sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
            )
            selected = await seed_ready(engine, "graph")
            edge_citation = CitationSnapshot(
                trace_id="trace-graph",
                citation_id="citation-graph-edge",
                document_id=selected.document_id,
                revision_id=selected.revision_id,
                chunk_id="h_graph",
                source_content_hash=SOURCE_HASH,
                chunk_content_hash=CHUNK_HASH,
                anchor_json=ANCHOR_JSON,
                citation_kind="edge",
                graph_version="7",
                edge_id="edge-1",
                subject_node_id="node-subject",
                object_node_id="node-object",
                relation_type="REQUIRES",
                relation_label="requires",
            )
            await document_repository.save_answer_with_citations(
                AnswerSnapshot(
                    trace_id="trace-graph",
                    query_hash="sha256:" + "c" * 64,
                    selected_revisions=(selected,),
                    citations=(edge_citation,),
                )
            )
            async with engine.connect() as connection:
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT citation_id,source_id,trace_id,document_id,"
                                "revision_id,chunk_id,source_content_hash,chunk_content_"
                                "hash,anchor_json,claim_text,origin,citation_kind,"
                                "graph_version,edge_id,subject_node_id,object_node_id,"
                                "relation_type,relation_label FROM knowledge_citati"
                                "on_snapshot WHERE citation_id='citation-graph-edge'"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            digest = citation_evidence_digest(
                citation_id=row["citation_id"],
                trace_id=row["trace_id"],
                source_id=row["source_id"],
                document_id=row["document_id"],
                revision_id=row["revision_id"],
                chunk_id=row["chunk_id"],
                source_content_hash=row["source_content_hash"],
                chunk_content_hash=row["chunk_content_hash"],
                anchor=row["anchor_json"],
                claim_text=row["claim_text"],
                origin=row["origin"],
                citation_kind=row["citation_kind"],
                graph_version=row["graph_version"],
                edge_id=row["edge_id"],
                subject_node_id=row["subject_node_id"],
                object_node_id=row["object_node_id"],
                relation_type=row["relation_type"],
                relation_label=row["relation_label"],
            )
            resolved_resources = (
                FrozenResource(
                    source_id=selected.source_id,
                    document_id=selected.document_id,
                    revision_id=selected.revision_id,
                    source_content_hash=SOURCE_HASH,
                ),
            )
            service = ConversationService(
                MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
                scope=VALIDATION_SCOPE,
            )

            # Matching graph_snapshot_id ("7" == the citation row's graph_version): completes.
            await service.create(
                "conversation-graph-match",
                "turn-graph-match",
                "request-graph-match",
                _input(resolved_resources=resolved_resources),
            )
            match_claim = (await service.repository.claim_queued(limit=10))[-1][1]
            completed = await service.complete_evidence(
                "conversation-graph-match",
                "turn-graph-match",
                AnswerEvidence(
                    "Grounded",
                    "completed",
                    RetrievalSummary("completed", trace_id="trace-graph"),
                    GraphContextStatus.APPLIED,
                    graph_snapshot_id="7",
                    citations=(CitationEvidence("citation-graph-edge", digest),),
                ),
                lease_token=match_claim.lease_token,
                terminal_event=("turn.completed", {"answer": {"answer": "Grounded"}}),
            )
            assert completed.state == "completed"

            # Mismatched graph_snapshot_id ("8" != the citation row's graph_version "7"): rejected.
            await service.create(
                "conversation-graph-mismatch",
                "turn-graph-mismatch",
                "request-graph-mismatch",
                _input(resolved_resources=resolved_resources),
            )
            mismatch_claim = (await service.repository.claim_queued(limit=10))[-1][1]
            with pytest.raises(ValueError, match="graph version"):
                await service.complete_evidence(
                    "conversation-graph-mismatch",
                    "turn-graph-mismatch",
                    AnswerEvidence(
                        "Grounded",
                        "completed",
                        RetrievalSummary("completed", trace_id="trace-graph"),
                        GraphContextStatus.APPLIED,
                        graph_snapshot_id="8",
                        citations=(CitationEvidence("citation-graph-edge", digest),),
                    ),
                    lease_token=mismatch_claim.lease_token,
                    terminal_event=("turn.completed", {"answer": {"answer": "Grounded"}}),
                )

            # Non-APPLIED graph context (here EMPTY) citing an edge-kind
            # citation row: rejected regardless of graph_version, since an
            # edge citation requires an APPLIED graph context on this Turn.
            await service.create(
                "conversation-graph-not-applied",
                "turn-graph-not-applied",
                "request-graph-not-applied",
                _input(resolved_resources=resolved_resources),
            )
            not_applied_claim = (await service.repository.claim_queued(limit=10))[-1][1]
            with pytest.raises(ValueError, match="APPLIED graph context"):
                await service.complete_evidence(
                    "conversation-graph-not-applied",
                    "turn-graph-not-applied",
                    AnswerEvidence(
                        "Grounded",
                        "completed",
                        RetrievalSummary("completed", trace_id="trace-graph"),
                        GraphContextStatus.EMPTY,
                        graph_snapshot_id=None,
                        citations=(CitationEvidence("citation-graph-edge", digest),),
                    ),
                    lease_token=not_applied_claim.lease_token,
                    terminal_event=("turn.completed", {"answer": {"answer": "Grounded"}}),
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resolve_citations_then_complete_evidence_round_trips_an_edge_citation(
    owned_project_mysql,
):
    """Fix round 2 CRITICAL 1 regression test: `resolve_citations()` (the
    generation worker's own call, `tapper_generation_worker.py:204`) and
    `complete()` must compute the exact same trusted digest for a persisted
    edge citation -- this test never hand-computes the digest itself (unlike
    `test_applied_graph_context_validates_edge_citation_graph_version`), it
    only uses whatever `resolve_citations()` returns, so a drift between the
    two call sites' SELECT columns or digest material fails here the same way
    it fails in production."""

    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            document_repository = MysqlDocumentRepository(
                sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
            )
            selected = await seed_ready(engine, "roundtrip")
            edge_citation = CitationSnapshot(
                trace_id="trace-roundtrip",
                citation_id="citation-roundtrip-edge",
                document_id=selected.document_id,
                revision_id=selected.revision_id,
                chunk_id="h_roundtrip",
                source_content_hash=SOURCE_HASH,
                chunk_content_hash=CHUNK_HASH,
                anchor_json=ANCHOR_JSON,
                citation_kind="edge",
                graph_version="7",
                edge_id="edge-1",
                subject_node_id="node-subject",
                object_node_id="node-object",
                relation_type="REQUIRES",
                relation_label="requires",
            )
            await document_repository.save_answer_with_citations(
                AnswerSnapshot(
                    trace_id="trace-roundtrip",
                    query_hash="sha256:" + "c" * 64,
                    selected_revisions=(selected,),
                    citations=(edge_citation,),
                )
            )

            conversation_repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            resolved = await conversation_repository.resolve_citations(
                "trace-roundtrip", ("citation-roundtrip-edge",)
            )
            assert len(resolved) == 1
            assert resolved[0].citation_snapshot_id == "citation-roundtrip-edge"

            resolved_resources = (
                FrozenResource(
                    source_id=selected.source_id,
                    document_id=selected.document_id,
                    revision_id=selected.revision_id,
                    source_content_hash=SOURCE_HASH,
                ),
            )
            service = ConversationService(conversation_repository, scope=VALIDATION_SCOPE)
            await service.create(
                "conversation-roundtrip",
                "turn-roundtrip",
                "request-roundtrip",
                _input(resolved_resources=resolved_resources),
            )
            claim = (await service.repository.claim_queued(limit=10))[-1][1]
            completed = await service.complete_evidence(
                "conversation-roundtrip",
                "turn-roundtrip",
                AnswerEvidence(
                    "Grounded",
                    "completed",
                    RetrievalSummary("completed", trace_id="trace-roundtrip"),
                    GraphContextStatus.APPLIED,
                    graph_snapshot_id="7",
                    citations=resolved,
                ),
                lease_token=claim.lease_token,
                terminal_event=("turn.completed", {"answer": {"answer": "Grounded"}}),
            )
            assert completed.state == "completed"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
