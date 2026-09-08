import asyncio
import json

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import ConversationConflict, ConversationService
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)


def _input(message="Persist this"):
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="tapper-chat",
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
                await restarted.complete_evidence(
                    "conversation-1",
                    "turn-2",
                    closed,
                    lease_token=first_claim.lease_token,
                )
            await restarted.complete_evidence(
                "conversation-1",
                "turn-2",
                closed,
                lease_token=second_claim.lease_token,
            )
            await restarted.append(
                "conversation-1", "turn-3", "request-3", _input("reject fake evidence")
            )
            with pytest.raises(ValueError, match="trusted persisted fact"):
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
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM project_audit WHERE action IN "
                            "('conversation-turn-requested','conversation-turn-completed')"
                        )
                    )
                    == 5
                )
        finally:
            async with engine.begin() as connection:
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                for table in owned:
                    await connection.execute(text(f"DELETE FROM {table}"))
                await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            await engine.dispose()

    asyncio.run(scenario())
