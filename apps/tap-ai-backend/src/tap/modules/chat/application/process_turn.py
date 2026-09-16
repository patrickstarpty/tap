"""Close every accepted generation attempt with bounded evidence."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, cast

from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    GraphContextStatus,
    RetrievalSummary,
)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    answer: str
    graph_context_status: GraphContextStatus
    graph_snapshot_id: str | None = None
    retrieval_summary: RetrievalSummary = RetrievalSummary("completed")
    citations: tuple[CitationEvidence, ...] = ()
    abstained: bool = False

    def __post_init__(self):
        if (self.graph_context_status is GraphContextStatus.APPLIED) != bool(
            self.graph_snapshot_id
        ):
            if self.graph_context_status is GraphContextStatus.APPLIED:
                raise ValueError("APPLIED graph context requires a graph snapshot")
            raise ValueError("graph snapshot is legal only for APPLIED graph context")


class TurnProcessor:
    def __init__(
        self,
        *,
        provider: Callable[[object], Awaitable[ProviderResult]],
        complete: Callable[[AnswerEvidence], object],
    ):
        self.provider = provider
        self.complete = complete

    async def process(
        self, snapshot: object, *, provider_events: Iterable[object] = ()
    ) -> AnswerEvidence:
        diagnostics = tuple(
            "unknown-provider-event"
            for event in provider_events
            if not isinstance(event, str) or event not in {"started", "delta", "completed"}
        )
        try:
            result = await self.provider(snapshot)
            outcome = "abstained" if result.abstained else "completed"
            evidence = AnswerEvidence(
                result.answer,
                outcome,
                result.retrieval_summary,
                result.graph_context_status,
                result.graph_snapshot_id,
                result.citations,
                diagnostics,
            )
        except Exception:
            evidence = AnswerEvidence(
                "",
                "failed",
                RetrievalSummary("failed"),
                GraphContextStatus.FAILED,
                diagnostics=diagnostics,
            )
        completion_result = self.complete(evidence)
        if inspect.isawaitable(completion_result):
            await cast(Awaitable[object], completion_result)
        return evidence
