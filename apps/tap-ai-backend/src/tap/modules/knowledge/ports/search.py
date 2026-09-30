"""Stable async ports used by the Knowledge application."""

from __future__ import annotations

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.knowledge.domain.models import Evidence
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    SearchExecution,
    SearchHit,
)


class SearchPort(Protocol):
    """Provider-neutral Knowledge retrieval; Milvus is the only implementation today.

    Implementations must apply the mandatory tenant, project, group, classification,
    environment, corpus and resource-scope prefilter on every channel, return only
    `SearchHit` values mapped from rows that passed it, and raise only the neutral
    `SearchUnavailable`/`SearchBoundsExceeded` errors. A new provider must pass
    `tests/contract/search_provider_conformance.py` (run by
    `tests/contract/test_search_provider_conformance.py`) and be selected only in
    `entrypoints/knowledge_bootstrap.py`.
    """

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


class GovernedKnowledgeModels(ModelPort, Protocol):
    """Domain mapping of one Project-bound ModelGateway, with no provider authority."""

    @property
    def gateway(self) -> ModelGateway: ...

    @property
    def scope(self) -> ProjectScopeContext: ...
