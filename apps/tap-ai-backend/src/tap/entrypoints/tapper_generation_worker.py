"""Provider-neutral generation worker for durable Conversation turns."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.trace import Span

from tap.contracts.problems import build_problem
from tap.entrypoints.tapper_chat_turn import (
    ChatTurnNodes,
    _evidence_from_checkpoint,
    answer_steps,
)
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.chat_graph import (
    CHAT_GRAPH_VERSION,
    CHAT_STATE_SCHEMA_VERSION,
    ChatGraph,
)
from tap.modules.ai.domain.graph_runs import GraphCheckpointRetryable, GraphCheckpointUnavailable
from tap.modules.ai.domain.models import ModelGatewayRejected, ModelGatewayUnavailable
from tap.modules.ai.ports.insights import (
    InsightsAuthorizationChanged,
    InsightsBudgetExceeded,
    InsightsQueryUnavailable,
)
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationNotFound,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    GraphContextStatus,
    RetrievalSummary,
)
from tap.modules.chat.domain.suggestions import RefreshReason
from tap.platform.telemetry import (
    bind_trace,
    extract_traceparent,
    flush_traces,
    span,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class GenerationWorker:
    conversations: Any
    knowledge: Any
    checkpointer: BaseCheckpointSaver | None = None
    insights_explanation: Any | None = None
    suggestion_refresh: Any | None = None
    lease_duration: timedelta = timedelta(seconds=60)
    renew_interval_seconds: float = 20.0
    max_checkpoint_attempts: int = 3

    def __post_init__(self) -> None:
        if (
            not timedelta(0) < self.lease_duration <= timedelta(minutes=15)
            or not 0 < self.renew_interval_seconds < self.lease_duration.total_seconds()
            or type(self.max_checkpoint_attempts) is not int
            or not 1 <= self.max_checkpoint_attempts <= 100
        ):
            raise ValueError("generation worker lease or checkpoint retry configuration is invalid")

    async def run_once(self, *, limit: int) -> int:
        claimed = await self.conversations.repository.claim_queued(limit=limit)
        for conversation_id, turn in claimed:
            try:
                with bind_trace(
                    scope=getattr(self.conversations, "scope", None),
                    turn_id=turn.turn_id,
                    attempt=getattr(turn, "attempt", None),
                    fresh_usage=True,
                ):
                    with span(
                        "turn.execute",
                        context=extract_traceparent(getattr(turn, "traceparent", None)),
                    ) as turn_span:
                        await self._process_claimed_turn(conversation_id, turn, turn_span)
            finally:
                await flush_traces()
        return len(claimed)

    async def _process_claimed_turn(self, conversation_id: str, turn: Any, turn_span: Span) -> None:
        insights_query_id = getattr(turn.input_snapshot.value, "insights_query_id", None)
        planner = getattr(self.knowledge, "answer_planner", None)
        renew = getattr(self.conversations.repository, "renew_processing_lease", None)

        async def fail_turn() -> None:
            await self.conversations.complete_evidence(
                conversation_id,
                turn.turn_id,
                AnswerEvidence(
                    "",
                    "failed",
                    RetrievalSummary("failed"),
                    GraphContextStatus.FAILED,
                ),
                lease_token=turn.lease_token,
                terminal_event=(
                    "turn.failed",
                    {
                        "problem": build_problem(
                            "answer-unavailable", correlation_id=turn.turn_id
                        ).model_dump(mode="json", by_alias=True)
                    },
                ),
            )

        outcome = "deferred"
        try:

            async def authorized() -> bool:
                if renew is None:
                    return True
                try:
                    await renew(
                        conversation_id,
                        turn.turn_id,
                        turn.lease_token,
                        lease_duration=self.lease_duration,
                    )
                except ConversationConflict:
                    return False
                return True

            async def require_authorized():
                if not await authorized():
                    raise PermissionError("answer plan authorization changed")

            checkpointer_factory = getattr(
                self.conversations.repository, "graph_checkpointer", None
            )
            checkpointer = (
                checkpointer_factory(turn)
                if self.checkpointer is None and checkpointer_factory is not None
                else self.checkpointer or InMemorySaver()
            )
            nodes = ChatTurnNodes(
                conversation_id=conversation_id,
                turn=turn,
                conversations=self.conversations,
                steps=answer_steps(self.knowledge),
                planner=planner,
                knowledge=self.knowledge,
                insights_explanation=self.insights_explanation,
                require_authorized=require_authorized,
                turn_span=turn_span,
            ).nodes
            graph = ChatGraph(
                checkpointer=checkpointer,
                nodes=nodes,
                authorize=authorized,
                propagate=(
                    ConversationConflict,
                    ConversationNotFound,
                    GraphCheckpointRetryable,
                    GraphCheckpointUnavailable,
                ),
            )

            async def run_graph():
                stored = await graph.stored_version(run_id=turn.turn_id)
                if stored is not None and stored != (CHAT_GRAPH_VERSION, CHAT_STATE_SCHEMA_VERSION):
                    await graph.discard(run_id=turn.turn_id)
                    return await graph.start(
                        run_id=turn.turn_id,
                        payload={"conversationId": conversation_id, "turnId": turn.turn_id},
                        route="insights" if insights_query_id is not None else "chat",
                    )
                if await graph.has_checkpoint(run_id=turn.turn_id):
                    return await graph.resume(run_id=turn.turn_id)
                return await graph.start(
                    run_id=turn.turn_id,
                    payload={"conversationId": conversation_id, "turnId": turn.turn_id},
                    route="insights" if insights_query_id is not None else "chat",
                )

            running = asyncio.create_task(run_graph())
            try:
                while True:
                    done, _ = await asyncio.wait({running}, timeout=self.renew_interval_seconds)
                    if done:
                        state = await running
                        break
                    if renew is not None:
                        await renew(
                            conversation_id,
                            turn.turn_id,
                            turn.lease_token,
                            lease_duration=self.lease_duration,
                        )
            except BaseException:
                if not running.done():
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
                raise
            result = state.get("result", {})
            evidence = _evidence_from_checkpoint(result.get("evidence"))
            if insights_query_id is not None and evidence.outcome == "completed":
                if self.insights_explanation is None or evidence.insights_explanation is None:
                    raise InsightsAuthorizationChanged("Insights result was not persisted")
                await asyncio.wait_for(
                    self.insights_explanation.reauthorize_result(
                        self.conversations.scope,
                        insights_query_id,
                        turn.input_snapshot.value.insights_report_refs,
                        turn.input_snapshot.value.resolved_resources,
                        evidence.insights_explanation,
                    ),
                    timeout=20,
                )
            if evidence.outcome not in {"failed", "canceled"}:
                await require_authorized()
                authorize_completed = getattr(self.knowledge, "authorize_completed_answer", None)
                if authorize_completed is not None and insights_query_id is None:
                    await authorize_completed(turn.input_snapshot, evidence)
            raw_terminal = result.get("terminalEvent")
            raw_stream_events = result.get("streamEvents", ())
            terminal_event = (
                None
                if raw_terminal is None
                else (str(raw_terminal["type"]), dict(raw_terminal["payload"]))
            )
            persisted_stream_events = tuple(
                (str(item["type"]), dict(item["payload"])) for item in raw_stream_events
            )
            await self.conversations.complete_evidence(
                conversation_id,
                turn.turn_id,
                evidence,
                lease_token=turn.lease_token,
                terminal_event=terminal_event,
                stream_events=persisted_stream_events,
            )
            if self.suggestion_refresh is not None and evidence.outcome == "completed":
                actor_id = getattr(turn.input_snapshot.value, "actor_id", None)
                if actor_id:
                    try:
                        await self.suggestion_refresh.request_refresh_for_actor(
                            actor_id, RefreshReason.TURN_COMPLETED, now=datetime.now(UTC)
                        )
                    except Exception as exc:  # noqa: BLE001 - must never fail a completed turn
                        _LOGGER.warning(
                            "prompt suggestion refresh request failed: %s",
                            type(exc).__name__,
                        )
            outcome = evidence.outcome
        except InsightsAuthorizationChanged:
            if insights_query_id is None:
                raise
            outcome = "failed"
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                return
        except PermissionError:
            # The graph fence proves another worker owns the Turn.
            return
        except ConversationNotFound:
            # The owner deleted the Conversation; its Turn must not be completed.
            return
        except ConversationConflict:
            if turn.attempt < self.max_checkpoint_attempts:
                return
            outcome = "failed"
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                # Token and expiry checks prevent a real loser from failing the Turn.
                return
        except GraphCheckpointRetryable:
            if turn.attempt < self.max_checkpoint_attempts:
                return
            outcome = "failed"
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                return
        except GraphCheckpointUnavailable:
            outcome = "failed"
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                return
        except AuthorizationDenied:
            outcome = "failed"
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                # Cancellation or lease reclaim won the terminal-state race.
                return
        except (
            InsightsBudgetExceeded,
            InsightsQueryUnavailable,
            ModelGatewayRejected,
            ModelGatewayUnavailable,
            RuntimeError,
            TimeoutError,
        ):
            if insights_query_id is None:
                raise
            outcome = "failed"
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                return
        finally:
            turn_span.set_attribute("tap.outcome", outcome)


async def main() -> None:
    from tap.entrypoints.tapper_runtime import TapperSettings, create_api_runtime
    from tap.entrypoints.tracing_setup import start_tracing

    settings = TapperSettings.from_mapping(dict(os.environ))
    start_tracing("tap-ai-worker-generation", settings)
    runtime = await create_api_runtime(settings)
    conversations = runtime.http_services.conversations
    knowledge = runtime.http_services.knowledge
    if conversations is None or knowledge is None:
        await runtime.aclose()
        raise RuntimeError("generation worker composition is unavailable")
    worker = GenerationWorker(
        conversations,
        knowledge,
        insights_explanation=runtime.http_services.insights_explanation,
        suggestion_refresh=runtime.http_services.prompt_suggestion_store,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(event, stop.set)
        except NotImplementedError:
            pass
    try:
        while not stop.is_set():
            processed = await worker.run_once(limit=settings.job_batch_size)
            if processed == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    asyncio.run(main())
