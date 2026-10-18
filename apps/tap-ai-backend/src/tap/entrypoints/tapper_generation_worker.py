"""Provider-neutral generation worker for durable Conversation turns."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.trace import Span, Status, StatusCode

from tap.contracts.problems import build_problem
from tap.entrypoints.tapper_chat_turn import (
    ChatTurnNodes,
    _evidence_from_checkpoint,
    answer_steps,
)
from tap.entrypoints.tapper_turn_failures import classify_turn_failure, retry_delay
from tap.modules.ai.application.chat_graph import (
    CHAT_GRAPH_VERSION,
    CHAT_STATE_SCHEMA_VERSION,
    ChatGraph,
)
from tap.modules.ai.ports.insights import InsightsAuthorizationChanged
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationNotFound,
)
from tap.modules.chat.application.ports import DraftStreamUnavailable
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    GraphContextStatus,
    RetrievalSummary,
)
from tap.modules.chat.domain.suggestions import RefreshReason
from tap.platform.budget import TurnBudget
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
    max_turn_attempts: int = 3
    rng: random.Random | None = None
    turn_budget: TurnBudget = TurnBudget()
    # PR 3: the shared Redis draft stream (`None` degrades the `model` node's
    # `DraftBatcher` to accumulate-only) and the conversation-path
    # personal-data (v2) egress redactor every draft chunk is sent through --
    # the same redactor `KnowledgeHttpService` uses for answer finalization.
    drafts: Any | None = None
    redact: Callable[[str], Awaitable[str]] | None = None

    def __post_init__(self) -> None:
        if (
            not timedelta(0) < self.lease_duration <= timedelta(minutes=15)
            or not 0 < self.renew_interval_seconds < self.lease_duration.total_seconds()
            or type(self.max_turn_attempts) is not int
            or not 1 <= self.max_turn_attempts <= 10
        ):
            raise ValueError("generation worker lease or turn retry configuration is invalid")

    async def run_once(self, *, limit: int) -> int:
        claimed = await self.conversations.repository.claim_queued(limit=limit)
        # `claim_queued` writes `turn.started` for every claimed Turn, and may
        # also settle permanently-faulted Turns (`turn.failed`) inline; both
        # bypass `emit`/`complete_evidence`, so notify here instead -- never
        # inside the repository (see `claim_queued`'s docstring). Fired
        # concurrently so a blackholed Redis serializes at most one socket
        # timeout, not one per wake-up.
        wakeups = claimed.wakeups
        if wakeups:
            await asyncio.gather(
                *(
                    self.conversations.notify_external_write(conversation_id, sequence)
                    for conversation_id, sequence in wakeups
                )
            )
        for conversation_id, turn in claimed:
            try:
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
                except Exception:
                    _LOGGER.exception(
                        "unhandled error processing turn %s", getattr(turn, "turn_id", None)
                    )
            finally:
                await flush_traces()
        return len(claimed)

    async def _process_claimed_turn(self, conversation_id: str, turn: Any, turn_span: Span) -> None:
        attempt_started_at = time.time()
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
                checkpointer_factory(turn, budget=self.turn_budget.to_dict())
                if self.checkpointer is None and checkpointer_factory is not None
                else self.checkpointer or InMemorySaver()
            )
            turn_retryable = lambda error: classify_turn_failure(error) == "transient"  # noqa: E731
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
                attempt_started_at=attempt_started_at,
                retryable=turn_retryable,
                drafts=self.drafts,
                redact=self.redact,
            ).nodes

            async def canceled() -> bool:
                """Fast, Redis-backed node-boundary check (Task 7); any
                Redis failure degrades to "not canceled" here -- the
                `authorize`/`renew_processing_lease` check above still
                stops the Turn at the next node boundary from MySQL alone
                once `cancel` has written the Turn's `canceled` state."""
                if self.drafts is None:
                    return False
                try:
                    return await self.drafts.cancel_requested(turn.turn_id)
                except DraftStreamUnavailable:
                    return False

            graph = ChatGraph(
                checkpointer=checkpointer,
                nodes=nodes,
                authorize=authorized,
                canceled=canceled,
                propagate=(ConversationConflict, ConversationNotFound),
                retryable=turn_retryable,
            )

            async def run_graph():
                stored = await graph.stored_version(run_id=turn.turn_id)
                if stored is not None and stored != (CHAT_GRAPH_VERSION, CHAT_STATE_SCHEMA_VERSION):
                    await graph.discard(run_id=turn.turn_id)
                elif await graph.has_checkpoint(run_id=turn.turn_id):
                    return await graph.resume(run_id=turn.turn_id)
                return await graph.start(
                    run_id=turn.turn_id,
                    payload={"conversationId": conversation_id, "turnId": turn.turn_id},
                    route="insights" if insights_query_id is not None else "chat",
                    budget=self.turn_budget,
                )

            running = asyncio.create_task(run_graph())
            # Task 7: poll `cancel_requested` far more often than the lease
            # renewal cadence (`renew_interval_seconds`, often tens of
            # seconds) so a cancel stops this Turn's generation within
            # well under a second, instead of waiting for the next node
            # boundary. Lease renewal itself still only fires every
            # `renew_interval_seconds`, tracked separately below.
            check_interval = min(self.renew_interval_seconds, 0.25)
            since_last_renew = 0.0
            try:
                while True:
                    done, _ = await asyncio.wait({running}, timeout=check_interval)
                    if done:
                        state = await running
                        break
                    if await canceled():
                        running.cancel()
                        await asyncio.gather(running, return_exceptions=True)
                        # The API already wrote `turn.canceled` (including
                        # any partial draft) before setting this marker, so
                        # there is nothing left for the worker to persist.
                        outcome = "canceled"
                        return
                    since_last_renew += check_interval
                    if renew is not None and since_last_renew >= self.renew_interval_seconds:
                        since_last_renew = 0.0
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
        except Exception as error:  # noqa: BLE001 - classified below
            failure_class = classify_turn_failure(error)
            turn_span.set_attribute("tap.failure.class", failure_class)
            turn_span.set_attribute("tap.failure.error", type(error).__name__)
            if failure_class == "ownership":
                # Another worker, or the owning caller, proved it owns this Turn
                # (lease loss, conflicting write, or a deleted Conversation); do
                # not write anything.
                outcome = "lost"
                return
            if failure_class == "deterministic" or turn.attempt >= self.max_turn_attempts:
                outcome = "failed"
                try:
                    await fail_turn()
                except (ConversationConflict, PermissionError):
                    # Token and expiry checks prevent a real loser from failing the Turn.
                    return
                return
            outcome = "deferred"
            try:
                await self.conversations.repository.defer_turn(
                    conversation_id,
                    turn.turn_id,
                    turn.lease_token,
                    next_attempt_at=datetime.now(UTC) + retry_delay(turn.attempt, rng=self.rng),
                    failure=f"transient:{type(error).__name__}",
                )
            except ConversationConflict:
                # Another worker proved it owns this Turn before the defer
                # committed; do not report this as a deferred attempt.
                outcome = "lost"
                return
        finally:
            turn_span.set_attribute("tap.outcome", outcome)


