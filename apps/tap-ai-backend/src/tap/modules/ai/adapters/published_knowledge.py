"""Insights evidence from authorized, current Knowledge search results."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.ports.insights import (
    AuthorizedEvidence,
    AuthorizedInsightsScope,
    InsightsAuthorizationChanged,
)
from tap.modules.chat.domain.conversations import FrozenResource
from tap.modules.knowledge.application.publication import (
    PublicationBinding,
    PublishedKnowledgeAuthority,
)
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    ContentRole,
    DocumentAnchor,
    Evidence,
    ResourceMode,
    ResourceRef,
    SearchRequest,
    SearchResponse,
    SourceFamily,
)
from tap.modules.knowledge.ports.answers import DocumentStateChanged, ReadyDocumentRevision


class PublishedSearch(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def resolve_conversation_selection(
        self, revision_ids: tuple[str, ...]
    ) -> tuple[tuple[ReadyDocumentRevision, ...], object]: ...

    async def search(self, request: SearchRequest) -> SearchResponse: ...


class PublishedKnowledgeEvidence:
    """Per-turn adapter. Frozen source facts, policy, and publication are rechecked."""

    def __init__(
        self,
        *,
        searches: PublishedSearch,
        publication_authority: PublishedKnowledgeAuthority | None,
        selection: tuple[FrozenResource, ...],
    ) -> None:
        if len(selection) > 20 or len({item.source_id for item in selection}) != len(selection):
            raise ValueError("frozen knowledge selection must be bounded and unique")
        self._searches = searches
        self._publication = publication_authority
        self._selection = selection
        self._by_citation: dict[str, Evidence] = {}
        self.search_performed = False

    async def retrieve(
        self,
        scope: AuthorizedInsightsScope,
        resource_refs: tuple[str, ...],
        question: str,
    ) -> tuple[AuthorizedEvidence, ...]:
        self._check_scope(scope)
        selected = {item.source_id for item in self._selection}
        requested = tuple(ref for ref in resource_refs if ref in selected)
        if not requested:
            return ()
        if len(requested) > 20 or len(set(requested)) != len(requested):
            raise InsightsAuthorizationChanged("knowledge source selection is invalid")
        await self._check_frozen()
        try:
            response = await self._searches.search(
                SearchRequest(
                    query=question,
                    answer_mode=AnswerMode.QUICK,
                    source_families=(SourceFamily.DOC,),
                    resource_refs=tuple(
                        ResourceRef(SourceFamily.DOC, source_id, ResourceMode.SCOPE)
                        for source_id in requested
                    ),
                )
            )
        except DocumentStateChanged as exc:
            raise InsightsAuthorizationChanged("frozen knowledge source changed") from exc
        self.search_performed = True
        if len(response.evidence) > 10:
            raise InsightsAuthorizationChanged("knowledge evidence exceeded its bound")
        result: list[AuthorizedEvidence] = []
        by_citation: dict[str, Evidence] = {}
        frozen_by_source = {item.source_id: item for item in self._selection}
        for item in response.evidence:
            frozen = frozen_by_source.get(item.source.source_id)
            if (
                item.family is not SourceFamily.DOC
                or item.content_role is not ContentRole.SOURCE
                or item.source.source_id not in requested
                or frozen is None
                or item.source.revision != frozen.revision_id
                or item.source.source_content_hash != frozen.source_content_hash
                or not isinstance(item.source.anchor, DocumentAnchor)
                or (
                    self._publication is not None
                    and (
                        item.approved_item_id != item.source.anchor.inventory_item_id
                        or item.approved_item_id is None
                        or item.publication_id is None
                        or item.approval_digest is None
                    )
                )
                or not item.content.strip()
                or item.citation_id in by_citation
            ):
                raise InsightsAuthorizationChanged("knowledge hit is outside frozen selection")
            binding = await self._authorize_hit(scope, item)
            if binding is not None and (
                binding.publication_id != item.publication_id
                or binding.approval_digest != item.approval_digest
            ):
                raise InsightsAuthorizationChanged("knowledge publication provenance changed")
            by_citation[item.citation_id] = item
            result.append(
                AuthorizedEvidence(
                    citation_id=item.citation_id,
                    resource_ref=item.source.source_id,
                    evidence_version=_version(item),
                    excerpt=item.content[:10_000],
                )
            )
        await self._check_frozen()
        self._by_citation = by_citation
        return tuple(result)

    async def reauthorize(
        self,
        scope: AuthorizedInsightsScope,
        evidence: tuple[AuthorizedEvidence, ...],
    ) -> bool:
        try:
            self._check_scope(scope)
            await self._check_frozen()
            for supplied in evidence:
                item = self._by_citation.get(supplied.citation_id)
                if (
                    item is None
                    or supplied.resource_ref != item.source.source_id
                    or supplied.evidence_version != _version(item)
                    or supplied.excerpt != item.content[:10_000]
                    or supplied.resource_ref not in scope.authorized_resource_refs
                ):
                    return False
                binding = await self._authorize_hit(scope, item)
                if binding is not None and (
                    binding.publication_id != item.publication_id
                    or binding.approval_digest != item.approval_digest
                ):
                    return False
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    def citation_details(
        self, citation_ids: Sequence[str]
    ) -> tuple[dict[str, str | int | None], ...]:
        details = []
        for citation_id in citation_ids:
            item = self._by_citation[citation_id]
            anchor = item.source.anchor
            assert isinstance(anchor, DocumentAnchor)
            details.append(
                {
                    "citationId": citation_id,
                    "sourceId": item.source.source_id,
                    "revisionId": item.source.revision,
                    "chunkId": item.chunk_id,
                    "sourceContentHash": item.source.source_content_hash,
                    "chunkContentHash": item.chunk_content_hash,
                    "publicationId": item.publication_id,
                    "approvalDigest": item.approval_digest,
                    "approvedItemId": item.approved_item_id,
                    "page": anchor.page,
                    "text": item.content[:10_000],
                }
            )
        return tuple(details)

    async def reauthorize_details(
        self,
        scope: AuthorizedInsightsScope,
        details: Sequence[Mapping[str, object]],
    ) -> bool:
        """Reopen current source and publication authority for a durable result read."""

        try:
            self._check_scope(scope)
            if len(details) > 20:
                return False
            await self._check_frozen()
            selected = {item.source_id: item for item in self._selection}
            for detail in details:
                # Report citations are reauthorized by TAP Insights, not Knowledge.
                if "sourceId" not in detail:
                    continue
                source_id = detail.get("sourceId")
                approved_item_id = detail.get("approvedItemId")
                frozen = selected.get(source_id) if isinstance(source_id, str) else None
                if (
                    frozen is None
                    or source_id not in scope.authorized_resource_refs
                    or detail.get("revisionId") != frozen.revision_id
                    or detail.get("sourceContentHash") != frozen.source_content_hash
                    or not isinstance(detail.get("citationId"), str)
                    or not isinstance(detail.get("chunkId"), str)
                    or not isinstance(detail.get("chunkContentHash"), str)
                    or not isinstance(detail.get("text"), str)
                    or (
                        self._publication is not None
                        and (not isinstance(approved_item_id, str) or not approved_item_id)
                    )
                ):
                    return False
                if self._publication is None:
                    if (
                        detail.get("publicationId") is not None
                        or detail.get("approvalDigest") is not None
                    ):
                        return False
                    continue
                assert isinstance(approved_item_id, str)
                binding = await self._publication.authorize_evidence(
                    scope.context.project_id,
                    source_revision_id=frozen.revision_id,
                    approved_item_id=approved_item_id,
                )
                owner = binding.for_revision(frozen.revision_id)
                if (
                    detail.get("publicationId") != owner.publication_id
                    or detail.get("approvalDigest") != owner.approval_digest
                ):
                    return False
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    def _check_scope(self, scope: AuthorizedInsightsScope) -> None:
        if scope.context != self._searches.scope:
            raise InsightsAuthorizationChanged("knowledge project or actor scope changed")
        if not {item.source_id for item in self._selection} <= set(scope.authorized_resource_refs):
            raise InsightsAuthorizationChanged("knowledge source authorization changed")

    async def _check_frozen(self) -> None:
        if not self._selection:
            return
        try:
            current, _policy = await self._searches.resolve_conversation_selection(
                tuple(item.revision_id for item in self._selection)
            )
        except DocumentStateChanged as exc:
            raise InsightsAuthorizationChanged("frozen knowledge source changed") from exc
        if {
            (item.source_id, item.document_id, item.revision_id, item.source_content_hash)
            for item in current
        } != {
            (item.source_id, item.document_id, item.revision_id, item.source_content_hash)
            for item in self._selection
        }:
            raise InsightsAuthorizationChanged("frozen knowledge source changed")

    async def _authorize_hit(
        self, scope: AuthorizedInsightsScope, item: Evidence
    ) -> PublicationBinding | None:
        if self._publication is None:
            if item.publication_id is not None or item.approval_digest is not None:
                raise InsightsAuthorizationChanged("direct knowledge cannot claim an approval")
            return None
        binding = await self._publication.authorize_evidence(
            scope.context.project_id,
            source_revision_id=item.source.revision,
            approved_item_id=item.approved_item_id,
        )
        return binding.for_revision(item.source.revision)


def _version(item: Evidence) -> str:
    facts = (
        item.source.source_id,
        item.source.revision,
        item.source.source_content_hash,
        item.chunk_id,
        item.chunk_content_hash,
        item.publication_id,
        item.approval_digest,
        item.approved_item_id,
    )
    return "sha256:" + hashlib.sha256(json.dumps(facts, separators=(",", ":")).encode()).hexdigest()
