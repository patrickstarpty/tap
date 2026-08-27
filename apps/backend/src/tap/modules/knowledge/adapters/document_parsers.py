"""Closed, local parsers for the four text-extractable document formats."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from tap.modules.knowledge.domain.documents import (
    MAX_UPLOAD_BYTES,
    BlockKind,
    DocumentParseRejected,
    DocumentSource,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    canonical_sha256,
    validate_filename_media_type,
)
from tap.modules.knowledge.ports.documents import DocumentParser

_DOCX_MAX_ENTRIES = 10_000
_DOCX_MAX_DECLARED_BYTES = 100 * 1024 * 1024
_HEADING = re.compile(r"^Heading\s*([1-9])$", re.IGNORECASE)
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$")
_LIST = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)+\|?\s*$")


@dataclass(slots=True)
class _BlockBuilder:
    blocks: list[NormalizedBlock] = field(default_factory=list)
    _cursor: int = 0
    _paragraph_index: int = 0

    def add(
        self,
        kind: BlockKind,
        text: str,
        heading_path: tuple[str, ...],
        *,
        page: int | None = None,
    ) -> None:
        clean = _normalize_text(text).strip("\n")
        if not clean:
            return
        if self.blocks:
            self._cursor += 2
        start = self._cursor
        self._cursor += len(clean)
        self.blocks.append(
            NormalizedBlock(
                block_id=f"b_{len(self.blocks):06d}",
                kind=kind,
                text=clean,
                heading_path=heading_path,
                page=page,
                paragraph_index=self._paragraph_index,
                start_offset=start,
                end_offset=self._cursor,
            )
        )
        self._paragraph_index += 1


class ParserRegistry:
    """Fails closed before dispatching only to a parser for the declared media type."""

    def __init__(self, parsers: Mapping[MediaType, DocumentParser] | None = None) -> None:
        self._parsers = dict(PARSERS if parsers is None else parsers)

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        try:
            validate_filename_media_type(source.filename, source.media_type)
            if len(source.content) > MAX_UPLOAD_BYTES:
                raise DocumentParseRejected("document-too-large")
            parser = self._parsers.get(source.media_type)
            if parser is None:
                raise DocumentParseRejected("unsupported-document")
            return parser.parse(source)
        except DocumentParseRejected:
            raise
        except (ValueError, TypeError, UnicodeError, zipfile.BadZipFile):
            raise DocumentParseRejected("invalid-document") from None
        except Exception:
            raise DocumentParseRejected("invalid-document") from None


class PdfParser:
    """Extract page text only; image-only and encrypted PDFs deliberately fail closed."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        reader = PdfReader(io.BytesIO(source.content), strict=True)
        if reader.is_encrypted:
            raise DocumentParseRejected("invalid-document")
        builder = _BlockBuilder()
        heading_path: tuple[str, ...] = ()
        extracted = False
        for page_number, page in enumerate(reader.pages, start=1):
            raw = page.extract_text() or ""
            text = _normalize_text(raw).strip()
            if "\0" in text:
                raise DocumentParseRejected("invalid-document")
            if not text:
                continue
            extracted = True
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if not lines:
                continue
            if not heading_path:
                heading_path = (lines[0],)
                builder.add(BlockKind.HEADING, lines[0], heading_path, page=page_number)
                lines = lines[1:]
            for line in lines:
                builder.add(BlockKind.PARAGRAPH, line, heading_path, page=page_number)
        if not extracted:
            raise DocumentParseRejected("ocr-required")
        return _artifact(source, builder.blocks)