async def _wait_or_stop(stop: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=timeout)
    except TimeoutError:
        pass


async def run_worker_loop(
    worker: GenerationWorker,
    stop: asyncio.Event,
    *,
    batch_size: int,
    poll_seconds: float,
    wait: Callable[[asyncio.Event, float], Awaitable[None]] = _wait_or_stop,
) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            processed = await worker.run_once(limit=batch_size)
        except Exception as error:  # noqa: BLE001 - keep the loop alive across run errors
            _LOGGER.exception("generation worker run_once failed")
            with span("worker.loop_error", {"tap.error": type(error).__name__}) as loop_span:
                loop_span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                loop_span.record_exception(error)
            await wait(stop, backoff)
            backoff = min(30.0, backoff * 2)
            continue
        backoff = 1.0
        if processed == 0:
            await wait(stop, poll_seconds)


async def main() -> None:
    from tap.entrypoints.tapper_runtime import (
        TapperSettings,
        _redact_chat_egress,
        create_api_runtime,
    )
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
        max_turn_attempts=settings.turn_max_attempts,
        turn_budget=settings.turn_budget,
        # The generation worker reuses `create_api_runtime`, so this is the
        # exact same Redis-backed `DraftStream` the API composes (Task 4).
        drafts=runtime.http_services.draft_stream,
        redact=_redact_chat_egress,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(event, stop.set)
        except NotImplementedError:
            pass
    try:
        await run_worker_loop(
            worker,
            stop,
            batch_size=settings.job_batch_size,
            poll_seconds=settings.poll_seconds,
        )
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    asyncio.run(main())
