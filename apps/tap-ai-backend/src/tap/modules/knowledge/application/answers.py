"""Selected-source answer orchestration for the local Tapper knowledge space."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import PolicyUnavailable, RetrievalPolicyContext
from tap.modules.ai.domain.models import GenerationGovernance
from tap.modules.ai.domain.persona import CURRENT_PERSONA, TapperPersona
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.application.publication import (
    FlowchartPublicationGate,
    PublicationBinding,
    PublishedKnowledgeAuthority,
)
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
        corpus_version: str = "tapper-demo-v1",
        publication_authority: PublishedKnowledgeAuthority | None = None,
        flowchart_gate: FlowchartPublicationGate | None = None,
    ) -> None:
        self._repository = repository
        self._knowledge = knowledge
        self._publication_authority = publication_authority
        self._flowchart_gate = None if publication_authority is not None else flowchart_gate
        if corpus_version not in {"tapper-demo-v1", "tapper-demo-v2"}:
            raise ValueError("unsupported projection corpus")
        self._corpus_version = corpus_version

    @property
    def scope(self) -> ProjectScopeContext:
        """Expose the binding held by the actual repository, without a second label."""
        return self._repository.scope

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Run internal E2E evidence verification through the answer authority graph."""

        document_ids = validate_search_selection(request)
        ordered = await self._load_selected_revisions(document_ids)
        publication = await self._authorize_selection(ordered)
        trusted = SearchRequest(
            query=request.query,
            answer_mode=AnswerMode.QUICK,
            source_families=(SourceFamily.DOC,),
            resource_refs=_scope_refs(ordered),
        )
        response = await self._knowledge.search(
            trusted, build_demo_policy_context(ordered, corpus_version=self._corpus_version)
        )
        await self._revalidate_publication(publication)
        return response

    async def answer(self, request: AnswerRequest) -> AnswerResponse:
        document_ids = validate_answer_selection(request)
        ordered = await self._load_selected_revisions(document_ids)
        publication = await self._authorize_selection(ordered)
        trusted = AnswerRequest(
            query=request.query,
            answer_mode=AnswerMode.QUICK,
            source_families=(SourceFamily.DOC,),
            resource_refs=_scope_refs(ordered),
        )
        policy = build_demo_policy_context(ordered, corpus_version=self._corpus_version)
        response = await self._knowledge.answer(trusted, policy)
        await self._revalidate_publication(publication)
        try:
            if policy.active_corpus_version != self._corpus_version:
                raise ValueError("answer policy corpus changed")
            snapshot = AnswerSnapshot.from_response(
                response=response,
                query=trusted.query,
                selected_revisions=ordered,
                corpus_version=self._corpus_version,
            )
        except (TypeError, ValueError) as error:
            raise AnswerSnapshotUnavailable("answer snapshot validation failed") from error
        try:
            await self._repository.save_answer_with_citations(snapshot)
        except (DocumentStateChanged, AnswerSnapshotUnavailable, asyncio.CancelledError):
            raise
        except Exception as error:
            raise AnswerSnapshotUnavailable("answer snapshot commit failed") from error
        await self._revalidate_publication(publication)
        return response

    async def answer_frozen(
        self,
        request: AnswerRequest,
        revisions: tuple[ReadyDocumentRevision, ...],
        policy: RetrievalPolicyContext,
        *,
        governance: GenerationGovernance | None,
        graph_context=(),
        model_alias: str | None = None,
        answer_execution=None,
        authorize=None,
    ) -> AnswerResponse:
        """Compose the resumable phases below into the same one-call behavior
        this method has always had; LangGraph nodes call the phases directly.

        Each phase independently re-opens `asyncio.timeout(answer_execution.
        remaining_seconds)` (a fixed budget captured once by `authorized_execution`,
        not a countdown) so it can be called alone as a resumed node. Composed
        here in one call, that would let total wall-clock grow to ~3x the plan's
        budget; wrap the whole composition in one outer timeout for the same
        single persisted deadline `answer_frozen` always enforced.
        """

        async def compose() -> AnswerResponse:
            prepared = await self.prepare_frozen(
                request,
                revisions,
                policy,
                governance=governance,
                graph_context=graph_context,
                answer_execution=answer_execution,
                authorize=authorize,
            )
            if isinstance(prepared, AnswerResponse):
                return prepared
            generation = await self.generate_frozen(
                prepared,
                revisions,
                policy,
                governance=governance,
                model_alias=model_alias,
                answer_execution=answer_execution,
                authorize=authorize,
            )
            return await self.verify_frozen(
                prepared,
                generation,
                revisions,
                policy,
                answer_execution=answer_execution,
                authorize=authorize,
            )

        if answer_execution is None:
            response = await compose()
        else:
            async with asyncio.timeout(answer_execution.remaining_seconds):
                response = await compose()
        await self.save_frozen(response, request.query, revisions)
        return response

    async def prepare_frozen(
        self,
        request: AnswerRequest,
        revisions: tuple[ReadyDocumentRevision, ...],
        policy: RetrievalPolicyContext,
        *,
        governance: GenerationGovernance | None,
        graph_context=(),
        answer_execution=None,
        authorize=None,
        include_relations: bool = True,
    ):
        publication = await self._authorize_frozen_selection(revisions, policy)
        trusted = AnswerRequest(
            query=request.query,
            answer_mode=AnswerMode.QUICK,
            source_families=(SourceFamily.DOC,),
            resource_refs=_scope_refs(revisions),
        )
        knowledge_prepare = getattr(self._knowledge, "prepare_frozen", None)
        if knowledge_prepare is None:
            if answer_execution is not None:
                raise ValueError("planned answers require the authorized Knowledge boundary")
            result = await self._knowledge.answer(trusted, policy)
        else:
            result = await knowledge_prepare(
                trusted,
                policy,
                governance=governance,
                graph_context=graph_context,
                answer_execution=answer_execution,
                authorize=self._reauthorize(publication, authorize),
                include_relations=include_relations,
            )
        await self._revalidate_publication(publication)
        return result

    async def generate_frozen(
        self,
        bundle,
        revisions: tuple[ReadyDocumentRevision, ...],
        policy: RetrievalPolicyContext,
        *,
        governance: GenerationGovernance | None,
        model_alias: str | None = None,
        answer_execution=None,
        authorize=None,
        on_text=None,
        persona: TapperPersona = CURRENT_PERSONA,
        tools: tuple[str, ...] = (),
        transcript: tuple[Mapping[str, object], ...] = (),
    ):
        publication = await self._authorize_frozen_selection(revisions, policy)
        knowledge_generate = getattr(self._knowledge, "generate_frozen", None)
        if knowledge_generate is None:
            raise ValueError("planned answers require the authorized Knowledge boundary")
        generation = await knowledge_generate(
            bundle,
            policy,
            governance=governance,
            model_alias=model_alias,
            answer_execution=answer_execution,
            authorize=self._reauthorize(publication, authorize),
            persona=persona,
            **({"on_text": on_text} if on_text is not None else {}),
            **({"tools": tools} if tools else {}),
            **({"transcript": transcript} if transcript else {}),
        )
        await self._revalidate_publication(publication)
        return generation

    async def verify_frozen(
        self,
        bundle,
        generation,
        revisions: tuple[ReadyDocumentRevision, ...],
        policy: RetrievalPolicyContext,
        *,
        answer_execution=None,
        authorize=None,
    ) -> AnswerResponse:
        publication = await self._authorize_frozen_selection(revisions, policy)
        knowledge_verify = getattr(self._knowledge, "verify_frozen", None)
        if knowledge_verify is None:
            raise ValueError("planned answers require the authorized Knowledge boundary")
        response = await knowledge_verify(
            bundle,
            generation,
            policy,
            answer_execution=answer_execution,
            authorize=self._reauthorize(publication, authorize),
        )
        await self._revalidate_publication(publication)
        return response

    async def search_more_frozen(
        self,
        bundle,
        query: str,
        source_ids: tuple[str, ...],
        revisions: tuple[ReadyDocumentRevision, ...],
        policy: RetrievalPolicyContext,
        *,
        answer_execution=None,
        authorize=None,
    ):
        """Task 6: the `knowledge.search` tool's frozen entrypoint, re-authorizing
        the accepted revision selection the same way every other `*_frozen`
        phase above does before delegating to the Knowledge boundary."""
        publication = await self._authorize_frozen_selection(revisions, policy)
        knowledge_search_more = getattr(self._knowledge, "search_more_frozen", None)
        if knowledge_search_more is None:
            raise ValueError("planned answers require the authorized Knowledge boundary")
        result = await knowledge_search_more(
            bundle,
            query,
            source_ids,
            policy,
            answer_execution=answer_execution,
            authorize=self._reauthorize(publication, authorize),
        )
        await self._revalidate_publication(publication)
        return result

    async def relations_frozen(
        self,
        bundle,
        query: str,
        revisions: tuple[ReadyDocumentRevision, ...],
        policy: RetrievalPolicyContext,
        *,
        authorize=None,
        turn_id: str | None = None,
        answer_execution=None,
    ):
        """Task 6: the `graph.relations` tool's frozen entrypoint, same
        re-authorization discipline as `search_more_frozen`."""
        publication = await self._authorize_frozen_selection(revisions, policy)
        knowledge_relations = getattr(self._knowledge, "relations_frozen", None)
        if knowledge_relations is None:
            raise ValueError("planned answers require the authorized Knowledge boundary")
        result = await knowledge_relations(
            bundle,
            query,
            policy,
            turn_id=turn_id,
            answer_execution=answer_execution,
            authorize=self._reauthorize(publication, authorize),
        )
        await self._revalidate_publication(publication)
        return result

    async def save_frozen(
        self,
        response: AnswerResponse,
        query: str,
        revisions: tuple[ReadyDocumentRevision, ...],
    ) -> None:
        publication = await self._authorize_selection(revisions)
        await self._revalidate_publication(publication)
        try:
            snapshot = AnswerSnapshot.from_response(
                response=response,
                query=query,
                selected_revisions=revisions,
                corpus_version=self._corpus_version,
            )
            frozen_save = getattr(self._repository, "save_frozen_answer_with_citations", None)
            if frozen_save is None:
                await self._repository.save_answer_with_citations(snapshot)
            else:
                await frozen_save(snapshot)
        except (DocumentStateChanged, AnswerSnapshotUnavailable, asyncio.CancelledError):
            raise
        except Exception as error:
            raise AnswerSnapshotUnavailable("answer snapshot commit failed") from error
        await self._revalidate_publication(publication)

    async def _authorize_frozen_selection(
        self,
        revisions: tuple[ReadyDocumentRevision, ...],
        policy: RetrievalPolicyContext,
    ) -> PublicationBinding | None:
        """Re-derive and re-check the accepted revision selection's authority:
        every resumable phase calls this independently rather than trusting a
        checkpointed binding from an earlier phase."""
        if (
            not revisions
            or tuple(sorted(revisions, key=lambda item: item.document_id)) != revisions
        ):
            raise DocumentStateChanged("accepted revision selection is invalid")
        publication = await self._authorize_selection(revisions)
        grants = {
            (grant.source_id, grant.revision, grant.source_content_hash)
            for grant in policy.resource_grants
        }
        if grants != {
            (item.source_id, item.revision_id, item.source_content_hash) for item in revisions
        }:
            raise DocumentStateChanged("accepted retrieval authority is invalid")
        return publication

    def _reauthorize(
        self,
        publication: PublicationBinding | None,
        authorize,
    ):
        async def reauthorize() -> None:
            await self._revalidate_publication(publication)
            if authorize is not None:
                await authorize()

        return reauthorize

    async def authorize_frozen_selection(
        self, revisions: tuple[ReadyDocumentRevision, ...]
    ) -> None:
        await self._authorize_selection(revisions)

    async def resolve_conversation_selection(
        self, revision_ids: tuple[str, ...]
    ) -> tuple[tuple[ReadyDocumentRevision, ...], RetrievalPolicyContext]:
        if not 1 <= len(revision_ids) <= 20 or len(set(revision_ids)) != len(revision_ids):
            raise DocumentStateChanged("conversation revision selection must be unique and bounded")
        rows = await self._repository.load_revision_selection(revision_ids)
        if {item.revision_id for item in rows} != set(revision_ids):
            raise DocumentStateChanged("conversation revision is not current and ready")
        ordered = tuple(sorted(rows, key=lambda item: item.document_id))
        if self._flowchart_gate is not None:
            await self._flowchart_gate.require_published(self.scope.project_id, ordered)
        return ordered, build_demo_policy_context(ordered, corpus_version=self._corpus_version)

    async def _load_selected_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        try:
            rows = await self._repository.load_source_revisions(document_ids)
        except (asyncio.CancelledError, DocumentStateChanged):
            raise
        except Exception as error:
            raise PolicyUnavailable("current document policy is unavailable") from error
        if (
            not 1 <= len(rows) <= 20
            or len({row.document_id for row in rows}) != len(rows)
            or set(document_ids) != {row.source_id for row in rows}
        ):
            raise DocumentStateChanged("selected document is not ready and current")
        return tuple(sorted(rows, key=lambda item: item.document_id))

    async def _authorize_selection(
        self, rows: tuple[ReadyDocumentRevision, ...]
    ) -> PublicationBinding | None:
        if self._publication_authority is None:
            if self._flowchart_gate is not None:
                await self._flowchart_gate.require_published(self.scope.project_id, rows)
            return None
        return await self._publication_authority.authorize_selection(
            self.scope.project_id,
            tuple(row.revision_id for row in rows),
        )

    async def _revalidate_publication(self, publication: PublicationBinding | None) -> None:
        if self._publication_authority is not None and publication is not None:
            await self._publication_authority.revalidate(publication)


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
    if any(re.fullmatch(r"src_[0-9a-f]{32}", ref.source_id) is None for ref in refs):
        raise AnswerSelectionRejected("source-selection-required")
    return tuple(ref.source_id for ref in refs)


def _scope_refs(
    rows: tuple[ReadyDocumentRevision, ...],
) -> tuple[ResourceRef, ...]:
    return tuple(
        ResourceRef(
            family=SourceFamily.DOC,
            source_id=row.source_id or "",
            requested_revision=row.revision_id,
            mode=ResourceMode.SCOPE,
        )
        for row in rows
    )
