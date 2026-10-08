"""Port for reading raw chunk content to build relation-evidence snippets.

A snippet reader resolves the handful of chunks a relation analysis run needs
fresh text for (an edge whose support chunk is not already an `S` label) by
`chunk_id`, independent of how those chunks are stored.
"""

from __future__ import annotations

from typing import Mapping, Protocol

__all__ = ["ChunkSnippetReader"]


class ChunkSnippetReader(Protocol):
    """Resolves `(document_revision_id, chunk_id)` refs to chunk content.

    The returned mapping is keyed by `chunk_id`; a ref whose chunk cannot be
    resolved (unknown revision, missing chunk, unavailable artifact) simply
    has no entry -- callers must treat a missing key as "no snippet", not as
    an error.
    """

    async def snippets(self, refs: tuple[tuple[str, str], ...]) -> Mapping[str, str]: ...
