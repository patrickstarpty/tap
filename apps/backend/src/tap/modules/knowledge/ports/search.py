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


class QueryEmbeddingPort(Protocol):
    @property
    def embedding_model_id(self) -> str:
        raise NotImplementedError

    @property
    def embedding_dimension(self) -> int:
        raise NotImplementedError

    async def embed(self, query: str) -> Embedding:
        raise NotImplementedError


class AnswerGenerationPort(Protocol):
    async def answer(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        profile_id: str,
    ) -> AnswerGeneration:
        raise NotImplementedError


class ModelPort(QueryEmbeddingPort, AnswerGenerationPort, Protocol):
    """Compatibility intersection for adapters/fakes that implement both narrow ports."""
