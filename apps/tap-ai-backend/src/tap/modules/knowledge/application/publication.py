"""Current-publication authority for retrieval and generated evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.knowledge.domain.models import DocumentAnchor
from tap.modules.knowledge.domain.review import KnowledgePublication, canonical_digest
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
    publications: tuple[KnowledgePublication, ...] = ()

    def for_revision(self, revision: str) -> PublicationBinding:
        matches = tuple(item for item in self.publications if revision in item.source_revision_ids)
        if len(matches) != 1:
            raise AuthorizationDenied("revision lacks unique current publication")
        return _binding(matches)


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
        binding = await self._current(project_id, source_revision_ids)
        if not source_revision_ids or not set(source_revision_ids) <= set(
            binding.source_revision_ids
        ):
            raise AuthorizationDenied("revision selection is outside the current publication")
        return binding

    async def authorize_hits(
        self, project_id: str, hits: tuple[SearchHit, ...]
    ) -> PublicationBinding:
        binding = await self._current(project_id, tuple(hit.source.revision for hit in hits))
        for hit in hits:
            anchor = hit.source.anchor
            owner = binding.for_revision(hit.source.revision)
            if (
                hit.source.revision not in owner.source_revision_ids
                or hit.index_revision.physical_index != owner.generation
                or not isinstance(anchor, DocumentAnchor)
                or anchor.inventory_item_id not in owner.approved_item_ids
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
        binding = await self._current(project_id, (source_revision_id,))
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
        current = await self._current(expected.project_id, expected.source_revision_ids)
        if current != expected:
            raise AuthorizationDenied("knowledge publication changed during retrieval")
        return current

    async def _current(
        self, project_id: str, revisions: tuple[str, ...] = ()
    ) -> PublicationBinding:
        load_many = getattr(self._repository, "current_publications", None)
        if load_many is None:
            publication = await self._repository.current_publication()
            publications = () if publication is None else (publication,)
        else:
            publications = await load_many()
        selected = tuple(
            sorted(
                (
                    publication
                    for publication in publications
                    if publication.project_id == project_id
                    and publication.status == "published"
                    and publication.expires_at > self._now()
                    and (
                        not revisions
                        or set(revisions).intersection(publication.source_revision_ids)
                    )
                ),
                key=lambda item: item.publication_id,
            )
        )
        if not selected:
            raise AuthorizationDenied("current knowledge publication is unavailable")
        binding = _binding(selected)
        if revisions and not set(revisions) <= set(binding.source_revision_ids):
            raise AuthorizationDenied("revision selection is outside the current publication")
        return binding


def _binding(publications: tuple[KnowledgePublication, ...]) -> PublicationBinding:
    first = publications[0]
    digest = canonical_digest(
        [(item.publication_id, item.approval_digest) for item in publications]
    )
    return PublicationBinding(
        publication_id=first.publication_id if len(publications) == 1 else "set-" + digest[7:47],
        project_id=first.project_id,
        approval_digest=first.approval_digest if len(publications) == 1 else digest,
        source_revision_ids=tuple(
            sorted({value for item in publications for value in item.source_revision_ids})
        ),
        approved_item_ids=tuple(
            sorted({value for item in publications for value in item.approved_item_ids})
        ),
        generation=first.generation,
        expires_at=min(item.expires_at for item in publications),
        publications=publications,
    )
