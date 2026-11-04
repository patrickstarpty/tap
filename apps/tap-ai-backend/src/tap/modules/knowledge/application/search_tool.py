"""`knowledge.search` executor over one Turn's already-authorized retrieval."""

from __future__ import annotations

import json
from collections.abc import Mapping

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.tools import ToolResult
from tap.modules.knowledge.ports.errors import ModelUnavailable, SearchUnavailable
from tap.modules.knowledge.ports.search import SearchEvidence

_DEFAULT_LIMIT = 5
_MAX_LIMIT = 8
_MAX_QUERY_CHARS = 500
_MAX_EXCERPT_CHARS = 1200


class KnowledgeSearchTool:
    """Search the Turn's selected, published sources and return labelled evidence.

    The evidence objects travel in `ToolResult.evidence` so the final answer can cite them;
    the text shown to the model carries only short excerpts with local `E` references.
    """

    def __init__(self, search: SearchEvidence, *, scope: ProjectScopeContext) -> None:
        self._search = search
        self._scope = scope

    async def invoke(
        self, scope: ProjectScopeContext, arguments: Mapping[str, object]
    ) -> ToolResult:
        if scope != self._scope:
            return ToolResult("tool-scope-mismatch", is_error=True)
        query = arguments.get("query")
        limit = arguments.get("limit", _DEFAULT_LIMIT)
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > _MAX_QUERY_CHARS
            or type(limit) is not int
            or not 1 <= limit <= _MAX_LIMIT
        ):
            return ToolResult("tool-arguments-invalid", is_error=True)
        try:
            evidence = (await self._search(query.strip(), limit))[:limit]
        except (ModelUnavailable, SearchUnavailable):
            return ToolResult("knowledge-search-unavailable", is_error=True)
        truncated = any(len(item.content) > _MAX_EXCERPT_CHARS for item in evidence)
        content = json.dumps(
            {
                "results": [
                    {
                        "ref": f"E{index}",
                        "title": item.title,
                        "content": item.content[:_MAX_EXCERPT_CHARS],
                    }
                    for index, item in enumerate(evidence, start=1)
                ]
            },
            ensure_ascii=False,
        )
        return ToolResult(content, truncated=truncated, evidence=tuple(evidence))