class DocxParser:
    """Parse a DOCX only after checking its ZIP envelope for unsafe expansion or paths."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        _validate_docx_zip(source.content)
        document = Document(io.BytesIO(source.content))
        builder = _BlockBuilder()
        headings: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                paragraph = Paragraph(child, document)
                text = _normalize_text(paragraph.text).strip()
                if "\0" in text:
                    raise DocumentParseRejected("invalid-document")
                if not text:
                    continue
                level = _heading_level(paragraph)
                if level is not None:
                    headings = headings[: level - 1]
                    headings.append(text)
                    builder.add(BlockKind.HEADING, text, tuple(headings))
                else:
                    builder.add(BlockKind.PARAGRAPH, text, tuple(headings))
            elif child.tag.endswith("}tbl"):
                table = Table(child, document)
                rows = [
                    "\t".join(_normalize_text(cell.text).strip() for cell in row.cells)
                    for row in table.rows
                ]
                text = "\n".join(row for row in rows if row)
                if "\0" in text:
                    raise DocumentParseRejected("invalid-document")
                builder.add(BlockKind.TABLE_TEXT, text, tuple(headings))
        return _artifact(source, builder.blocks)


class MarkdownParser:
    """Retain headings, fenced code, lists, and pipe-table regions as addressable text."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        text = _decode_text(source.content)
        builder = _BlockBuilder()
        headings: list[str] = []
        lines = text.split("\n")
        index = 0
        while index < len(lines):
            line = lines[index]
            match = _ATX_HEADING.match(line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                headings = headings[: level - 1]
                headings.append(title)
                builder.add(BlockKind.HEADING, title, tuple(headings))
                index += 1
                continue
            fence = _FENCE.match(line)
            if fence:
                marker = fence.group(1)
                end = index + 1
                while end < len(lines) and not lines[end].startswith(marker):
                    end += 1
                if end < len(lines):
                    end += 1
                builder.add(BlockKind.CODE, "\n".join(lines[index:end]), tuple(headings))
                index = end
                continue
            if _is_table_start(lines, index):
                end = index + 2
                while end < len(lines) and "|" in lines[end] and lines[end].strip():
                    end += 1
                builder.add(BlockKind.TABLE_TEXT, "\n".join(lines[index:end]), tuple(headings))
                index = end
                continue
            if _LIST.match(line):
                end = index + 1
                while end < len(lines) and _LIST.match(lines[end]):
                    end += 1
                builder.add(BlockKind.LIST, "\n".join(lines[index:end]), tuple(headings))
                index = end
                continue
            if not line.strip():
                index += 1
                continue
            end = index + 1
            while (
                end < len(lines)
                and lines[end].strip()
                and not _ATX_HEADING.match(lines[end])
                and not _FENCE.match(lines[end])
                and not _LIST.match(lines[end])
                and not _is_table_start(lines, end)
            ):
                end += 1
            builder.add(BlockKind.PARAGRAPH, "\n".join(lines[index:end]), tuple(headings))
            index = end
        return _artifact(source, builder.blocks)


class TextParser:
    """Split normalized plain text into ordered Unicode paragraphs."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        text = _decode_text(source.content)
        builder = _BlockBuilder()
        for paragraph in re.split(r"\n[ \t]*\n+", text):
            builder.add(BlockKind.PARAGRAPH, paragraph.strip(), ())
        return _artifact(source, builder.blocks)


PARSERS: Mapping[MediaType, DocumentParser] = {
    MediaType.PDF: PdfParser(),
    MediaType.DOCX: DocxParser(),
    MediaType.MARKDOWN: MarkdownParser(),
    MediaType.TEXT: TextParser(),
}


def _artifact(source: DocumentSource, blocks: list[NormalizedBlock]) -> NormalizedArtifact:
    if not blocks:
        raise DocumentParseRejected("empty-document")
    return NormalizedArtifact(
        filename=source.filename,
        media_type=source.media_type,
        source_hash=canonical_sha256(source.content),
        blocks=tuple(blocks),
        document_id=source.document_id,
        revision_id=source.revision_id,
    )


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _decode_text(content: bytes) -> str:
    text = _normalize_text(content.decode("utf-8"))
    if "\0" in text:
        raise DocumentParseRejected("invalid-document")
    return text


def _validate_docx_zip(content: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > _DOCX_MAX_ENTRIES:
            raise DocumentParseRejected("invalid-document")
        if sum(entry.file_size for entry in entries) > _DOCX_MAX_DECLARED_BYTES:
            raise DocumentParseRejected("invalid-document")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (
                entry.filename.startswith(("/", "\\"))
                or ".." in path.parts
                or "\\" in entry.filename
            ):
                raise DocumentParseRejected("invalid-document")


def _heading_level(paragraph: Paragraph) -> int | None:
    style = paragraph._p.pPr.pStyle if paragraph._p.pPr is not None else None  # noqa: SLF001
    value = style.val if style is not None else ""
    match = _HEADING.match(str(value))
    return int(match.group(1)) if match else None


def _is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and bool(_TABLE_DIVIDER.match(lines[index + 1]))
    )
