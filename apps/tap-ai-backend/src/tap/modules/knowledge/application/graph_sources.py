"""Published knowledge sources that feed the Project graph domain overview."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.graph.application.overview import PublishedSource
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority


class _ReadySource(Protocol):
    source_id: str
    revision_id: str


class _ReadySourcePage(Protocol):
    @property
    def items(self) -> Sequence[_ReadySource]: ...


class PublishedKnowledgeGraphSources:
    """Ready sources and current revisions, as listed for chat and the Library.

    ``list_sources`` is the same gated listing the review service exposes, so
    flowchart images appear only after their review is published. When a
    publication authority is configured, revisions it denies are skipped.
    """

    def __init__(
        self,
        list_sources: Callable[[], Awaitable[_ReadySourcePage]],
        *,
        project_id: str,
        authority: PublishedKnowledgeAuthority | None = None,
    ) -> None:
        self._list_sources = list_sources
        self._project_id = project_id
        self._authority = authority

    async def published_source_revisions(
        self, scope: ProjectScopeContext
    ) -> tuple[PublishedSource, ...]:
        if scope.project_id != self._project_id:
            return ()
        page = await self._list_sources()
        published: list[PublishedSource] = []
        seen: set[str] = set()
        for item in page.items:
            if item.revision_id in seen:
                continue
            seen.add(item.revision_id)
            if self._authority is not None:
                try:
                    await self._authority.authorize_selection(scope.project_id, (item.revision_id,))
                except AuthorizationDenied:
                    continue
            published.append(PublishedSource(item.source_id, item.revision_id))
        return tuple(published)
