"""Unit tests for `ArtifactChunkSnippets` (PR 3 task 3 review fix round 1).

Verifies the two guarantees `RelationAnalysisAgent` relies on: each
revision's chunk artifact is read at most once per `snippets()` call
regardless of how many of its chunks were requested, and a ref that cannot
be resolved (unknown revision, or a chunk missing from an otherwise-readable
artifact) simply has no entry in the result -- never an error.
"""

from __future__ import annotations

from typing import Mapping

import pytest

from tap.modules.knowledge.adapters.artifact_snippets import ArtifactChunkSnippets
from tap.modules.knowledge.domain.documents import ChunkDraft, ChunkId, DocumentId, LogicalChunkId
from tap.modules.knowledge.ports.documents import ArtifactLocator


class _FakeLedger:
    def __init__(self, locators: Mapping[str, ArtifactLocator]) -> None:
        self._locators = dict(locators)
        self.calls: list[tuple[str, ...]] = []

    async def load_chunk_locators(
        self, revision_ids: tuple[str, ...]
    ) -> Mapping[str, ArtifactLocator]:
        self.calls.append(revision_ids)
        return {
            revision_id: self._locators[revision_id]
            for revision_id in revision_ids
            if revision_id in self._locators
        }


class _FakeArtifacts:
    def __init__(self, chunks_by_locator: Mapping[ArtifactLocator, tuple[ChunkDraft, ...]]) -> None:
        self._chunks_by_locator = dict(chunks_by_locator)
        self.read_calls: list[ArtifactLocator] = []

    async def read_normalized(self, locator: ArtifactLocator) -> object:
        raise NotImplementedError

    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]:
        self.read_calls.append(locator)
        return self._chunks_by_locator[locator]


def _chunk(chunk_id: str, content: str) -> ChunkDraft:
    return ChunkDraft(
        chunk_id=ChunkId(chunk_id),
        logical_chunk_id=LogicalChunkId(f"logical-{chunk_id}"),
        root_id=DocumentId("doc-1"),
        parent_id=None,
        content=content,
        anchor_json="{}",
        source_content_hash="sha256:" + "a" * 64,
        chunk_content_hash="sha256:" + "b" * 64,
    )


@pytest.mark.asyncio
async def test_snippets_reads_each_revision_chunk_artifact_once() -> None:
    locator = ArtifactLocator("art-rev-1")
    ledger = _FakeLedger({"rev-1": locator})
    artifacts = _FakeArtifacts(
        {locator: (_chunk("chunk-1", "内容一"), _chunk("chunk-2", "内容二"))}
    )
    reader = ArtifactChunkSnippets(ledger, artifacts)

    result = await reader.snippets((("rev-1", "chunk-1"), ("rev-1", "chunk-2")))

    assert result == {"chunk-1": "内容一", "chunk-2": "内容二"}
    assert artifacts.read_calls == [locator]


@pytest.mark.asyncio
async def test_missing_locator_yields_no_key() -> None:
    reader = ArtifactChunkSnippets(_FakeLedger({}), _FakeArtifacts({}))

    result = await reader.snippets((("rev-missing", "chunk-1"),))

    assert result == {}


@pytest.mark.asyncio
async def test_missing_chunk_in_an_otherwise_readable_artifact_yields_no_key() -> None:
    locator = ArtifactLocator("art-rev-1")
    ledger = _FakeLedger({"rev-1": locator})
    artifacts = _FakeArtifacts({locator: (_chunk("chunk-1", "内容一"),)})
    reader = ArtifactChunkSnippets(ledger, artifacts)

    result = await reader.snippets((("rev-1", "chunk-does-not-exist"),))

    assert result == {}


@pytest.mark.asyncio
async def test_empty_refs_returns_empty_without_calling_the_ledger() -> None:
    ledger = _FakeLedger({})
    reader = ArtifactChunkSnippets(ledger, _FakeArtifacts({}))

    result = await reader.snippets(())

    assert result == {}
    assert ledger.calls == []
