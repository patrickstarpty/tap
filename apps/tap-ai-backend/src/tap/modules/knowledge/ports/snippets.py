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

    async def document_ids(self, revision_ids: tuple[str, ...]) -> Mapping[str, str]:
        """Resolve each document revision's document root identity -- needed to
        reconstruct a citation for a snippet-only relation support (one whose
        chunk never became an `S` label) without a second answer-pipeline-owned
        document lookup. A revision that cannot be resolved simply has no
        entry, same convention as `snippets`."""
        ...
