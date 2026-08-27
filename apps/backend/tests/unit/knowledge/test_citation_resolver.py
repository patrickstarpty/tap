from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from tap.modules.knowledge.application.citations import (
    CitationResolver,
    CitationStale,
    CitationUnavailable,
)
from tap.modules.knowledge.domain.documents import (
    BlockKind,
    ChunkDraft,
    DocumentId,
    LogicalChunkId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    revision_id_for,
)
from tap.modules.knowledge.ports.answers import CitationSnapshot, ReadyDocumentRevision
from tap.modules.knowledge.ports.citations import (
    CitationDocumentFacts,
    CitationLookup,
    CitationSnapshotCorrupt,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DocumentState,
    ManifestChunk,
)
from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure, ArtifactUnavailable

DOCUMENT_ID = DocumentId("doc_" + "1" * 32)
SOURCE_HASH = "sha256:" + "a" * 64
REVISION_ID = revision_id_for(DOCUMENT_ID, SOURCE_HASH, "athena-parser-v1")


def fixtures() -> tuple[
    CitationLookup,
    NormalizedArtifact,
    tuple[ChunkDraft, ...],
    str,
    str,
    str,
]:
    prefix = ("前🙂" * 300)[:600]
    content = ("证据🙂" * 1400)[:4_100]
    suffix = ("后🚀" * 300)[:600]
    full = prefix + content + suffix
    normalized = NormalizedArtifact(
        filename="policy.md",
        media_type=MediaType.MARKDOWN,
        source_hash=SOURCE_HASH,
        blocks=(
            NormalizedBlock(
                block_id="b_000000",
                kind=BlockKind.PARAGRAPH,
                text=full,
                heading_path=("政策",),
                page=None,
                paragraph_index=0,
                start_offset=0,
                end_offset=len(full),
            ),
        ),
        document_id=DOCUMENT_ID,
        revision_id=REVISION_ID,
    )
    anchor_json = json.dumps(
        {
            "endOffset": len(prefix) + len(content),
            "headingPath": ["政策"],
            "startOffset": len(prefix),
            "type": "document",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    chunk_hash = canonical_sha256(content.encode("utf-8"))
    chunk_id = chunk_id_for(RevisionId(REVISION_ID), anchor_json, chunk_hash)
    logical_id = logical_chunk_id_for(DOCUMENT_ID, anchor_json)
    chunk = ChunkDraft(
        chunk_id=chunk_id,
        logical_chunk_id=LogicalChunkId(logical_id),
        root_id=DOCUMENT_ID,
        parent_id="b_000000",
        content=content,
        anchor_json=anchor_json,
        source_content_hash=SOURCE_HASH,
        chunk_content_hash=chunk_hash,
    )
    manifest = ManifestChunk(
        chunk_id=str(chunk_id),
        logical_chunk_id=str(logical_id),
        ordinal=0,
        root_id=str(DOCUMENT_ID),
        parent_id="b_000000",
        anchor_json=anchor_json,
        chunk_content_hash=chunk_hash,
        embedding_model_version="athena-embedding",
        index_version="athena-doc-v1",
    )
    citation = CitationSnapshot(
        trace_id="trace-a",
        citation_id="citation-a",
        document_id=str(DOCUMENT_ID),
        revision_id=str(REVISION_ID),
        chunk_id=str(chunk_id),
        source_content_hash=SOURCE_HASH,
        chunk_content_hash=chunk_hash,
        anchor_json=anchor_json,
    )
    selected = ReadyDocumentRevision(str(DOCUMENT_ID), str(REVISION_ID), SOURCE_HASH)
    lookup = CitationLookup(
        citation=citation,
        answer_trace_id="trace-a",
        selected_revisions=(selected,),
        document=CitationDocumentFacts(
            document_id=str(DOCUMENT_ID),
            filename="policy.md",
            status=DocumentState.READY,
            deleted=False,
            current_revision_id=str(REVISION_ID),
            current_source_content_hash=SOURCE_HASH,
            revision_source_content_hash=SOURCE_HASH,
            normalized_locator=ArtifactLocator(
                f"athena-artifacts/revisions/{REVISION_ID}/normalized-v1.json"
            ),
            chunks_locator=ArtifactLocator(
                f"athena-artifacts/revisions/{REVISION_ID}/chunks-v1.jsonl.gz"
            ),
        ),
        manifest=manifest,
    )
    return lookup, normalized, (chunk,), prefix, content, suffix


class MemoryCitationRepository:
    def __init__(self, lookup: CitationLookup | None) -> None:
        self.lookup = lookup
        self.corrupt = False
        self.unavailable = False
        self.current = True
        self.current_checks = 0

    async def load_citation(self, citation_id: str) -> CitationLookup | None:
        if self.corrupt:
            raise CitationSnapshotCorrupt("bad selected revisions")
        if self.unavailable:
            raise RuntimeError("mysql://root:secret@localhost")
        if self.lookup is None or self.lookup.citation.citation_id != citation_id:
            return None
        return self.lookup

    async def citation_is_current(self, citation: CitationSnapshot) -> bool:
        del citation
        self.current_checks += 1
        if self.unavailable:
            raise RuntimeError("mysql unavailable")
        return self.current


class MemoryArtifacts:
    def __init__(
        self,
        normalized: NormalizedArtifact,
        chunks: tuple[ChunkDraft, ...],
    ) -> None:
        self.normalized = normalized
        self.chunks = chunks
        self.error: Exception | None = None
        self.reads: list[str] = []

    async def read_normalized(self, locator: ArtifactLocator) -> NormalizedArtifact:
        self.reads.append(str(locator))
        if self.error is not None:
            raise self.error
        return self.normalized

    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]:
        self.reads.append(str(locator))
        if self.error is not None:
            raise self.error
        return self.chunks


def resolver() -> tuple[CitationResolver, MemoryCitationRepository, MemoryArtifacts]:
    lookup, normalized, chunks, _prefix, _content, _suffix = fixtures()
    repository = MemoryCitationRepository(lookup)
    artifacts = MemoryArtifacts(normalized, chunks)
    return CitationResolver(repository=repository, artifacts=artifacts), repository, artifacts


def test_resolver_returns_exact_unicode_bounded_quote_and_context() -> None:
    async def scenario() -> None:
        citation_resolver, repository, _artifacts = resolver()
        _lookup, _normalized, _chunks, prefix, content, suffix = fixtures()

        preview = await citation_resolver.resolve("citation-a")

        assert preview.quote == content[:4_000]
        assert len(preview.quote) == 4_000
        assert preview.prefix == prefix[-500:]
        assert len(preview.prefix) == 500
        assert preview.suffix == suffix[:500]
        assert len(preview.suffix) == 500
        assert preview.anchor.start_offset == len(prefix)
        assert preview.anchor.end_offset == len(prefix) + len(content)
        assert repository.current_checks == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["missing", "orphan", "unselected", "corrupt"])
