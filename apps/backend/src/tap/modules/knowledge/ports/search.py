"""Stable async ports used by the Knowledge application."""

from __future__ import annotations

from typing import Protocol

from tap.modules.knowledge.domain.models import Evidence
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    SearchExecution,
    SearchHit,
)


class SearchPort(Protocol):
    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]: ...


class ModelPort(Protocol):
    async def embed(self, query: str) -> Embedding: ...

    async def answer(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        profile_id: str,
    ) -> AnswerGeneration: ...
