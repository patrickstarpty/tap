"""Provider-neutral generation worker for durable Conversation turns."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from typing import Any

from tap.contracts.http import RetrievalAnswerRequest
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.conversations import (
    CitationEvidence,
    GraphContextStatus,
    RetrievalSummary,
    content_digest,
)


@dataclass(slots=True)
class GenerationWorker:
    conversations: Any
    knowledge: Any

    async def run_once(self, *, limit: int) -> int:
        claimed = await self.conversations.repository.claim_queued(limit=limit)
        for conversation_id, turn in claimed:

            async def provider(_snapshot, value=turn.input_snapshot.value):
                answer = await self.knowledge.answer(RetrievalAnswerRequest(query=value.message))
                citations = tuple(
                    CitationEvidence(item.citation_id, content_digest(item.model_dump(mode="json")))
                    for item in answer.citations
                )
                return ProviderResult(
                    answer=answer.answer,
                    graph_context_status=GraphContextStatus.NOT_REQUESTED,
                    retrieval_summary=RetrievalSummary(
                        "abstained" if answer.abstained else "completed",
                        trace_id=answer.trace_id,
                        authorized_hit_count=len(answer.citations),
                    ),
                    citations=citations,
                    abstained=answer.abstained,
                )

            async def complete(evidence, chat=conversation_id, identity=turn.turn_id):
                if evidence.answer:
                    await self.conversations.emit(
                        chat, identity, "answer.delta", {"text": evidence.answer}
                    )
                await self.conversations.complete_evidence(chat, identity, evidence)

            await TurnProcessor(provider=provider, complete=complete).process(turn.input_snapshot)
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
