"""`ChunkSnippetReader` backed by the citation artifact store.

Resolves relation-evidence snippets by locating each ref's revision's chunk
artifact through a narrow ledger lookup, reading it once per revision, and
pulling out the requested chunks' content.
"""

from __future__ import annotations

from typing import Mapping, Protocol

from tap.modules.knowledge.ports.citations import CitationArtifactStore
from tap.modules.knowledge.ports.documents import ArtifactLocator

__all__ = ["ArtifactChunkSnippets"]


class _ChunkLocatorLedger(Protocol):
    """Narrow ledger lookup `ArtifactChunkSnippets` needs: a document
    revision's `chunks_blob_locator`. `MysqlDocumentRepository.load_chunk_locators`
    satisfies this; no dedicated `DocumentLedger` type exists in this codebase."""

    async def load_chunk_locators(
        self, revision_ids: tuple[str, ...]
    ) -> Mapping[str, ArtifactLocator]: ...


class ArtifactChunkSnippets:
    """Reads chunk content for relation-evidence snippets.

    Refs are grouped by `document_revision_id` so each revision's chunk
    artifact is located and read at most once per `snippets` call, regardless
    of how many of its chunks were requested.
    """

    def __init__(self, ledger: _ChunkLocatorLedger, artifacts: CitationArtifactStore) -> None:
        self._ledger = ledger
        self._artifacts = artifacts

    async def snippets(self, refs: tuple[tuple[str, str], ...]) -> Mapping[str, str]:
        if not refs:
            return {}
        revision_ids = tuple(dict.fromkeys(revision_id for revision_id, _chunk_id in refs))
        wanted_chunk_ids = {chunk_id for _revision_id, chunk_id in refs}
        locators = await self._ledger.load_chunk_locators(revision_ids)
        result: dict[str, str] = {}
        for revision_id in revision_ids:
            locator = locators.get(revision_id)
            if locator is None:
                continue
            chunks = await self._artifacts.read_chunks(locator)
            for chunk in chunks:
                if chunk.chunk_id in wanted_chunk_ids and chunk.chunk_id not in result:
                    result[chunk.chunk_id] = chunk.content
        return result