def test_snapshot_ownership_or_selection_failure_is_stale_before_blob_io(mode: str) -> None:
    async def scenario() -> None:
        citation_resolver, repository, artifacts = resolver()
        assert repository.lookup is not None
        if mode == "missing":
            repository.lookup = None
        elif mode == "orphan":
            repository.lookup = replace(repository.lookup, answer_trace_id=None)
        elif mode == "unselected":
            repository.lookup = replace(repository.lookup, selected_revisions=())
        else:
            repository.corrupt = True

        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

        assert artifacts.reads == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "document_change",
    [
        {"status": DocumentState.DELETING},
        {"deleted": True},
        {"current_revision_id": "rev_changed"},
        {"current_source_content_hash": "sha256:" + "b" * 64},
        {"revision_source_content_hash": "sha256:" + "b" * 64},
        {"normalized_locator": None},
        {"chunks_locator": None},
    ],
)
def test_document_state_revision_hash_or_locator_failure_is_stale_before_blob_io(
    document_change: dict[str, object],
) -> None:
    async def scenario() -> None:
        citation_resolver, repository, artifacts = resolver()
        assert repository.lookup is not None and repository.lookup.document is not None
        repository.lookup = replace(
            repository.lookup,
            document=replace(repository.lookup.document, **document_change),
        )

        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

        assert artifacts.reads == []

    asyncio.run(scenario())


def test_exact_manifest_anchor_and_chunk_hash_are_required_without_fuzzy_search() -> None:
    async def scenario() -> None:
        citation_resolver, repository, artifacts = resolver()
        assert repository.lookup is not None and repository.lookup.manifest is not None
        repository.lookup = replace(
            repository.lookup,
            manifest=replace(repository.lookup.manifest, anchor_json='{"type":"document"}'),
        )

        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

        repository.lookup, artifacts.normalized, artifacts.chunks, *_ = fixtures()
        target = artifacts.chunks[0]
        full = artifacts.normalized.blocks[0].text
        start = json.loads(target.anchor_json)["startOffset"]
        replaced_text = full[:start] + "近" * 7 + full[start + 7 :]
        artifacts.normalized = replace(
            artifacts.normalized,
            blocks=(replace(artifacts.normalized.blocks[0], text=replaced_text),),
        )
        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

    asyncio.run(scenario())


def test_recomputed_chunk_content_hash_detects_tampering() -> None:
    async def scenario() -> None:
        citation_resolver, _repository, artifacts = resolver()
        chunk = artifacts.chunks[0]
        artifacts.chunks = (replace(chunk, content=chunk.content + "tamper"),)

        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

    asyncio.run(scenario())


