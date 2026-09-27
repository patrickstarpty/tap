"""Insights uses the real Turn worker, durable graph and atomic terminal settlement."""

from dataclasses import replace

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_checkpointer import MysqlGraphCheckpointer, graph_run
from tap.modules.ai.domain.graph_runs import GraphCheckpointRetryable
from tap.modules.ai.ports.insights import InsightsAuthorizationChanged
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository, chat_turn
from tap.modules.chat.application.conversations import ConversationService
from tap.platform.db.schema import outbox
from tests.integration.test_conversation_persistence import _input


class _NoOrdinaryAnswer:
    async def answer(self, _request):
        pytest.fail("Insights must not fall through to ordinary Knowledge Chat")


class _Explanation:
    def __init__(self):
        self.calls = []
        self.allowed = True
        self.authorizations = []

    async def reauthorize_result(self, scope, query_id, resource_refs, selection, result):
        self.authorizations.append((scope, query_id, resource_refs, selection, result))
        if not self.allowed:
            raise InsightsAuthorizationChanged("publication withdrawn before result settlement")

    async def explain(self, scope, query_id, resource_refs, question, **binding):
        self.calls.append((scope, query_id, resource_refs, question, binding))
        return {
            "queryId": query_id,
            "metricVersion": "metrics-v1",
            "asOf": "2026-09-27T00:00:00Z",
            "facts": [],
            "reportCoverage": [],
            "hypotheses": ["The timeout may reflect a slow upstream dependency. [report-1]"],
            "evidenceExcerpts": [{"citationId": "report-1", "text": "upstream timeout"}],
            "missingInformation": ["Provide the upstream latency logs."],
            "stopReason": "completed",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interrupt_after_checkpoint", "withdraw_before_reclaim"),
    [(False, False), (True, False), (True, True)],
)
async def test_insights_turn_reload_and_checkpoint_reclaim_preserve_result_and_actual_ids(
    owned_project_mysql, monkeypatch, interrupt_after_checkpoint, withdraw_before_reclaim
):
    interrupted = False
    original = MysqlGraphCheckpointer.aput_writes

    async def interrupt(self, config, writes, task_id, task_path=""):
        nonlocal interrupted
        await original(self, config, writes, task_id, task_path)
        if (
            interrupt_after_checkpoint
            and not interrupted
            and any(channel == "result" for channel, _ in writes)
        ):
            interrupted = True
            raise GraphCheckpointRetryable("connection lost after durable Insights result")

    monkeypatch.setattr(MysqlGraphCheckpointer, "aput_writes", interrupt)
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    conversation_id = "insights-recovery-chat"
    turn_id = "insights-recovery-turn"
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
        )
        await conversations.create(
            conversation_id,
            turn_id,
            "insights-recovery-request",
            replace(
                _input("Explain the timeout"),
                insights_query_id="query-recovery",
                insights_report_refs=("report-1",),
            ),
        )
        explanation = _Explanation()
        worker = GenerationWorker(
            conversations, _NoOrdinaryAnswer(), insights_explanation=explanation
        )
        assert await worker.run_once(limit=1) == 1
        if interrupt_after_checkpoint:
            pending = (await conversations.load(conversation_id)).turns[0]
            assert pending.state == "running"
            async with sessions() as session, session.begin():
                await session.execute(
                    update(chat_turn)
                    .where(chat_turn.c.turn_id == turn_id)
                    .values(
                        processing_lease_expires_at=func.utc_timestamp() - text("INTERVAL 1 SECOND")
                    )
                )
                await session.execute(
                    update(graph_run)
                    .where(graph_run.c.run_id == turn_id)
                    .values(lease_until=func.utc_timestamp() - text("INTERVAL 1 SECOND"))
                )
            restarted = ConversationService(
                MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
                scope=VALIDATION_SCOPE,
            )
            explanation.allowed = not withdraw_before_reclaim
            assert (
                await GenerationWorker(
                    restarted, _NoOrdinaryAnswer(), insights_explanation=explanation
                ).run_once(limit=1)
                == 1
            )

        loaded = await ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
        ).load(conversation_id)
        turn = loaded.turns[0]
        assert len(explanation.calls) == 1
        assert explanation.authorizations
        if withdraw_before_reclaim:
            assert turn.state == "failed"
            assert turn.answer_snapshot.value.insights_explanation is None
            assert not any(event.event_type == "turn.completed" for event in loaded.events)
            assert await worker.run_once(limit=1) == 0
            return
        assert turn.state == "completed"
        scope, query_id, refs, question, binding = explanation.calls[0]
        assert scope == VALIDATION_SCOPE
        assert (query_id, refs, question) == (
            "query-recovery",
            ("report-1",),
            "Explain the timeout",
        )
        assert binding["conversation_id"] == conversation_id
        assert binding["turn_id"] == turn_id
        assert turn.input_snapshot.value.insights_query_id == query_id
        result = turn.answer_snapshot.value.insights_explanation
        assert result["hypotheses"] == [
            "The timeout may reflect a slow upstream dependency. [report-1]"
        ]
        assert result["missingInformation"] == ["Provide the upstream latency logs."]
        assert result["queryId"] == query_id
        completed_events = [
            event for event in loaded.events if event.event_type == "turn.completed"
        ]
        assert len(completed_events) == 1
        audit = completed_events[0].payload["insightsAudit"]
        assert audit == {
            "conversationId": conversation_id,
            "turnId": turn_id,
            "graphRunId": turn_id,
            "tool": "insights.query",
            "queryId": query_id,
            "metricVersion": "metrics-v1",
            "resourceRefs": ["report-1"],
            "knowledgeSearchPerformed": False,
            "knowledgeSources": [],
            "knowledgeCitations": [],
        }
        async with sessions() as session:
            run = (
                (await session.execute(select(graph_run).where(graph_run.c.run_id == turn_id)))
                .mappings()
                .one()
            )
            assert run["status"] == "SUCCEEDED"
            assert run["graph_version"] == "insights-explanation-v1"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(outbox)
                    .where(
                        outbox.c.aggregate_id == turn_id,
                        outbox.c.message_type == "conversation.turn.completed",
                    )
                )
                == 1
            )
        assert await worker.run_once(limit=1) == 0
        assert len(explanation.calls) == 1
    finally:
        await engine.dispose()
