import asyncio
from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import (
    MysqlConversationRepository,
    chat_relation_citation,
)
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    FrozenResource,
    GraphContextStatus,
    RelationCitationEvidence,
    RetrievalSummary,
    TurnInput,
)
from tap.modules.graph.adapters.mysql import MysqlGraphStore
from tap.modules.knowledge.adapters.mysql_documents import knowledge_citation_snapshot
from tap.platform.db.project_scope import scope_values
from tests.integration.test_mysql_graph_store import _draft
from tests.owned_mysql import owned_project_database_url


def test_relation_citations_finalize_atomically_and_survive_restart(owned_project_mysql):
    async def scenario():
        engine = create_async_engine(owned_project_database_url(owned_project_mysql))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await MysqlGraphStore(sessions).publish(VALIDATION_SCOPE, _draft("s"))
            source_hash, chunk_hash = "sha256:" + "a" * 64, "sha256:" + "b" * 64
            anchor = {"type": "document", "startOffset": 0, "endOffset": 8, "headingPath": []}
            claim = "Policy governs Claim."
            # Seed an immutable original-text citation; its authority and digest are checked
            # by real finalization below. Only fixture construction skips unrelated ledgers.
            values = dict(
                citation_id="c",
                source_id="source",
                trace_id="trace",
                document_id="doc",
                revision_id="document-revision-1",
                chunk_id="chunk-1",
                source_content_hash=source_hash,
                chunk_content_hash=chunk_hash,
                anchor_json=anchor,
                claim_text=claim,
                origin="SOURCE",
                created_at=datetime.now(),
            )
            async with engine.begin() as connection:
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                await connection.execute(
                    insert(knowledge_citation_snapshot).values(
                        **scope_values(VALIDATION_SCOPE), **values
                    )
                )
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            frozen = TurnInput(
                message="What is the relationship?",
                actor_id=VALIDATION_SCOPE.actor_id,
                identity_mode="validation",
                model_alias="qwen-plus",
                source_revision_ids=("source-revision-1",),
                # Browser source selections omit the separate legacy document array.
                # Authoritative resolution below still freezes its document revision.
                document_revision_ids=(),
                resolved_resources=(
                    FrozenResource(
                        "source",
                        "doc",
                        "document-revision-1",
                        source_hash,
                        "source-revision-1",
                        "document-revision-1",
                    ),
                ),
            )
            service = ConversationService(
                MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
                scope=VALIDATION_SCOPE,
            )
            await service.create("chat", "turn", "request", frozen)
            citations = await service.repository.resolve_citations("trace", ("c",))
            lease = (await service.repository.claim_queued(limit=1))[0][1].lease_token
            relation = RelationCitationEvidence(
                "r",
                "s",
                "edge-1",
                "node-1",
                "node-2",
                "Policy",
                "Claim",
                "GOVERNS",
                "EXTRACTED",
                0,
                ("edge-1",),
                ("c",),
            )
            evidence = AnswerEvidence(
                claim,
                "completed",
                RetrievalSummary("completed", "trace", 1),
                GraphContextStatus.APPLIED,
                "s",
                citations,
                relation_citations=(relation,),
            )
            with pytest.raises(ValueError):
                await service.complete_evidence(
                    "chat",
                    "turn",
                    replace(
                        evidence, relation_citations=(replace(relation, relation_type="INVENTED"),)
                    ),
                    lease_token=lease,
                )
            async with sessions() as session:
                assert (await session.execute(select(chat_relation_citation))).all() == []
            await service.complete_evidence("chat", "turn", evidence, lease_token=lease)
            restarted = ConversationService(
                MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
                scope=VALIDATION_SCOPE,
            )
            loaded = (await restarted.load("chat")).turns[0].answer_snapshot
            assert loaded.value.relation_citations == (relation,)
            async with sessions() as session:
                row = (await session.execute(select(chat_relation_citation))).mappings().one()
                assert row["claim_index"] == 0
                assert row["path_edge_ids"] == ["edge-1"]
                assert row["original_citation_ids"] == ["c"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
