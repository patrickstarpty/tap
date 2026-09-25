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

from tap.contracts.http import ResourceMode, ResourceRef, RetrievalAnswerRequest, SourceFamily
from tap.contracts.problems import build_problem
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.interaction_graph import InteractionGraph
from tap.modules.ai.domain.graph_runs import GraphCheckpointUnavailable
from tap.modules.chat.application.conversations import ConversationConflict
from tap.modules.chat.application.plan_answer import planning_input
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    GraphContextStatus,
    RetrievalSummary,
)


def _evidence_checkpoint(evidence: AnswerEvidence) -> dict[str, object]:
    return {
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
    )


@dataclass(slots=True)
class GenerationWorker:
    conversations: Any
    knowledge: Any
    checkpointer: BaseCheckpointSaver | None = None
    lease_duration: timedelta = timedelta(seconds=60)
    renew_interval_seconds: float = 20.0

    def __post_init__(self) -> None:
        if (
            not timedelta(0) < self.lease_duration <= timedelta(minutes=15)
            or not 0 < self.renew_interval_seconds < self.lease_duration.total_seconds()
        ):
            raise ValueError("generation worker lease renewal is invalid")

    async def run_once(self, *, limit: int) -> int:
        claimed = await self.conversations.repository.claim_queued(limit=limit)
        for conversation_id, turn in claimed:
            answer_response = None
            active_plan = None
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

            async def complete(evidence, chat=conversation_id, identity=turn.turn_id):
                if evidence.outcome == "failed":
                    return
                await self.conversations.emit(
                    chat,
                    identity,
                    "context.assembled",
                    {"sourceCount": len(turn.input_snapshot.value.resolved_resources)},
                    lease_token=turn.lease_token,
                )
                await self.conversations.emit(
                    chat,
                    identity,
                    "stage.completed",
                    {"stage": "knowledge.answer", "outcome": evidence.outcome},
                    lease_token=turn.lease_token,
                )
                await self.conversations.emit(
                    chat,
                    identity,
                    "retrieval.hits_ready",
                    {"authorizedHitCount": evidence.retrieval_summary.authorized_hit_count},
                    lease_token=turn.lease_token,
                )
                if evidence.answer:
                    await self.conversations.emit(
                        chat,
                        identity,
                        "answer.delta",
                        {"text": evidence.answer},
                        lease_token=turn.lease_token,
                    )
                if answer_response is None:
                    return
                for citation in answer_response.citations:
                    await self.conversations.emit(
                        chat,
                        identity,
                        "citation.resolved",
                        {"citation": citation.model_dump(mode="json", by_alias=True)},
                        lease_token=turn.lease_token,
                    )

            try:

                async def classify(_state):
                    if planner is None:
                        return {"reasoning_mode": "direct"}
                    authorize_planning = getattr(self.knowledge, "authorize_planning", None)
                    if authorize_planning is not None:
                        await authorize_planning(turn.input_snapshot)
                    load = getattr(self.conversations, "load", None)
                    history = (
                        ()
                        if load is None
                        else tuple(
                            item.input_snapshot for item in (await load(conversation_id)).turns
                        )
                    )
                    plan = await planner.plan(planning_input(turn.input_snapshot, history=history))
                    return {"reasoning_mode": "direct", "answer_plan": plan.to_dict()}

                async def admit(_state):
                    return {"admitted": True}

                async def execute(_state):
                    nonlocal active_plan
                    if planner is not None:
                        active_plan = AnswerPlan.from_dict(_state["answer_plan"])
                        active_plan.validate_binding(planning_input(turn.input_snapshot))
                    evidence = await TurnProcessor(provider=provider, complete=complete).process(
                        turn.input_snapshot
                    )
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
                    graph_version="fast-chat-v1",
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
                raw_terminal = result.get("terminalEvent")
                terminal_event = (
                    None
                    if raw_terminal is None
                    else (str(raw_terminal["type"]), dict(raw_terminal["payload"]))
                )
                await self.conversations.complete_evidence(
                    conversation_id,
                    turn.turn_id,
                    evidence,
                    lease_token=turn.lease_token,
                    terminal_event=terminal_event,
                )
            except (ConversationConflict, PermissionError, GraphCheckpointUnavailable):
                # Cancellation, lease reclaim, or transient checkpoint storage leaves
                # the turn non-terminal so a later claim can recover it.
                continue
            except AuthorizationDenied:
                try:
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
                except (ConversationConflict, PermissionError):
                    # Cancellation or lease reclaim won the terminal-state race.
                    continue
        return len(claimed)


async def main() -> None:
    from tap.entrypoints.tapper_runtime import TapperSettings, create_api_runtime

    settings = TapperSettings.from_mapping(dict(os.environ))
    runtime = await create_api_runtime(settings)
    conversations = runtime.http_services.conversations
    knowledge = runtime.http_services.knowledge
    if conversations is None or knowledge is None:
        await runtime.aclose()
        raise RuntimeError("generation worker composition is unavailable")
    worker = GenerationWorker(conversations, knowledge)
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
