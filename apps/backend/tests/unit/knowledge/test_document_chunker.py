"""Behavioral tests for structure-first token bounded chunks."""

from __future__ import annotations

import json

import pytest

from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.domain.documents import (
    DocumentId,
    DocumentParseRejected,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
)


def _artifact(*blocks: NormalizedBlock) -> NormalizedArtifact:
    text = "\n".join(block.text for block in blocks)
    return NormalizedArtifact(
        filename="policy.md",
        media_type=MediaType.MARKDOWN,
        source_hash=canonical_sha256(text.encode()),
        blocks=blocks,
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
