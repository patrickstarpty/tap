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
from tap.modules.ai.application.interaction_graph import InteractionGraph
from tap.modules.chat.application.conversations import ConversationConflict
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.conversations import (
    GraphContextStatus,
    RetrievalSummary,
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
                    await answer_boundary(request, value)
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
                )

            async def complete(evidence, chat=conversation_id, identity=turn.turn_id):
                if evidence.outcome == "failed":
                    await self.conversations.complete_evidence(
                        chat,
                        identity,
                        evidence,
                        lease_token=turn.lease_token,
                        terminal_event=(
                            "turn.failed",
                            {
                                "problem": build_problem(
                                    "answer-unavailable", correlation_id=identity
                                ).model_dump(mode="json", by_alias=True)
                            },
                        ),
                    )
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
                    await self.conversations.complete_evidence(
                        chat, identity, evidence, lease_token=turn.lease_token
                    )
                    return
                for citation in answer_response.citations:
                    await self.conversations.emit(
                        chat,
                        identity,
                        "citation.resolved",
                        {"citation": citation.model_dump(mode="json", by_alias=True)},
                        lease_token=turn.lease_token,
                    )
                await self.conversations.complete_evidence(
                    chat,
                    identity,
                    evidence,
                    lease_token=turn.lease_token,
                    terminal_event=(
                        "turn.abstained" if answer_response.abstained else "turn.completed",
                        {"answer": answer_response.model_dump(mode="json", by_alias=True)},
                    ),
                )

            try:

                async def classify(_state):
                    return {"reasoning_mode": "direct"}

                async def admit(_state):
                    return {"admitted": True}

                async def execute(_state):
                    evidence = await TurnProcessor(provider=provider, complete=complete).process(
                        turn.input_snapshot
                    )
                    return {"result": {"outcome": evidence.outcome}}

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
                            await running
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
