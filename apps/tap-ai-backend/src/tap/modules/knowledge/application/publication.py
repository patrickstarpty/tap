"""Current-publication authority for retrieval and generated evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.knowledge.domain.models import DocumentAnchor
from tap.modules.knowledge.domain.review import KnowledgePublication
from tap.modules.knowledge.ports.models import SearchHit


class CurrentPublicationRepository(Protocol):
    async def current_publication(self) -> KnowledgePublication | None: ...


@dataclass(frozen=True, slots=True)
class PublicationBinding:
    publication_id: str
    project_id: str
    approval_digest: str
    source_revision_ids: tuple[str, ...]
    approved_item_ids: tuple[str, ...]
    generation: str
    expires_at: datetime


class PublishedKnowledgeAuthority:
    """Fail closed unless evidence belongs to the current unexpired publication."""

    def __init__(
        self,
        repository: CurrentPublicationRepository,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._now = now

    async def authorize_selection(
        self, project_id: str, source_revision_ids: tuple[str, ...]
    ) -> PublicationBinding:
        binding = await self._current(project_id)
        if not source_revision_ids or not set(source_revision_ids) <= set(
            binding.source_revision_ids
        ):
            raise AuthorizationDenied("revision selection is outside the current publication")
        return binding

    async def authorize_hits(
        self, project_id: str, hits: tuple[SearchHit, ...]
    ) -> PublicationBinding:
        binding = await self._current(project_id)
        revisions = set(binding.source_revision_ids)
        approved_items = set(binding.approved_item_ids)
        for hit in hits:
            anchor = hit.source.anchor
            if (
                hit.source.revision not in revisions
                or hit.index_revision.physical_index != binding.generation
                or not isinstance(anchor, DocumentAnchor)
                or anchor.inventory_item_id not in approved_items
            ):
                raise AuthorizationDenied("search evidence is outside the current publication")
        return binding

    async def authorize_evidence(
        self,
        project_id: str,
        *,
        source_revision_id: str,
        approved_item_id: str | None,
        document_revision_id: str | None = None,
    ) -> PublicationBinding:
        binding = await self._current(project_id)
        if (
            source_revision_id not in binding.source_revision_ids
            or (
                document_revision_id is not None
                and document_revision_id not in binding.source_revision_ids
            )
            or approved_item_id not in binding.approved_item_ids
        ):
            raise AuthorizationDenied("evidence is outside the current publication")
        return binding

    async def authorize_historical_access(self, project_id: str) -> PublicationBinding:
        """Require current Project publication authority without trusting an old snapshot."""

        return await self._current(project_id)

    async def revalidate(self, expected: PublicationBinding) -> PublicationBinding:
        current = await self._current(expected.project_id)
        if current != expected:
            raise AuthorizationDenied("knowledge publication changed during retrieval")
        return current

    async def _current(self, project_id: str) -> PublicationBinding:
        publication = await self._repository.current_publication()
        if (
            publication is None
            or publication.project_id != project_id
            or publication.status != "published"
            or publication.expires_at <= self._now()
        ):
            raise AuthorizationDenied("current knowledge publication is unavailable")
        return PublicationBinding(
            publication_id=publication.publication_id,
            project_id=publication.project_id,
            approval_digest=publication.approval_digest,
            source_revision_ids=publication.source_revision_ids,
            approved_item_ids=publication.approved_item_ids,
            generation=publication.generation,
            expires_at=publication.expires_at,
        )
