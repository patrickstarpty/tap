"""Structure-first, token-bounded chunks with canonical document anchors."""

from __future__ import annotations

import json
import re

import tiktoken

from tap.modules.knowledge.domain.documents import (
    MAX_CHUNKS_PER_DOCUMENT,
    BlockKind,
    ChunkDraft,
    DocumentParseRejected,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
)

MAX_CHUNK_TOKENS = 512
OVERSIZED_BLOCK_OVERLAP_TOKENS = 64
# A PDF block is a page-bound clause; prefer a paragraph break once a chunk is this full.
_PARAGRAPH_BREAK_FILL = 0.6
_SHORT_LINE = 40
_LINE_CLOSERS = ("。", "！", "？", "；", "!", "?", ";", ".", "：", ":")
_SENTENCE_END = re.compile(r"[。！？；!?;](?=\s|$|[^\s。！？；!?;”’」』）)])|\n")


class StructuralChunker:
    """Keep normal structural blocks intact; split only individually oversized blocks."""

    def __init__(
        self,
        *,
        max_chunks: int = MAX_CHUNKS_PER_DOCUMENT,
    ) -> None:
        if type(max_chunks) is not int or not 1 <= max_chunks <= MAX_CHUNKS_PER_DOCUMENT:
            raise ValueError("chunk limit must be within the durable document bound")
        self._encoding = tiktoken.get_encoding("cl100k_base")
        self._max_chunks = max_chunks

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
            reasons = {item.reason for item in artifact.parse_inventory}
            if "ocr-required" in reasons:
                raise DocumentParseRejected("ocr-required")
            if "invalid-encoding" in reasons:
                raise DocumentParseRejected("invalid-document")
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
        if self.token_count(block.text) <= MAX_CHUNK_TOKENS:
            return [self._draft(artifact, block, block.text, block.start_offset, block.end_offset)]
        if artifact.media_type is MediaType.PDF:
            return self._sentence_chunks(artifact, block)
        return self._token_windows(artifact, block, 0, len(block.text))

    def _token_windows(
        self, artifact: NormalizedArtifact, block: NormalizedBlock, first: int, last: int
    ) -> list[ChunkDraft]:
        """Split block.text[first:last] into overlapping token windows."""
        text = block.text[first:last]
        tokens = self._encoding.encode(text, disallowed_special=())
        decoded, token_offsets = self._encoding.decode_with_offsets(tokens)
        if decoded != text:
            raise AssertionError("tokenizer did not preserve normalized text")
        drafts: list[ChunkDraft] = []
        start = 0
        while start < len(tokens):
            start = _code_point_start(token_offsets, start)
            end = _code_point_end(token_offsets, start, len(tokens))
            character_start = first + token_offsets[start]
            character_end = last if end == len(tokens) else first + token_offsets[end]
            content = block.text[character_start:character_end]
            content_start = block.start_offset + character_start
            content_end = block.start_offset + character_end
            drafts.append(self._draft(artifact, block, content, content_start, content_end))
            if end == len(tokens):
                break
            start = end - OVERSIZED_BLOCK_OVERLAP_TOKENS
        return drafts

    def _sentence_chunks(
        self, artifact: NormalizedArtifact, block: NormalizedBlock
    ) -> list[ChunkDraft]:
        """Fill chunks up to the token limit, ending each at a paragraph or sentence end.

        A recovered PDF block holds one page of one clause, so whole sentences keep their
        figures and conditions together and no overlap is added. Once a chunk is well
        filled it ends at a paragraph break rather than inside the next paragraph. A single
        sentence above the limit falls back to token windows.
        """
        text = block.text
        ends = [
            match.end()
            for match in _SENTENCE_END.finditer(text)
            if match.group() != "\n" or not _opens_section(text, match.start())
        ]
        if not ends or ends[-1] != len(text):
            ends.append(len(text))
        drafts: list[ChunkDraft] = []
        start = _skip_whitespace(text, 0)
        while start < len(text):
            fitting: list[int] = []
            for end in ends:
                if end <= start:
                    continue
                if self.token_count(text[start:end].rstrip()) > MAX_CHUNK_TOKENS:
                    break
                fitting.append(end)
            if not fitting:
                end = next(end for end in ends if end > start)
                content_end = start + len(text[start:end].rstrip())
                drafts.extend(self._token_windows(artifact, block, start, content_end))
            else:
                end = fitting[-1]
                filled = MAX_CHUNK_TOKENS * _PARAGRAPH_BREAK_FILL
                breaks = [
                    item
                    for item in fitting
                    if text[item - 1] == "\n" and self.token_count(text[start:item]) >= filled
                ]
                if end != len(text) and breaks:
                    end = breaks[-1]
                content_end = start + len(text[start:end].rstrip())
                drafts.append(
                    self._draft(
                        artifact,
                        block,
                        text[start:content_end],
                        block.start_offset + start,
                        block.start_offset + content_end,
                    )
                )
            start = _skip_whitespace(text, end)
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
        if block.inventory_item_id is not None:
            anchor["inventoryItemId"] = block.inventory_item_id
        if block.bbox is not None:
            anchor["bbox"] = list(block.bbox)
        anchor_json = json.dumps(anchor, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        chunk_hash = canonical_sha256(content.encode("utf-8"))
        return ChunkDraft(
            chunk_id=chunk_id_for(artifact.revision_id, anchor_json, chunk_hash),
            logical_chunk_id=logical_chunk_id_for(artifact.document_id, anchor_json),
            root_id=artifact.document_id,
            parent_id=block.block_id,
            content=content,
            anchor_json=anchor_json,
            source_content_hash=artifact.source_hash,
            chunk_content_hash=chunk_hash,
            parser_version=artifact.parser_version,
        )


def _opens_section(text: str, line_end: int) -> bool:
    """A short line without closing punctuation introduces what follows it."""
    line = text[text.rfind("\n", 0, line_end) + 1 : line_end].rstrip()
    return len(line) <= _SHORT_LINE and not line.endswith(_LINE_CLOSERS)


def _skip_whitespace(text: str, start: int) -> int:
    while start < len(text) and text[start].isspace():
        start += 1
    return start


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
