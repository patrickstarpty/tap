"""Structure-first, token-bounded chunks with canonical document anchors."""

from __future__ import annotations

import json
from collections.abc import Callable

import tiktoken

from tap.modules.knowledge.domain.documents import (
    MAX_CHUNKS_PER_DOCUMENT,
    BlockKind,
    ChunkDraft,
    ChunkId,
    DocumentParseRejected,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
)

MAX_CHUNK_TOKENS = 512
OVERSIZED_BLOCK_OVERLAP_TOKENS = 64


class StructuralChunker:
    """Keep normal structural blocks intact; split only individually oversized blocks."""

    def __init__(
        self,
        *,
        max_chunks: int = MAX_CHUNKS_PER_DOCUMENT,
        chunk_id_factory: Callable[[RevisionId, str, str], ChunkId] = chunk_id_for,
    ) -> None:
        if type(max_chunks) is not int or not 1 <= max_chunks <= MAX_CHUNKS_PER_DOCUMENT:
            raise ValueError("chunk limit must be within the durable document bound")
        if not callable(chunk_id_factory):
            raise TypeError("chunk identity factory must be callable")
        self._encoding = tiktoken.get_encoding("cl100k_base")
        self._max_chunks = max_chunks
        self._chunk_id_factory = chunk_id_factory

    def token_count(self, content: str) -> int:
        return len(self._encoding.encode(content, disallowed_special=()))

    def chunk(self, artifact: NormalizedArtifact) -> tuple[ChunkDraft, ...]:
        if artifact.document_id is None or artifact.revision_id is None:
            raise DocumentParseRejected("invalid-document")
        chunks: list[ChunkDraft] = []
        for block in artifact.blocks:
            if block.kind is BlockKind.HEADING:
                continue
            chunks.extend(self._chunk_block(artifact, block))
        if not chunks:
            raise DocumentParseRejected("empty-document")
        if len(chunks) > self._max_chunks:
            raise DocumentParseRejected("document-too-complex")
        if any(self.token_count(chunk.content) > MAX_CHUNK_TOKENS for chunk in chunks):
            raise AssertionError("chunker emitted an oversized chunk")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise AssertionError("chunk identities are not unique")
        return tuple(chunks)

    def _chunk_block(
        self, artifact: NormalizedArtifact, block: NormalizedBlock
    ) -> list[ChunkDraft]:
        tokens = self._encoding.encode(block.text, disallowed_special=())
        if len(tokens) <= MAX_CHUNK_TOKENS:
            return [self._draft(artifact, block, block.text, block.start_offset, block.end_offset)]
        decoded, token_offsets = self._encoding.decode_with_offsets(tokens)
        if decoded != block.text:
            raise AssertionError("tokenizer did not preserve normalized text")
        drafts: list[ChunkDraft] = []
        start = 0
        while start < len(tokens):
            start = _code_point_start(token_offsets, start)
            end = _code_point_end(token_offsets, start, len(tokens))
            character_start = token_offsets[start]
            character_end = len(block.text) if end == len(tokens) else token_offsets[end]
            content = block.text[character_start:character_end]
            content_start = block.start_offset + character_start
            content_end = block.start_offset + character_end
            drafts.append(self._draft(artifact, block, content, content_start, content_end))
            if end == len(tokens):
                break
            start = end - OVERSIZED_BLOCK_OVERLAP_TOKENS
        return drafts

    def _draft(
        self,
        artifact: NormalizedArtifact,
        block: NormalizedBlock,
        content: str,
        start_offset: int,
        end_offset: int,
    ) -> ChunkDraft:
        assert artifact.document_id is not None
        assert artifact.revision_id is not None
        anchor = {
            "endOffset": end_offset,
            "headingPath": list(block.heading_path),
            "startOffset": start_offset,
            "type": "document",
        }
        if block.page is not None:
            anchor["page"] = block.page
        anchor_json = json.dumps(anchor, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        chunk_hash = canonical_sha256(content.encode("utf-8"))
        return ChunkDraft(
            chunk_id=self._chunk_id_factory(artifact.revision_id, anchor_json, chunk_hash),
            logical_chunk_id=logical_chunk_id_for(artifact.document_id, anchor_json),
            root_id=artifact.document_id,
            parent_id=block.block_id,
            content=content,
            anchor_json=anchor_json,
            source_content_hash=artifact.source_hash,
            chunk_content_hash=chunk_hash,
        )


def _code_point_start(token_offsets: list[int], start: int) -> int:
    while start > 0 and token_offsets[start] == token_offsets[start - 1]:
        start -= 1
    return start


def _code_point_end(token_offsets: list[int], start: int, total: int) -> int:
    end = min(start + MAX_CHUNK_TOKENS, total)
    while end < total and end > start and token_offsets[end] == token_offsets[end - 1]:
        end -= 1
    if end == start:
        raise AssertionError("one Unicode code point exceeds the chunk token limit")
    return end
