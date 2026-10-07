"""Node-detail enrichment port and an in-memory reference double for tests.

See `adapters/mysql_node_enrichment.py` for the real, MySQL/ArtifactStore-backed
implementation; both resolve to `None` whenever nothing matches rather than
raising, since this is cosmetic enrichment of `GET /nodes/{node_id}`."""

from __future__ import annotations

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.platform.db.project_scope import require_project_scope

__all__ = ["GraphNodeEnrichmentPort", "InMemoryGraphNodeEnrichment"]


class GraphNodeEnrichmentPort(Protocol):
    async def source_name(
        self, scope: ProjectScopeContext, source_revision_id: str
    ) -> str | None: ...

    async def snippet(
        self, scope: ProjectScopeContext, source_revision_id: str, chunk_id: str
    ) -> str | None: ...


class InMemoryGraphNodeEnrichment:
    """Reference double keyed by project; tests `put_source_name`/`put_snippet`
    before publishing a request, mirroring what the real adapter would resolve
    from the Knowledge tables and the chunks blob."""

    def __init__(self) -> None:
        self._source_names: dict[tuple[str, str], str] = {}
        self._snippets: dict[tuple[str, str, str], str] = {}

    def put_source_name(
        self, scope: ProjectScopeContext, source_revision_id: str, name: str
    ) -> None:
        scope = require_project_scope(scope)
        self._source_names[(scope.project_id, source_revision_id)] = name

    def put_snippet(
        self, scope: ProjectScopeContext, source_revision_id: str, chunk_id: str, text: str
    ) -> None:
        scope = require_project_scope(scope)
        self._snippets[(scope.project_id, source_revision_id, chunk_id)] = text

    async def source_name(self, scope: ProjectScopeContext, source_revision_id: str) -> str | None:
        scope = require_project_scope(scope)
        return self._source_names.get((scope.project_id, source_revision_id))

    async def snippet(
        self, scope: ProjectScopeContext, source_revision_id: str, chunk_id: str
    ) -> str | None:
        scope = require_project_scope(scope)
        return self._snippets.get((scope.project_id, source_revision_id, chunk_id))
