"""Provider-neutral generation worker for durable Conversation turns."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from typing import Any

from tap.contracts.http import ResourceMode, ResourceRef, RetrievalAnswerRequest, SourceFamily
from tap.contracts.problems import build_problem
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

    async def run_once(self, *, limit: int) -> int:
        claimed = await self.conversations.repository.claim_queued(limit=limit)
        for conversation_id, turn in claimed:
            answer_response = None

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
                await TurnProcessor(provider=provider, complete=complete).process(
                    turn.input_snapshot
                )
            except ConversationConflict:
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