def test_duplicate_chunk_identity_and_normalized_layout_gap_are_stale() -> None:
    async def scenario() -> None:
        citation_resolver, _repository, artifacts = resolver()
        artifacts.chunks = (artifacts.chunks[0], artifacts.chunks[0])
        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

        citation_resolver, _repository, artifacts = resolver()
        block = artifacts.normalized.blocks[0]
        artifacts.normalized = replace(
            artifacts.normalized,
            blocks=(replace(block, start_offset=1, end_offset=block.end_offset + 1),),
        )
        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "anchor_value",
    [
        {
            "bbox": [0.0, 0.0, 1.0, 1.0],
            "endOffset": 4_700,
            "headingPath": ["政策"],
            "startOffset": 600,
            "type": "document",
        },
        {
            "endOffset": 4_700,
            "headingPath": ["政策"],
            "type": "document",
        },
    ],
)
def test_bbox_or_missing_offset_anchor_is_stale_even_when_all_artifacts_are_rebound(
    anchor_value: dict[str, object],
) -> None:
    async def scenario() -> None:
        citation_resolver, repository, artifacts = resolver()
        assert repository.lookup is not None and repository.lookup.manifest is not None
        anchor_json = json.dumps(
            anchor_value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        old_chunk = artifacts.chunks[0]
        chunk_id = chunk_id_for(REVISION_ID, anchor_json, old_chunk.chunk_content_hash)
        logical_id = logical_chunk_id_for(DOCUMENT_ID, anchor_json)
        rebound_chunk = replace(
            old_chunk,
            anchor_json=anchor_json,
            chunk_id=chunk_id,
            logical_chunk_id=logical_id,
        )
        citation = replace(
            repository.lookup.citation,
            anchor_json=anchor_json,
            chunk_id=str(chunk_id),
        )
        manifest = replace(
            repository.lookup.manifest,
            anchor_json=anchor_json,
            chunk_id=str(chunk_id),
            logical_chunk_id=str(logical_id),
        )
        repository.lookup = replace(
            repository.lookup,
            citation=citation,
            manifest=manifest,
        )
        artifacts.chunks = (rebound_chunk,)

        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

    asyncio.run(scenario())


def test_repeated_text_resolves_only_the_exact_offset_and_parent_block() -> None:
    async def scenario() -> None:
        citation_resolver, repository, artifacts = resolver()
        assert repository.lookup is not None and repository.lookup.manifest is not None
        repeated = "重复🙂证据"
        second_start = len(repeated) + 2
        normalized = replace(
            artifacts.normalized,
            blocks=(
                NormalizedBlock(
                    block_id="b_first",
                    kind=BlockKind.PARAGRAPH,
                    text=repeated,
                    heading_path=("政策",),
                    page=None,
                    paragraph_index=0,
                    start_offset=0,
                    end_offset=len(repeated),
                ),
                NormalizedBlock(
                    block_id="b_second",
                    kind=BlockKind.PARAGRAPH,
                    text=repeated,
                    heading_path=("政策",),
                    page=None,
                    paragraph_index=1,
                    start_offset=second_start,
                    end_offset=second_start + len(repeated),
                ),
            ),
        )
        anchor_json = json.dumps(
            {
                "endOffset": second_start + len(repeated),
                "headingPath": ["政策"],
                "startOffset": second_start,
                "type": "document",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        chunk_hash = canonical_sha256(repeated.encode("utf-8"))
        chunk_id = chunk_id_for(REVISION_ID, anchor_json, chunk_hash)
        logical_id = logical_chunk_id_for(DOCUMENT_ID, anchor_json)
        chunk = replace(
            artifacts.chunks[0],
            anchor_json=anchor_json,
            chunk_id=chunk_id,
            logical_chunk_id=logical_id,
            parent_id="b_second",
            content=repeated,
            chunk_content_hash=chunk_hash,
        )
        citation = replace(
            repository.lookup.citation,
            anchor_json=anchor_json,
            chunk_id=str(chunk_id),
            chunk_content_hash=chunk_hash,
        )
        manifest = replace(
            repository.lookup.manifest,
            anchor_json=anchor_json,
            chunk_id=str(chunk_id),
            logical_chunk_id=str(logical_id),
            parent_id="b_second",
            chunk_content_hash=chunk_hash,
        )
        repository.lookup = replace(
            repository.lookup,
            citation=citation,
            manifest=manifest,
        )
        artifacts.normalized = normalized
        artifacts.chunks = (chunk,)

        preview = await citation_resolver.resolve("citation-a")

        assert preview.quote == repeated
        assert preview.prefix.endswith("\n\n")
        assert preview.anchor.start_offset == second_start

    asyncio.run(scenario())


def test_artifact_integrity_failure_is_stale_and_provider_outage_is_unavailable() -> None:
    async def scenario() -> None:
        citation_resolver, _repository, artifacts = resolver()
        artifacts.error = ArtifactIntegrityFailure("tampered envelope")
        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

        artifacts.error = ArtifactUnavailable("provider offline")
        with pytest.raises(CitationUnavailable):
            await citation_resolver.resolve("citation-a")

    asyncio.run(scenario())


def test_repository_outage_is_unavailable_and_final_current_recheck_closes_delete_race() -> None:
    async def scenario() -> None:
        citation_resolver, repository, _artifacts = resolver()
        repository.unavailable = True
        with pytest.raises(CitationUnavailable):
            await citation_resolver.resolve("citation-a")

        repository.unavailable = False
        repository.current = False
        with pytest.raises(CitationStale):
            await citation_resolver.resolve("citation-a")

    asyncio.run(scenario())
