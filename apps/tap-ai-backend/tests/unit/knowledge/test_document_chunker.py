"""Behavioral tests for structure-first token bounded chunks."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
import tiktoken

from tap.modules.knowledge.adapters import document_chunker
from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.domain.documents import (
    ChunkId,
    DocumentId,
    DocumentParseRejected,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
)


def _artifact(*blocks: NormalizedBlock) -> NormalizedArtifact:
    ordered = tuple(replace(block, paragraph_index=index) for index, block in enumerate(blocks))
    text = "\n\n".join(block.text for block in ordered)
    return NormalizedArtifact(
        filename="policy.md",
        media_type=MediaType.MARKDOWN,
        source_hash=canonical_sha256(text.encode()),
        blocks=ordered,
        document_id=DocumentId("doc_01JTESTDOCUMENT000000000000"),
        revision_id=RevisionId("rev_test"),
    )


def _block(
    kind: str, text: str, start: int, end: int, *, page: int | None = None
) -> NormalizedBlock:
    return NormalizedBlock(
        block_id=f"b_{start}",
        kind=kind,
        text=text,
        heading_path=("Refunds",),
        page=page,
        paragraph_index=0,
        start_offset=start,
        end_offset=end,
    )


def test_chunker_preserves_small_code_list_and_table_blocks() -> None:
    """Packing through structural boundaries would remove the user's citation locator."""
    code = "```python\nrefund()\n```"
    listing = "- finance approves\n- director approves"
    table = "Role\\tCount\nFinance\\t2"
    artifact = _artifact(
        _block("code", code, 0, len(code)),
        _block("list", listing, len(code) + 2, len(code) + 2 + len(listing)),
        _block("table_text", table, 100, 100 + len(table)),
    )

    chunks = StructuralChunker().chunk(artifact)

    assert [chunk.content for chunk in chunks] == [code, listing, table]
    assert all(chunk.root_id == artifact.document_id for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == 3


def test_chunker_splits_only_oversized_structural_block_with_overlap() -> None:
    """Applying overlap between ordinary blocks creates duplicate citations unnecessarily."""
    words = " ".join(f"word{index}" for index in range(900))
    artifact = _artifact(_block("paragraph", words, 0, len(words)))

    chunks = StructuralChunker().chunk(artifact)

    assert len(chunks) > 1
    assert all(StructuralChunker().token_count(chunk.content) <= 512 for chunk in chunks)
    assert chunks[0].content != chunks[1].content
    assert "word" in chunks[0].content and "word" in chunks[1].content


def test_oversized_chunk_uses_exact_sixty_four_token_overlap() -> None:
    """A changed overlap would lose or duplicate source context at an oversized boundary."""
    text = " ".join(["word"] * 600)
    chunks = StructuralChunker().chunk(_artifact(_block("paragraph", text, 0, len(text))))
    encoding = tiktoken.get_encoding("cl100k_base")

    assert len(chunks) == 2
    assert encoding.encode(chunks[0].content)[-64:] == encoding.encode(chunks[1].content)[:64]


def test_chunker_enforces_an_injected_manifest_limit_before_returning_chunks() -> None:
    """Returning more manifest rows than the durable cap would overload later publishing."""
    artifact = _artifact(
        _block("paragraph", "first", 0, 5),
        _block("paragraph", "next", 7, 11),
        _block("paragraph", "third", 13, 18),
    )

    with pytest.raises(DocumentParseRejected, match="^document-too-complex$"):
        StructuralChunker(max_chunks=2).chunk(artifact)


def test_chunker_constructor_rejects_a_caller_supplied_chunk_identity_factory() -> None:
    """Callers must not be able to replace revision-bound canonical chunk identities."""

    def fabricated_chunk_id(_: RevisionId, __: str, ___: str) -> ChunkId:
        return ChunkId("h_not_bound_to_the_draft")

    with pytest.raises(TypeError):
        StructuralChunker(chunk_id_factory=fabricated_chunk_id)


def test_chunker_detects_a_duplicate_identity_from_its_identity_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collision must fail before a duplicate manifest row can be published."""
    artifact = _artifact(
        _block("paragraph", "first", 0, 5),
        _block("paragraph", "next", 7, 11),
    )

    def duplicate_chunk_id(_: RevisionId, __: str, ___: str) -> ChunkId:
        return ChunkId("h_duplicate")

    with pytest.raises(AssertionError, match="^chunk identities are not unique$"):
        monkeypatch.setattr(document_chunker, "chunk_id_for", duplicate_chunk_id)
        StructuralChunker().chunk(artifact)


def test_chunker_never_splits_a_unicode_code_point_at_a_token_boundary() -> None:
    """A token boundary inside an emoji must not emit replacement text or wrong offsets."""
    text = "x" + "🙂" * 256
    artifact = _artifact(_block("paragraph", text, 0, len(text)))

    chunks = StructuralChunker().chunk(artifact)

    assert all("\ufffd" not in chunk.content for chunk in chunks)
    assert all(json.loads(chunk.anchor_json)["endOffset"] <= len(text) for chunk in chunks)


def test_chunk_anchor_is_compact_sorted_json_with_unicode_offsets() -> None:
    """Noncanonical anchors would create duplicate manifest identities across processes."""
    text = "退款🙂需要两人批准。"
    chunk = StructuralChunker().chunk(_artifact(_block("paragraph", text, 0, len(text), page=2)))[0]

    assert chunk.anchor_json == (
        '{"endOffset":10,"headingPath":["Refunds"],"page":2,"startOffset":0,"type":"document"}'
    )
    assert json.loads(chunk.anchor_json)["endOffset"] == len(text)
    assert chunk.logical_chunk_id.startswith("lc_")
    assert chunk.chunk_id.startswith("h_")


def test_chunker_rejects_artifact_without_answerable_text() -> None:
    """Returning an empty manifest would let worker publishing mark a blank document ready."""
    artifact = _artifact(_block("heading", "Refunds", 0, 7))

    with pytest.raises(DocumentParseRejected, match="^empty-document$"):
        StructuralChunker().chunk(artifact)
