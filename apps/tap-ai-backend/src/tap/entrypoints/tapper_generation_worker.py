"""Provider-neutral generation worker for durable Conversation turns."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.trace import Span

from tap.contracts.http import (
    InsightsExplanationResult,
    ResourceMode,
    ResourceRef,
    RetrievalAnswerRequest,
    SourceFamily,
)
from tap.contracts.problems import build_problem
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.interaction_graph import InteractionGraph
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
from tap.modules.chat.application.plan_answer import planning_input
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    GraphContextStatus,
    RetrievalSummary,
)
from tap.platform.telemetry import bind_trace, extract_traceparent, flush_traces, span


def _evidence_checkpoint(evidence: AnswerEvidence) -> dict[str, object]:
    result: dict[str, object] = {
        "answer": evidence.answer,
        "outcome": evidence.outcome,
        "retrievalSummary": {
            "status": evidence.retrieval_summary.status,
            "traceId": evidence.retrieval_summary.trace_id,
            "authorizedHitCount": evidence.retrieval_summary.authorized_hit_count,
        },
        "graphContextStatus": evidence.graph_context_status.value,
        "graphSnapshotId": evidence.graph_snapshot_id,
        "citations": [
            {
                "citationSnapshotId": item.citation_snapshot_id,
                "citationDigest": item.citation_digest,
            }
            for item in evidence.citations
        ],
        "diagnostics": list(evidence.diagnostics),
    }
    if evidence.insights_explanation is not None:
        result["insightsExplanation"] = evidence.insights_explanation
    return result


def _evidence_from_checkpoint(raw: object) -> AnswerEvidence:
    if not isinstance(raw, dict) or not isinstance(raw.get("retrievalSummary"), dict):
        raise ValueError("chat graph checkpoint lacks answer evidence")
    summary = raw["retrievalSummary"]
    citations = raw.get("citations", [])
    if not isinstance(citations, list):
        raise ValueError("chat graph checkpoint citations are invalid")
    return AnswerEvidence(
        str(raw.get("answer", "")),
        str(raw["outcome"]),
        RetrievalSummary(
            str(summary["status"]),
            trace_id=summary.get("traceId"),
            authorized_hit_count=int(summary.get("authorizedHitCount", 0)),
        ),
        GraphContextStatus(str(raw["graphContextStatus"])),
        graph_snapshot_id=raw.get("graphSnapshotId"),
        citations=tuple(
            CitationEvidence(str(item["citationSnapshotId"]), str(item["citationDigest"]))
            for item in citations
            if isinstance(item, dict)
        ),
        diagnostics=tuple(str(item) for item in raw.get("diagnostics", [])),
        insights_explanation=raw.get("insightsExplanation"),
    )


@dataclass(slots=True)
class GenerationWorker:
    conversations: Any
    knowledge: Any
    checkpointer: BaseCheckpointSaver | None = None
    insights_explanation: Any | None = None
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
        answer_response = None
        active_plan = None
        insights_query_id = getattr(turn.input_snapshot.value, "insights_query_id", None)
        planner = getattr(self.knowledge, "answer_planner", None)
        renew = getattr(self.conversations.repository, "renew_processing_lease", None)

        async def provider(_snapshot, value=turn.input_snapshot.value):
            nonlocal answer_response
            request = RetrievalAnswerRequest(
                query=value.message,
                sources=[SourceFamily.DOC],
                resource_refs=[
                    ResourceRef(
                        family=SourceFamily.DOC,
                        source_id=item.source_id,
                        mode=ResourceMode.SCOPE,
                    )
                    for item in value.resolved_resources
                ],
            )
            answer_boundary = getattr(self.knowledge, "answer_conversation", None)
            answer = (
                await answer_boundary(
                    request,
                    value,
                    **(
                        {"answer_plan": active_plan, "authorize": require_authorized}
                        if active_plan is not None
                        else {}
                    ),
                )
                if answer_boundary is not None
                else await self.knowledge.answer(request)
            )
            answer_response = answer
            citations = (
                await self.conversations.repository.resolve_citations(
                    answer.trace_id, tuple(item.citation_id for item in answer.citations)
                )
                if answer.citations
                else ()
            )
            return ProviderResult(
                answer=answer.answer,
                graph_context_status=GraphContextStatus(
                    getattr(answer, "graph_context_status", "UNAVAILABLE")
                ),
                graph_snapshot_id=getattr(answer, "graph_snapshot_id", None),
                retrieval_summary=RetrievalSummary(
                    "abstained" if answer.abstained else "completed",
                    trace_id=answer.trace_id,
                    authorized_hit_count=len(answer.citations),
                ),
                citations=citations,
                abstained=answer.abstained,
                answer_plan_id=None if active_plan is None else active_plan.plan_id,
            )

        def stream_events(evidence) -> list[dict[str, object]]:
            if evidence.outcome == "failed":
                return []
            events: list[dict[str, object]] = [
                {
                    "type": "context.assembled",
                    "payload": {"sourceCount": len(turn.input_snapshot.value.resolved_resources)},
                },
                {
                    "type": "stage.completed",
                    "payload": {"stage": "knowledge.answer", "outcome": evidence.outcome},
                },
                {
                    "type": "retrieval.hits_ready",
                    "payload": {
                        "authorizedHitCount": evidence.retrieval_summary.authorized_hit_count
                    },
                },
            ]
            if evidence.answer:
                events.append({"type": "answer.delta", "payload": {"text": evidence.answer}})
            if answer_response is not None:
                events.extend(
                    {
                        "type": "citation.resolved",
                        "payload": {"citation": citation.model_dump(mode="json", by_alias=True)},
                    }
                    for citation in answer_response.citations
                )
            return events

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

        outcome = "failed"
        try:

            async def classify(_state):
                if insights_query_id is not None:
                    return {"reasoning_mode": "workflow"}
                if planner is None:
                    return {"reasoning_mode": "direct"}
                authorize_planning = getattr(self.knowledge, "authorize_planning", None)
                if authorize_planning is not None:
                    await authorize_planning(turn.input_snapshot)
                load = getattr(self.conversations, "load", None)
                history = (
                    ()
                    if load is None
                    else tuple(item.input_snapshot for item in (await load(conversation_id)).turns)
                )
                plan = await planner.plan(planning_input(turn.input_snapshot, history=history))
                return {"reasoning_mode": "direct", "answer_plan": plan.to_dict()}

            async def admit(_state):
                return {"admitted": True}

            async def execute(_state):
                nonlocal active_plan
                if insights_query_id is not None:
                    if self.insights_explanation is None:
                        raise RuntimeError("Insights explanation runtime is unavailable")
                    result = await self.insights_explanation.explain(
                        self.conversations.scope,
                        insights_query_id,
                        turn.input_snapshot.value.insights_report_refs,
                        turn.input_snapshot.value.message,
                        conversation_id=conversation_id,
                        turn_id=turn.turn_id,
                        selected_knowledge=turn.input_snapshot.value.resolved_resources,
                    )
                    if result.get("queryId") != insights_query_id:
                        raise InsightsQueryUnavailable(
                            "Insights result changed the historical query ID"
                        )
                    try:
                        InsightsExplanationResult.model_validate(result)
                    except ValueError as exc:
                        raise InsightsQueryUnavailable(
                            "Insights result does not satisfy the response contract"
                        ) from exc
                    evidence = AnswerEvidence(
                        "Insights interpretation",
                        "completed",
                        RetrievalSummary("completed", trace_id=insights_query_id),
                        GraphContextStatus.NOT_REQUESTED,
                        insights_explanation=dict(result),
                    )
                    audit = {
                        "conversationId": conversation_id,
                        "turnId": turn.turn_id,
                        "graphRunId": turn.turn_id,
                        "tool": "insights.query",
                        "queryId": insights_query_id,
                        "metricVersion": result.get("metricVersion"),
                        "resourceRefs": list(turn.input_snapshot.value.insights_report_refs),
                        "knowledgeSearchPerformed": result.get("knowledgeSearchPerformed", False)
                        is True,
                        "knowledgeSources": [
                            {
                                "sourceId": item.source_id,
                                "revisionId": item.revision_id,
                                "sourceContentHash": item.source_content_hash,
                            }
                            for item in turn.input_snapshot.value.resolved_resources
                        ],
                        "knowledgeCitations": [
                            {
                                "citationId": item.get("citationId"),
                                "sourceId": item.get("sourceId"),
                                "revisionId": item.get("revisionId"),
                                "chunkId": item.get("chunkId"),
                                "publicationId": item.get("publicationId"),
                                "approvalDigest": item.get("approvalDigest"),
                            }
                            for item in result.get("evidenceExcerpts", [])
                            if isinstance(item, dict) and item.get("sourceId")
                        ],
                    }
                    audit_events = [
                        {
                            "type": "context.assembled",
                            "payload": {
                                "sourceCount": len(turn.input_snapshot.value.resolved_resources)
                            },
                        },
                        {"type": "stage.completed", "payload": audit},
                    ]
                    if audit["knowledgeSearchPerformed"]:
                        audit_events.append(
                            {
                                "type": "stage.completed",
                                "payload": {
                                    "stage": "knowledge.search",
                                    "conversationId": conversation_id,
                                    "turnId": turn.turn_id,
                                    "graphRunId": turn.turn_id,
                                    "queryId": insights_query_id,
                                    "sourceIds": [
                                        item.source_id
                                        for item in turn.input_snapshot.value.resolved_resources
                                    ],
                                },
                            }
                        )
                    return {
                        "result": {
                            "evidence": _evidence_checkpoint(evidence),
                            "terminalEvent": {
                                "type": "turn.completed",
                                "payload": {"insightsAudit": audit},
                            },
                            "streamEvents": audit_events,
                        }
                    }
                if planner is not None:
                    active_plan = AnswerPlan.from_dict(_state["answer_plan"])
                    active_plan.validate_binding(planning_input(turn.input_snapshot))
                evidence = await TurnProcessor(
                    provider=provider, complete=lambda _evidence: None
                ).process(turn.input_snapshot)
                terminal_event: dict[str, object] | None
                if evidence.outcome == "failed":
                    terminal_event = {
                        "type": "turn.failed",
                        "payload": {
                            "problem": build_problem(
                                "answer-unavailable", correlation_id=turn.turn_id
                            ).model_dump(mode="json", by_alias=True)
                        },
                    }
                elif answer_response is None:
                    terminal_event = None
                else:
                    terminal_event = {
                        "type": (
                            "turn.abstained" if answer_response.abstained else "turn.completed"
                        ),
                        "payload": {
                            "answer": answer_response.model_dump(mode="json", by_alias=True)
                        },
                    }
                return {
                    "result": {
                        "evidence": _evidence_checkpoint(evidence),
                        "terminalEvent": terminal_event,
                        "streamEvents": stream_events(evidence),
                    }
                }

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
            graph = InteractionGraph(
                graph_version=(
                    "insights-explanation-v1" if insights_query_id is not None else "fast-chat-v1"
                ),
                state_schema_version=1,
                checkpointer=checkpointer,
                classify=classify,
                admit=admit,
                execute=execute,
                authorize=authorized,
            )

            async def run_graph():
                if await graph.has_checkpoint(run_id=turn.turn_id):
                    return await graph.resume(run_id=turn.turn_id)
                return await graph.start(
                    run_id=turn.turn_id,
                    payload={"conversationId": conversation_id, "turnId": turn.turn_id},
                    execution_mode="inline",
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
            outcome = evidence.outcome
        except InsightsAuthorizationChanged:
            if insights_query_id is None:
                raise
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
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                # Token and expiry checks prevent a real loser from failing the Turn.
                return
        except GraphCheckpointRetryable:
            if turn.attempt < self.max_checkpoint_attempts:
                return
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                return
        except GraphCheckpointUnavailable:
            try:
                await fail_turn()
            except (ConversationConflict, PermissionError):
                return
        except AuthorizationDenied:
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
        conversations, knowledge, insights_explanation=runtime.http_services.insights_explanation
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
