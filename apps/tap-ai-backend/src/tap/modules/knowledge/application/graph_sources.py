"""Published knowledge sources that feed the Project graph domain overview."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.graph.application.overview import PublishedSource, PublishedSourceListing
from tap.modules.knowledge.application.publication import (
    PublicationBinding,
    PublishedKnowledgeAuthority,
    is_flowchart_image,
)

OVERVIEW_SOURCE_CAP = 2000

_LOGGER = logging.getLogger(__name__)


class _ReadyRevision(Protocol):
    @property
    def source_id(self) -> str: ...
    @property
    def revision_id(self) -> str: ...
    @property
    def filename(self) -> str: ...


class _ListReadyRevisions(Protocol):
    async def __call__(self, *, limit: int) -> Sequence[_ReadyRevision]: ...


class _FlowchartGate(Protocol):
    async def current(self, project_id: str) -> PublicationBinding | None: ...


class PublishedKnowledgeGraphSources:
    """Every ready, current-revision source of the Project, up to a safety cap.

    ``list_revisions`` applies the same ready/non-deleted/current-revision rules
    as the chat and Library source listing but is called with ``cap + 1`` so a
    cap hit is logged and reported as truncation instead of silently dropping
    older sources. Flowchart images stay hidden until their review is published,
    and revisions denied by a configured publication authority are skipped.
    """

    def __init__(
        self,
        list_revisions: _ListReadyRevisions,
        *,
        project_id: str,
        cap: int = OVERVIEW_SOURCE_CAP,
        gate: _FlowchartGate | None = None,
        authority: PublishedKnowledgeAuthority | None = None,
    ) -> None:
        self._list_revisions = list_revisions
        self._project_id = project_id
        self._cap = cap
        self._gate = gate
        self._authority = authority

    async def published_source_revisions(
        self, scope: ProjectScopeContext
    ) -> PublishedSourceListing:
        if scope.project_id != self._project_id:
            return PublishedSourceListing(())
        items = list(await self._list_revisions(limit=self._cap + 1))
        truncated = len(items) > self._cap
        if truncated:
            _LOGGER.warning("graph overview source listing truncated at %d sources", self._cap)
            items = items[: self._cap]
        if self._gate is not None and any(is_flowchart_image(item.filename) for item in items):
            binding = await self._gate.current(scope.project_id)
            published_images = set() if binding is None else set(binding.source_revision_ids)
            items = [
                item
                for item in items
                if not is_flowchart_image(item.filename) or item.revision_id in published_images
            ]
        published: list[PublishedSource] = []
        seen: set[str] = set()
        for item in items:
            if item.revision_id in seen:
                continue
            seen.add(item.revision_id)
            if self._authority is not None:
                try:
                    await self._authority.authorize_selection(scope.project_id, (item.revision_id,))
                except AuthorizationDenied:
                    continue
            published.append(PublishedSource(item.source_id, item.revision_id))
        return PublishedSourceListing(tuple(published), truncated=truncated)
