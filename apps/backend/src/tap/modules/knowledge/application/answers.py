"""Selected-source answer orchestration for the local Athena knowledge space."""

from __future__ import annotations

import asyncio
from typing import Protocol

from tap.modules.access.domain.policy import PolicyUnavailable, RetrievalPolicyContext
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    AnswerRequest,
    AnswerResponse,
    ResourceMode,
    ResourceRef,
    SearchRequest,
    SearchResponse,
    SourceFamily,
)
from tap.modules.knowledge.ports.answers import (
    AnswerSnapshot,
    AnswerSnapshotRepository,
    AnswerSnapshotUnavailable,
    CitationSnapshot,
    DocumentStateChanged,
    ReadyDocumentRevision,
)

__all__ = [
    "AnswerSelectionRejected",
    "AnswerService",
    "AnswerSnapshot",
    "AnswerSnapshotUnavailable",
    "CitationSnapshot",
    "DocumentStateChanged",
    "ReadyDocumentRevision",
    "validate_answer_selection",
]


class AnswerSelectionRejected(ValueError):
    """Browser retrieval controls are outside the fixed local-demo answer shape."""

    _CODES = frozenset({"source-selection-required", "unsupported-answer-control"})

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("answer selection errors must use a closed code")
        self.code = code
        super().__init__(code)


class KnowledgeAnswerGateway(Protocol):
    async def search(
        self, request: SearchRequest, policy: RetrievalPolicyContext
    ) -> SearchResponse: ...

    async def answer(
        self, request: AnswerRequest, policy: RetrievalPolicyContext
    ) -> AnswerResponse: ...


class AnswerService:
    """Close browser selection into current revisions before the sole Knowledge API call."""

    def __init__(
        self,
        *,
        repository: AnswerSnapshotRepository,
        knowledge: KnowledgeAnswerGateway,
    ) -> None:
        self._repository = repository
        self._knowledge = knowledge

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Run internal E2E evidence verification through the answer authority graph."""

        document_ids = validate_search_selection(request)
        ordered = await self._load_selected_revisions(document_ids)
        trusted = SearchRequest(
            query=request.query,
            answer_mode=AnswerMode.QUICK,
            source_families=(SourceFamily.DOC,),
            resource_refs=_scope_refs(ordered),
        )
        return await self._knowledge.search(trusted, build_demo_policy_context(ordered))

    async def answer(self, request: AnswerRequest) -> AnswerResponse:
        document_ids = validate_answer_selection(request)
        ordered = await self._load_selected_revisions(document_ids)
        trusted = AnswerRequest(
            query=request.query,
            answer_mode=AnswerMode.QUICK,
            source_families=(SourceFamily.DOC,),
            resource_refs=_scope_refs(ordered),
        )
        policy = build_demo_policy_context(ordered)
        response = await self._knowledge.answer(trusted, policy)
        try:
            snapshot = AnswerSnapshot.from_response(
                response=response,
                query=trusted.query,
                selected_revisions=ordered,
            )
        except (TypeError, ValueError) as error:
            raise AnswerSnapshotUnavailable("answer snapshot validation failed") from error
        try:
            await self._repository.save_answer_with_citations(snapshot)
        except (DocumentStateChanged, AnswerSnapshotUnavailable, asyncio.CancelledError):
            raise
        except Exception as error:
            raise AnswerSnapshotUnavailable("answer snapshot commit failed") from error
        return response

    async def _load_selected_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        try:
            rows = await self._repository.load_ready_revisions(document_ids)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise PolicyUnavailable("current document policy is unavailable") from error
        if (
            len(rows) != len(document_ids)
            or len({row.document_id for row in rows}) != len(rows)
            or set(document_ids) != {row.document_id for row in rows}
        ):
            raise DocumentStateChanged("selected document is not ready and current")
        return tuple(sorted(rows, key=lambda item: item.document_id))


def validate_answer_selection(request: AnswerRequest) -> tuple[str, ...]:
    """Validate the closed browser-selectable request shape without provider I/O."""
    if not isinstance(request, AnswerRequest):
        raise TypeError("answer service requires a framework-free AnswerRequest")
    return _validate_selection(request)


def validate_search_selection(request: SearchRequest) -> tuple[str, ...]:
    """Validate the same closed selection for internal real-search verification."""

    if not isinstance(request, SearchRequest):
        raise TypeError("answer service search requires a framework-free SearchRequest")
    return _validate_selection(request)


def _validate_selection(request: AnswerRequest | SearchRequest) -> tuple[str, ...]:
    refs = request.resource_refs
    if not 1 <= len(refs) <= 20 or len({ref.source_id for ref in refs}) != len(refs):
        raise AnswerSelectionRejected("source-selection-required")
    if (
        request.answer_mode is not AnswerMode.QUICK
        or request.source_families not in {(), (SourceFamily.DOC,)}
        or request.requested_environment is not None
        or request.requested_corpus_version is not None
        or request.top_k is not None
        or any(
            ref.family is not SourceFamily.DOC
            or ref.mode is not ResourceMode.SCOPE
            or ref.requested_revision is not None
            or ref.anchor is not None
            for ref in refs
        )
    ):
        raise AnswerSelectionRejected("unsupported-answer-control")
    return tuple(ref.source_id for ref in refs)


def _scope_refs(
    rows: tuple[ReadyDocumentRevision, ...],
) -> tuple[ResourceRef, ...]:
    return tuple(
        ResourceRef(
            family=SourceFamily.DOC,
            source_id=row.document_id,
            mode=ResourceMode.SCOPE,
        )
        for row in rows
    )
