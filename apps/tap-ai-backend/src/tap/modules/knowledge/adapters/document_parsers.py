"""Closed, local parsers for the four text-extractable document formats."""

from __future__ import annotations

import io
import posixpath
import re
import stat
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.parsers import expat

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree  # type: ignore[import-untyped]
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, filters
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from tap.modules.knowledge.domain.documents import (
    MAX_UPLOAD_BYTES,
    BlockKind,
    DocumentParseRejected,
    DocumentSource,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    VisionImageInput,
    canonical_sha256,
    validate_filename_media_type,
)
from tap.modules.knowledge.domain.parse_inventory import (
    MAX_ORIGINAL_EXCERPT_BYTES,
    OriginalExcerptRange,
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    failed_document_inventory,
    parse_inventory_digest,
    parser_config_digest,
)
from tap.modules.knowledge.ports.documents import DocumentParser

_DOCX_MAX_ENTRIES = 2048
_DOCX_MAX_DECLARED_BYTES = 32 * 1024 * 1024
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
        inventory_item_id: str | None = None,
    ) -> None:
        clean = _normalize_text(text).strip("\n")
        if not clean:
            return
        if (
            len(self.blocks) >= 10_000
            or self._cursor + len(clean) > 8_000_000
            or len(heading_path) > 32
            or any(len(h) > 256 for h in heading_path)
        ):
            raise DocumentParseRejected("document-too-complex")
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
                inventory_item_id=inventory_item_id,
            )
        )
        self._paragraph_index += 1


@dataclass(frozen=True, slots=True)
class _PendingTextBlock:
    kind: BlockKind
    content: str
    locator: str
    heading_path: tuple[str, ...]
    source_span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _XmlTextNode:
    text: str
    start_byte: int
    end_byte: int


@dataclass(frozen=True, slots=True)
class _PdfToken:
    kind: str
    start_byte: int
    end_byte: int
    safe_text: bool = False


@dataclass(frozen=True, slots=True)
class _SourceProvenance:
    source: DocumentSource
    digest: str

    @classmethod
    def create(cls, source: DocumentSource) -> _SourceProvenance:
        return cls(source=source, digest=canonical_sha256(source.content))

    @property
    def revision_id(self) -> str:
        return str(self.source.revision_id or self.digest)


class ParserRegistry:
    """Fails closed before dispatching only to a parser for the declared media type."""

    def __init__(self, parsers: Mapping[MediaType, DocumentParser] | None = None) -> None:
        self._parsers = dict(PARSERS if parsers is None else parsers)

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        try:
            validate_filename_media_type(source.filename, source.media_type)
            if len(source.content) > MAX_UPLOAD_BYTES:
                raise DocumentParseRejected("document-too-large")
            _validate_signature(source)
            parser = self._parsers.get(source.media_type)
            if parser is None:
                raise DocumentParseRejected("unsupported-document")
            return parser.parse(source)
        except DocumentParseRejected:
            raise
        except (MemoryError, RecursionError):
            raise DocumentParseRejected("document-too-complex") from None
        except (ValueError, TypeError, UnicodeError, zipfile.BadZipFile):
            raise DocumentParseRejected("invalid-document") from None
        except Exception:
            raise DocumentParseRejected("invalid-document") from None


class ImageParser:
    """Validate one bounded raster original; semantic analysis happens after isolation."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        expected = "PNG" if source.media_type is MediaType.PNG else "JPEG"
        try:
            with Image.open(io.BytesIO(source.content)) as image:
                width, height = image.size
                if (
                    image.format != expected
                    or width < 1
                    or height < 1
                    or width > 8192
                    or height > 8192
                    or width * height > 16_000_000
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise DocumentParseRejected("document-too-complex")
                image.verify()
            with Image.open(io.BytesIO(source.content)) as image:
                if image.getexif().get(274, 1) != 1:
                    raise DocumentParseRejected("document-too-complex")
                image.load()
                if image.mode == "RGB":
                    clean = image.convert("RGB")
                else:
                    rgba = image.convert("RGBA")
                    clean = Image.new("RGB", (width, height), "white")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                if source.media_type is MediaType.JPEG:
                    clean.save(output, format="JPEG", quality=95, optimize=True)
                else:
                    clean.save(output, format="PNG")
                image_bytes = output.getvalue()
                image_media = source.media_type
                sent_size = (width, height)
                if len(image_bytes) > 4 * 1024 * 1024:
                    clean.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                    sent_size = clean.size
                    for quality in (85, 70, 55):
                        output = io.BytesIO()
                        clean.save(output, format="JPEG", quality=quality, optimize=True)
                        image_bytes = output.getvalue()
                        if len(image_bytes) <= 4 * 1024 * 1024:
                            break
                    else:
                        raise DocumentParseRejected("document-too-complex")
                    image_media = MediaType.JPEG
        except DocumentParseRejected:
            raise
        except (OSError, ValueError, UnidentifiedImageError):
            raise DocumentParseRejected("invalid-document") from None
        provenance = _SourceProvenance.create(source)
        item = _inventory_item(
            provenance,
            ParseInventoryKind.IMAGE,
            "image:1",
            ParseInventoryStatus.NEEDS_REVIEW,
            source.content,
            reason="visual-analysis-required",
        )
        return replace(
            _artifact(provenance, [], [item]),
            vision_input=VisionImageInput(image_bytes, image_media, (width, height), sent_size),
        )


class PdfParser:
    """Extract page text only; image-only and encrypted PDFs deliberately fail closed."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        provenance = _SourceProvenance.create(source)
        for setting in (
            "MAX_DECLARED_STREAM_LENGTH",
            "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
            "LZW_MAX_OUTPUT_LENGTH",
            "RUN_LENGTH_MAX_OUTPUT_LENGTH",
            "ZLIB_MAX_OUTPUT_LENGTH",
            "FLATE_MAX_BUFFER_SIZE",
        ):
            setattr(filters, setting, 8 * 1024 * 1024)
        reader = PdfReader(io.BytesIO(source.content), strict=True)
        if reader.is_encrypted:
            raise DocumentParseRejected("invalid-document")
        _validate_pdf_objects(reader)
        if len(reader.pages) > 200:
            raise DocumentParseRejected("document-too-complex")
        decoded_total = 0
        builder = _BlockBuilder()
        inventory: list[ParseInventoryItem] = []
        heading_path: tuple[str, ...] = ()
        for page_number, page in enumerate(reader.pages, start=1):
            contents = page.get_contents()
            page_bytes = b""
            if contents is not None:
                page_bytes = contents.get_data()
                decoded_size = len(page_bytes)
                decoded_total += decoded_size
                if decoded_size > 8 * 1024 * 1024 or decoded_total > 32 * 1024 * 1024:
                    raise DocumentParseRejected("document-too-complex")
            raw = page.extract_text() or ""
            text = _normalize_text(raw).strip()
            if "\0" in text:
                raise DocumentParseRejected("invalid-document")
            content_bounds = _pdf_content_stream_bounds(reader, page, source.content, page_bytes)
            original_excerpt = (
                _pdf_text_operator_excerpt(provenance, page_bytes, text, content_bounds)
                if text and content_bounds is not None
                else None
            )
            page_item = _inventory_item(
                provenance,
                ParseInventoryKind.PAGE,
                f"page:{page_number}",
                ParseInventoryStatus.PARSED if text else ParseInventoryStatus.FAILED,
                text.encode("utf-8") if text else page_bytes,
                reason=None if text else "ocr-required",
                original_excerpt=original_excerpt,
            )
            inventory.append(page_item)
            try:
                images = tuple(page.images)
            except Exception:
                images = ()
            for image_number, image in enumerate(images, start=1):
                inventory.append(
                    _inventory_item(
                        provenance,
                        ParseInventoryKind.IMAGE,
                        f"page:{page_number}/image:{image_number}",
                        ParseInventoryStatus.NEEDS_REVIEW,
                        image.data,
                        reason="image-not-text-extracted",
                    )
                )
            if not text:
                continue
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if not lines:
                continue
            if not heading_path:
                heading_path = (lines[0],)
                builder.add(
                    BlockKind.HEADING,
                    lines[0],
                    heading_path,
                    page=page_number,
                    inventory_item_id=page_item.item_id,
                )
                lines = lines[1:]
            for line in lines:
                builder.add(
                    BlockKind.PARAGRAPH,
                    line,
                    heading_path,
                    page=page_number,
                    inventory_item_id=page_item.item_id,
                )
        if not inventory:
            inventory.extend(
                failed_document_inventory(
                    provenance.revision_id,
                    provenance.digest,
                    "empty-document",
                )
            )
        return _artifact(provenance, builder.blocks, inventory)


class DocxParser:
    """Parse a DOCX only after checking its ZIP envelope for unsafe expansion or paths."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        provenance = _SourceProvenance.create(source)
        _validate_docx_zip(source.content)
        document_xml_bounds = _stored_zip_member_bounds(source.content, "word/document.xml")
        with zipfile.ZipFile(io.BytesIO(source.content)) as archive:
            document_xml = archive.read("word/document.xml")
        paragraph_nodes = _docx_body_paragraph_nodes(document_xml)
        document = Document(io.BytesIO(source.content))
        builder = _BlockBuilder()
        inventory: list[ParseInventoryItem] = []
        headings: list[str] = []
        paragraph_number = 0
        table_number = 0
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                paragraph_number += 1
                paragraph = Paragraph(child, document)
                text = _normalize_text(paragraph.text).strip()
                if "\0" in text:
                    raise DocumentParseRejected("invalid-document")
                if not text:
                    continue
                level = _heading_level(paragraph)
                kind = (
                    ParseInventoryKind.HEADING
                    if level is not None
                    else ParseInventoryKind.PARAGRAPH
                )
                nodes = (
                    paragraph_nodes[paragraph_number - 1]
                    if paragraph_number <= len(paragraph_nodes)
                    else ()
                )
                item = _inventory_item(
                    provenance,
                    kind,
                    f"paragraph:{paragraph_number}",
                    ParseInventoryStatus.PARSED,
                    text.encode("utf-8"),
                    original_excerpt=_docx_paragraph_excerpt(
                        provenance,
                        document_xml_bounds,
                        nodes,
                        text,
                    ),
                )
                inventory.append(item)
                if level is not None:
                    headings = headings[: level - 1]
                    headings.append(text)
                    builder.add(
                        BlockKind.HEADING,
                        text,
                        tuple(headings),
                        inventory_item_id=item.item_id,
                    )
                else:
                    builder.add(
                        BlockKind.PARAGRAPH,
                        text,
                        tuple(headings),
                        inventory_item_id=item.item_id,
                    )
            elif child.tag.endswith("}tbl"):
                table_number += 1
                table = Table(child, document)
                rows = [
                    "\t".join(_normalize_text(cell.text).strip() for cell in row.cells)
                    for row in table.rows
                ]
                text = "\n".join(row for row in rows if row)
                if "\0" in text:
                    raise DocumentParseRejected("invalid-document")
                item = _inventory_item(
                    provenance,
                    ParseInventoryKind.TABLE,
                    f"table:{table_number}",
                    ParseInventoryStatus.PARSED if text else ParseInventoryStatus.FAILED,
                    text.encode("utf-8"),
                    reason=None if text else "empty-table",
                )
                inventory.append(item)
                builder.add(
                    BlockKind.TABLE_TEXT,
                    text,
                    tuple(headings),
                    inventory_item_id=item.item_id,
                )
        with zipfile.ZipFile(io.BytesIO(source.content)) as archive:
            image_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            )
            for image_number, name in enumerate(image_names, start=1):
                inventory.append(
                    _inventory_item(
                        provenance,
                        ParseInventoryKind.IMAGE,
                        f"image:{image_number}:{PurePosixPath(name).name}",
                        ParseInventoryStatus.NEEDS_REVIEW,
                        archive.read(name),
                        reason="image-not-text-extracted",
                    )
                )
        if not inventory:
            inventory.extend(
                failed_document_inventory(
                    provenance.revision_id,
                    provenance.digest,
                    "empty-document",
                )
            )
        return _artifact(provenance, builder.blocks, inventory)


class MarkdownParser:
    """Retain headings, fenced code, lists, and pipe-table regions as addressable text."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        provenance = _SourceProvenance.create(source)
        try:
            text = _decode_text(source.content)
        except UnicodeError:
            return _failed_text_artifact(provenance, "invalid-encoding")
        builder = _BlockBuilder()
        inventory: list[ParseInventoryItem] = []
        pending: list[_PendingTextBlock] = []
        headings: list[str] = []
        lines = text.split("\n")
        line_starts: list[int] = []
        cursor = 0
        for line_number, line in enumerate(lines):
            line_starts.append(cursor)
            cursor += len(line) + (1 if line_number < len(lines) - 1 else 0)
        index = 0

        def add(
            kind: BlockKind,
            content: str,
            locator: str,
            *,
            source_start: int,
            source_end: int,
        ) -> None:
            pending.append(
                _PendingTextBlock(
                    kind,
                    content,
                    locator,
                    tuple(headings),
                    (source_start, source_end),
                )
            )

        while index < len(lines):
            line = lines[index]
            match = _ATX_HEADING.match(line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                headings = headings[: level - 1]
                headings.append(title)
                add(
                    BlockKind.HEADING,
                    title,
                    f"line:{index + 1}:heading",
                    source_start=line_starts[index],
                    source_end=line_starts[index] + len(line),
                )
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
                add(
                    BlockKind.CODE,
                    "\n".join(lines[index:end]),
                    f"line:{index + 1}:code",
                    source_start=line_starts[index],
                    source_end=line_starts[end - 1] + len(lines[end - 1]),
                )
                index = end
                continue
            if _is_table_start(lines, index):
                end = index + 2
                while end < len(lines) and "|" in lines[end] and lines[end].strip():
                    end += 1
                add(
                    BlockKind.TABLE_TEXT,
                    "\n".join(lines[index:end]),
                    f"line:{index + 1}:table",
                    source_start=line_starts[index],
                    source_end=line_starts[end - 1] + len(lines[end - 1]),
                )
                index = end
                continue
            if _LIST.match(line):
                end = index + 1
                while end < len(lines) and _LIST.match(lines[end]):
                    end += 1
                add(
                    BlockKind.LIST,
                    "\n".join(lines[index:end]),
                    f"line:{index + 1}:list",
                    source_start=line_starts[index],
                    source_end=line_starts[end - 1] + len(lines[end - 1]),
                )
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
            add(
                BlockKind.PARAGRAPH,
                "\n".join(lines[index:end]),
                f"line:{index + 1}:paragraph",
                source_start=line_starts[index],
                source_end=line_starts[end - 1] + len(lines[end - 1]),
            )
            index = end
        excerpts = _text_original_excerpts(
            provenance,
            text,
            [item.source_span for item in pending],
        )
        for block, original_excerpt in zip(pending, excerpts, strict=True):
            item = _inventory_item(
                provenance,
                _inventory_kind(block.kind),
                block.locator,
                ParseInventoryStatus.PARSED,
                _normalize_text(block.content).strip("\n").encode("utf-8"),
                original_excerpt=original_excerpt,
            )
            inventory.append(item)
            builder.add(
                block.kind,
                block.content,
                block.heading_path,
                inventory_item_id=item.item_id,
            )
        if not pending:
            return _failed_text_artifact(provenance, "empty-document")
        return _artifact(provenance, builder.blocks, inventory)


class TextParser:
    """Split normalized plain text into ordered Unicode paragraphs."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        provenance = _SourceProvenance.create(source)
        try:
            text = _decode_text(source.content)
        except UnicodeError:
            return _failed_text_artifact(provenance, "invalid-encoding")
        builder = _BlockBuilder()
        inventory: list[ParseInventoryItem] = []
        paragraphs: list[tuple[int, str, tuple[int, int]]] = []
        separators = tuple(re.finditer(r"\n[ \t]*\n+", text))
        starts = (0, *(match.end() for match in separators))
        ends = (*(match.start() for match in separators), len(text))
        for paragraph_number, (start, end) in enumerate(zip(starts, ends, strict=True), start=1):
            paragraph = text[start:end]
            clean = paragraph.strip()
            if not clean:
                continue
            leading = len(paragraph) - len(paragraph.lstrip())
            trailing = len(paragraph) - len(paragraph.rstrip())
            clean_end = end - trailing if trailing else end
            paragraphs.append((paragraph_number, clean, (start + leading, clean_end)))
        excerpts = _text_original_excerpts(
            provenance,
            text,
            [span for _, _, span in paragraphs],
        )
        for (paragraph_number, clean, _), original_excerpt in zip(
            paragraphs, excerpts, strict=True
        ):
            item = _inventory_item(
                provenance,
                ParseInventoryKind.PARAGRAPH,
                f"paragraph:{paragraph_number}",
                ParseInventoryStatus.PARSED,
                clean.encode("utf-8"),
                original_excerpt=original_excerpt,
            )
            inventory.append(item)
            builder.add(
                BlockKind.PARAGRAPH,
                clean,
                (),
                inventory_item_id=item.item_id,
            )
        if not inventory:
            return _failed_text_artifact(provenance, "empty-document")
        return _artifact(provenance, builder.blocks, inventory)


PARSERS: Mapping[MediaType, DocumentParser] = {
    MediaType.PDF: PdfParser(),
    MediaType.DOCX: DocxParser(),
    MediaType.MARKDOWN: MarkdownParser(),
    MediaType.TEXT: TextParser(),
    MediaType.PNG: ImageParser(),
    MediaType.JPEG: ImageParser(),
}


def _artifact(
    provenance: _SourceProvenance,
    blocks: list[NormalizedBlock],
    inventory: list[ParseInventoryItem],
) -> NormalizedArtifact:
    source = provenance.source
    items = tuple(inventory)
    return NormalizedArtifact(
        filename=source.filename,
        media_type=source.media_type,
        source_hash=provenance.digest,
        blocks=tuple(blocks),
        document_id=source.document_id,
        revision_id=source.revision_id,
        parse_inventory=items,
        parser_config_digest=parser_config_digest(source.media_type.value),
        parse_inventory_digest=parse_inventory_digest(items),
    )


def _failed_text_artifact(provenance: _SourceProvenance, reason: str) -> NormalizedArtifact:
    items = failed_document_inventory(
        provenance.revision_id,
        provenance.digest,
        reason,
        locator="document:text",
    )
    return _artifact(provenance, [], list(items))


def _inventory_item(
    provenance: _SourceProvenance,
    kind: ParseInventoryKind,
    locator: str,
    status: ParseInventoryStatus,
    content: bytes,
    *,
    reason: str | None = None,
    original_excerpt: OriginalExcerptRange | None = None,
) -> ParseInventoryItem:
    return ParseInventoryItem.create(
        source_revision_id=provenance.revision_id,
        kind=kind,
        locator=locator,
        status=status,
        reason=reason,
        artifact_digest=canonical_sha256(content),
        original_excerpt=original_excerpt,
        original_alignment_reason=(
            None
            if original_excerpt is not None
            else (
                "source-text-not-stably-addressable"
                if status is ParseInventoryStatus.PARSED
                else "item-original-not-text"
            )
        ),
    )


def _source_excerpt(
    provenance: _SourceProvenance, start_byte: int, end_byte: int
) -> OriginalExcerptRange | None:
    source = provenance.source
    if start_byte < 0 or end_byte <= start_byte or end_byte > len(source.content):
        return None
    bounded_end = min(end_byte, start_byte + MAX_ORIGINAL_EXCERPT_BYTES)
    excerpt = bytes(source.content[start_byte:bounded_end])
    while excerpt:
        try:
            excerpt.decode("utf-8")
            break
        except UnicodeDecodeError as error:
            if error.start < len(excerpt) - 4:
                return None
            excerpt = excerpt[:-1]
    if not excerpt:
        return None
    return OriginalExcerptRange(
        source_digest=provenance.digest,
        start_byte=start_byte,
        end_byte=start_byte + len(excerpt),
        excerpt_digest=canonical_sha256(excerpt),
    )


def _text_original_excerpts(
    provenance: _SourceProvenance,
    normalized_text: str,
    spans: list[tuple[int, int]],
) -> list[OriginalExcerptRange | None]:
    """Map parser-node character spans to source bytes in one forward pass."""
    if not spans:
        return []
    raw_text = provenance.source.content.decode("utf-8")
    boundaries = sorted({value for span in spans for value in span})
    byte_at: dict[int, int] = {}
    boundary_index = 0
    normalized_cursor = 0
    byte_cursor = 0
    raw_cursor = 0

    def record_segment(value: str, raw_value: str, *, direct_ascii: bool) -> bool:
        nonlocal boundary_index, normalized_cursor, byte_cursor
        normalized_end = normalized_cursor + len(value)
        byte_end = byte_cursor + len(raw_value.encode("utf-8"))
        if normalized_text[normalized_cursor:normalized_end] != value:
            return False
        while boundary_index < len(boundaries) and boundaries[boundary_index] <= normalized_end:
            boundary = boundaries[boundary_index]
            if boundary == normalized_cursor:
                byte_at[boundary] = byte_cursor
            elif boundary == normalized_end:
                byte_at[boundary] = byte_end
            elif direct_ascii:
                byte_at[boundary] = byte_cursor + boundary - normalized_cursor
            boundary_index += 1
        normalized_cursor = normalized_end
        byte_cursor = byte_end
        return True

    while raw_cursor < len(raw_text):
        character = raw_text[raw_cursor]
        if character == "\r":
            end = (
                raw_cursor + 2
                if raw_text[raw_cursor : raw_cursor + 2] == "\r\n"
                else raw_cursor + 1
            )
            raw_value = raw_text[raw_cursor:end]
            if not record_segment("\n", raw_value, direct_ascii=False):
                return [None] * len(spans)
            raw_cursor = end
            continue
        next_is_combining = raw_cursor + 1 < len(raw_text) and bool(
            unicodedata.combining(raw_text[raw_cursor + 1])
        )
        if character.isascii() and not next_is_combining:
            end = raw_cursor + 1
            while (
                end < len(raw_text)
                and raw_text[end].isascii()
                and raw_text[end] != "\r"
                and not (end + 1 < len(raw_text) and unicodedata.combining(raw_text[end + 1]))
            ):
                end += 1
            raw_value = raw_text[raw_cursor:end]
            if not record_segment(raw_value, raw_value, direct_ascii=True):
                return [None] * len(spans)
            raw_cursor = end
            continue
        end = raw_cursor + 1
        while end < len(raw_text) and unicodedata.combining(raw_text[end]):
            end += 1
        raw_value = raw_text[raw_cursor:end]
        value = unicodedata.normalize("NFC", raw_value)
        if not record_segment(value, raw_value, direct_ascii=False):
            return [None] * len(spans)
        raw_cursor = end
    if normalized_cursor != len(normalized_text) or boundary_index != len(boundaries):
        return [None] * len(spans)
    return [
        (
            _source_excerpt(provenance, byte_at[start], byte_at[end])
            if start in byte_at and end in byte_at
            else None
        )
        for start, end in spans
    ]


def _pdf_content_stream_bounds(
    reader: PdfReader,
    page: object,
    content: bytes,
    decoded_stream: bytes,
) -> tuple[int, int] | None:
    try:
        reference = page.raw_get("/Contents")  # type: ignore[attr-defined]
        if not isinstance(reference, IndirectObject):
            return None
        offset = reader.xref[reference.generation][reference.idnum]
    except (AttributeError, KeyError, TypeError):
        return None
    start = _pdf_stream_data_start(content, offset)
    if start is None:
        return None
    end = start + len(decoded_stream)
    if content[start:end] != decoded_stream:
        return None
    return start, end


def _pdf_stream_data_start(content: bytes, offset: int) -> int | None:
    """Locate a direct stream only after parsing the xref-target object's dictionary."""
    limit = min(len(content), offset + 64 * 1024)
    cursor = _pdf_skip_space_and_comments(content, offset, limit)
    integer_end = _pdf_skip_unsigned_integer(content, cursor, limit)
    if integer_end is None:
        return None
    cursor = _pdf_skip_space_and_comments(content, integer_end, limit)
    integer_end = _pdf_skip_unsigned_integer(content, cursor, limit)
    if integer_end is None:
        return None
    cursor = _pdf_skip_space_and_comments(content, integer_end, limit)
    if content[cursor : cursor + 3] != b"obj":
        return None
    cursor = _pdf_skip_space_and_comments(content, cursor + 3, limit)
    if content[cursor : cursor + 2] != b"<<":
        return None
    cursor += 2
    depth = 1
    while cursor < limit and depth:
        value = content[cursor]
        if value == ord("%"):
            cursor = _pdf_skip_comment(content, cursor, limit)
        elif value == ord("("):
            string_end = _pdf_skip_literal_string(content, cursor, limit)
            if string_end is None:
                return None
            cursor = string_end
        elif content[cursor : cursor + 2] == b"<<":
            depth += 1
            cursor += 2
        elif content[cursor : cursor + 2] == b">>":
            depth -= 1
            cursor += 2
        elif value == ord("<"):
            string_end = _pdf_skip_hex_string(content, cursor, limit)
            if string_end is None:
                return None
            cursor = string_end
        else:
            cursor += 1
    if depth:
        return None
    cursor = _pdf_skip_space_and_comments(content, cursor, limit)
    if content[cursor : cursor + 6] != b"stream":
        return None
    cursor += 6
    if content[cursor : cursor + 2] == b"\r\n":
        return cursor + 2
    if content[cursor : cursor + 1] in {b"\r", b"\n"}:
        return cursor + 1
    return None


def _pdf_skip_space_and_comments(content: bytes, cursor: int, limit: int) -> int:
    whitespace = b"\x00\t\n\x0c\r "
    while cursor < limit:
        if content[cursor] in whitespace:
            cursor += 1
        elif content[cursor] == ord("%"):
            cursor = _pdf_skip_comment(content, cursor, limit)
        else:
            break
    return cursor


def _pdf_skip_comment(content: bytes, cursor: int, limit: int) -> int:
    while cursor < limit and content[cursor] not in b"\r\n":
        cursor += 1
    return cursor


def _pdf_skip_unsigned_integer(content: bytes, cursor: int, limit: int) -> int | None:
    start = cursor
    while cursor < limit and ord("0") <= content[cursor] <= ord("9"):
        cursor += 1
    return cursor if cursor > start else None


def _pdf_skip_literal_string(content: bytes, cursor: int, limit: int) -> int | None:
    cursor += 1
    depth = 1
    while cursor < limit:
        value = content[cursor]
        if value == ord("\\"):
            cursor += 1
            if cursor < limit and content[cursor : cursor + 2] == b"\r\n":
                cursor += 2
            elif cursor < limit:
                cursor += 1
        elif value == ord("("):
            depth += 1
            cursor += 1
        elif value == ord(")"):
            depth -= 1
            cursor += 1
            if depth == 0:
                return cursor
        else:
            cursor += 1
    return None


def _pdf_skip_hex_string(content: bytes, cursor: int, limit: int) -> int | None:
    cursor += 1
    while cursor < limit:
        if content[cursor] == ord(">"):
            return cursor + 1
        cursor += 1
    return None


def _pdf_tokens(content: bytes) -> tuple[_PdfToken, ...] | None:
    tokens: list[_PdfToken] = []
    index = 0
    whitespace = b"\x00\t\n\x0c\r "
    delimiters = b"()<>[]{}/%"
    while index < len(content):
        value = content[index]
        if value in whitespace:
            index += 1
            continue
        if value == ord("%"):
            index = _pdf_skip_comment(content, index, len(content))
            continue
        if value == ord("("):
            start = index + 1
            index += 1
            depth = 1
            safe = True
            while index < len(content) and depth:
                current = content[index]
                if current == ord("\\"):
                    safe = False
                    index += 2
                    continue
                if current == ord("("):
                    depth += 1
                elif current == ord(")"):
                    depth -= 1
                    if depth == 0:
                        tokens.append(_PdfToken("literal", start, index, safe))
                        index += 1
                        break
                index += 1
            if depth:
                return None
            continue
        if value in b"[]":
            tokens.append(_PdfToken(chr(value), index, index + 1))
            index += 1
            continue
        if value in b"<>/{":
            end = index + 1
            while end < len(content) and content[end] not in whitespace + delimiters:
                end += 1
            tokens.append(_PdfToken("other", index, end))
            index = end
            continue
        end = index + 1
        while end < len(content) and content[end] not in whitespace + delimiters:
            end += 1
        try:
            kind = content[index:end].decode("ascii")
        except UnicodeDecodeError:
            kind = "other"
        if kind == "BI":
            return None
        tokens.append(_PdfToken(kind, index, end))
        index = end
    return tuple(tokens)


def _pdf_text_operator_excerpt(
    provenance: _SourceProvenance,
    decoded_stream: bytes,
    extracted_text: str,
    stream_bounds: tuple[int, int],
) -> OriginalExcerptRange | None:
    in_text = False
    previous: _PdfToken | None = None
    array_literals: list[_PdfToken] | None = None
    closed_array: tuple[_PdfToken, ...] | None = None
    shown: list[_PdfToken | None] = []
    tokens = _pdf_tokens(decoded_stream)
    if tokens is None:
        return None
    for token in tokens:
        if token.kind == "BT":
            in_text = True
        elif token.kind == "ET":
            in_text = False
        elif token.kind == "[" and in_text:
            array_literals = []
            closed_array = None
        elif token.kind == "]" and array_literals is not None:
            closed_array = tuple(array_literals)
            array_literals = None
        elif token.kind == "literal" and array_literals is not None:
            array_literals.append(token)
        elif in_text and token.kind == "Tj":
            shown.append(previous if previous is not None and previous.kind == "literal" else None)
        elif in_text and token.kind == "TJ":
            shown.append(
                closed_array[0] if closed_array is not None and len(closed_array) == 1 else None
            )
        elif in_text and token.kind in {"'", '"'}:
            shown.append(None)
        previous = token
    if len(shown) != 1 or shown[0] is None or not shown[0].safe_text:
        return None
    token = shown[0]
    raw = decoded_stream[token.start_byte : token.end_byte]
    try:
        displayed = _normalize_text(raw.decode("utf-8")).strip()
    except UnicodeDecodeError:
        return None
    if displayed != extracted_text:
        return None
    absolute_start = stream_bounds[0] + token.start_byte
    return _source_excerpt(provenance, absolute_start, absolute_start + len(raw))


def _docx_body_paragraph_nodes(document_xml: bytes) -> tuple[tuple[_XmlTextNode, ...], ...]:
    parser = expat.ParserCreate(namespace_separator="|")
    stack: list[str] = []
    paragraphs: list[tuple[_XmlTextNode, ...]] = []
    paragraph: list[_XmlTextNode] | None = None
    run: list[_XmlTextNode] | None = None
    run_hidden = False
    text_start: int | None = None
    text_parts: list[str] = []

    def local(name: str) -> str:
        return name.rsplit("|", 1)[-1]

    def start_element(name: str, _attributes: Mapping[str, str]) -> None:
        nonlocal paragraph, run, run_hidden, text_start, text_parts
        element = local(name)
        parent = stack[-1] if stack else None
        stack.append(element)
        if element == "p" and parent == "body":
            paragraph = []
        elif element == "r" and paragraph is not None:
            run = []
            run_hidden = False
        elif element == "vanish" and run is not None:
            run_hidden = True
        elif element == "t" and run is not None:
            text_start = _xml_start_tag_content_start(document_xml, parser.CurrentByteIndex)
            if text_start is None:
                raise ValueError("DOCX text tag is unbounded")
            text_parts = []

    def character_data(value: str) -> None:
        if text_start is not None:
            text_parts.append(value)

    def end_element(name: str) -> None:
        nonlocal paragraph, run, text_start, text_parts
        element = local(name)
        if element == "t" and run is not None and text_start is not None:
            run.append(_XmlTextNode("".join(text_parts), text_start, parser.CurrentByteIndex))
            text_start = None
            text_parts = []
        elif element == "r" and paragraph is not None and run is not None:
            if not run_hidden:
                paragraph.extend(run)
            run = None
        elif element == "p" and paragraph is not None:
            paragraphs.append(tuple(paragraph))
            paragraph = None
        stack.pop()

    parser.StartElementHandler = start_element
    parser.CharacterDataHandler = character_data
    parser.EndElementHandler = end_element
    parser.ExternalEntityRefHandler = lambda *_args: 0
    parser.Parse(document_xml, True)
    return tuple(paragraphs)


def _xml_start_tag_content_start(content: bytes, start: int) -> int | None:
    limit = min(len(content), start + 64 * 1024)
    quote: int | None = None
    cursor = start
    while cursor < limit:
        value = content[cursor]
        if quote is not None:
            if value == quote:
                quote = None
        elif value in {ord('"'), ord("'")}:
            quote = value
        elif value == ord(">"):
            return cursor + 1
        cursor += 1
    return None


def _docx_paragraph_excerpt(
    provenance: _SourceProvenance,
    document_xml_bounds: tuple[int, int] | None,
    nodes: tuple[_XmlTextNode, ...],
    extracted_text: str,
) -> OriginalExcerptRange | None:
    if document_xml_bounds is None or len(nodes) != 1:
        return None
    node = nodes[0]
    if _normalize_text(node.text).strip() != extracted_text:
        return None
    start = document_xml_bounds[0] + node.start_byte
    end = document_xml_bounds[0] + node.end_byte
    if end > document_xml_bounds[1]:
        return None
    return _source_excerpt(provenance, start, end)


def _stored_zip_member_bounds(content: bytes, member: str) -> tuple[int, int] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo(member)
            if info.compress_type != zipfile.ZIP_STORED:
                return None
            header = info.header_offset
            if content[header : header + 4] != b"PK\x03\x04":
                return None
            name_length = int.from_bytes(content[header + 26 : header + 28], "little")
            extra_length = int.from_bytes(content[header + 28 : header + 30], "little")
            start = header + 30 + name_length + extra_length
            end = start + info.file_size
            if content[start:end] != archive.read(info):
                return None
            return start, end
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None


def _inventory_kind(kind: BlockKind) -> ParseInventoryKind:
    return {
        BlockKind.HEADING: ParseInventoryKind.HEADING,
        BlockKind.PARAGRAPH: ParseInventoryKind.PARAGRAPH,
        BlockKind.LIST: ParseInventoryKind.LIST,
        BlockKind.CODE: ParseInventoryKind.CODE,
        BlockKind.TABLE_TEXT: ParseInventoryKind.TABLE,
    }[kind]


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _decode_text(content: bytes) -> str:
    text = _normalize_text(content.decode("utf-8"))
    if "\0" in text:
        raise DocumentParseRejected("invalid-document")
    return text


def _validate_signature(source: DocumentSource) -> None:
    content = source.content
    if source.media_type is MediaType.PDF:
        valid = content.startswith(b"%PDF-")
    elif source.media_type is MediaType.DOCX:
        valid = content.startswith(b"PK\x03\x04")
    elif source.media_type is MediaType.PNG:
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif source.media_type is MediaType.JPEG:
        valid = content.startswith(b"\xff\xd8\xff")
    else:
        valid = not content.startswith((b"%PDF-", b"PK\x03\x04", b"\x7fELF", b"MZ", b"\x89PNG"))
    if not valid:
        raise DocumentParseRejected("invalid-document")


def _validate_pdf_objects(reader: PdfReader) -> None:
    count = sum(len(entries) for entries in reader.xref.values()) + len(reader.xref_objStm)
    if count > 50_000 or int(reader.trailer.get("/Size", 0)) > 50_000:
        raise DocumentParseRejected("document-too-complex")
    forbidden = {
        "/JavaScript",
        "/JS",
        "/Launch",
        "/OpenAction",
        "/AA",
        "/URI",
        "/GoToR",
        "/GoToE",
        "/EmbeddedFiles",
        "/EmbeddedFile",
        "/XFA",
    }
    visited: set[tuple[int, int]] = set()
    inspected = 0

    def visit(value: object, depth: int) -> None:
        nonlocal inspected
        inspected += 1
        if depth > 64 or inspected > 200_000:
            raise DocumentParseRejected("document-too-complex")
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key in visited:
                return
            visited.add(key)
            visit(value.get_object(), depth + 1)
        elif isinstance(value, DictionaryObject):
            if forbidden.intersection(value) or str(value.get("/S", "")) in forbidden:
                raise DocumentParseRejected("invalid-document")
            if value.get("/Type") == "/Pages" and int(value.get("/Count", 0)) > 200:
                raise DocumentParseRejected("document-too-complex")
            for child in value.values():
                visit(child, depth + 1)
        elif isinstance(value, ArrayObject):
            for child in value:
                visit(child, depth + 1)

    visit(reader.trailer, 0)
    for generation, entries in reader.xref.items():
        if generation == 65535:
            continue
        for number in entries:
            if number:
                visit(IndirectObject(number, generation, reader), 0)
    for number in reader.xref_objStm:
        visit(IndirectObject(number, 0, reader), 0)


def _validate_docx_zip(content: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > _DOCX_MAX_ENTRIES:
            raise DocumentParseRejected("document-too-complex")
        expanded = sum(entry.file_size for entry in entries)
        compressed = sum(entry.compress_size for entry in entries)
        if expanded > _DOCX_MAX_DECLARED_BYTES or expanded > 100 * max(compressed, 1):
            raise DocumentParseRejected("document-too-complex")
        names: set[str] = set()
        xml_entries: dict[str, bytes] = {}
        actual_total = 0
        for entry in entries:
            path = PurePosixPath(entry.filename)
            name = entry.filename
            lower = name.casefold()
            if (
                name.startswith(("/", "\\"))
                or ".." in path.parts
                or "\\" in name
                or ":" in name
                or "\0" in name
                or lower in names
                or str(path) != name.rstrip("/")
                or stat.S_ISLNK(entry.external_attr >> 16)
                or entry.flag_bits & 1
                or entry.compress_type not in (0, 8)
            ):
                raise DocumentParseRejected("invalid-document")
            names.add(lower)
            if (
                "vbaproject" in lower
                or "/embeddings/" in lower
                or "/activex/" in lower
                or lower.endswith((".js", ".vbs", ".exe", ".py", ".ps1"))
            ):
                raise DocumentParseRejected("invalid-document")
            if entry.file_size > 8 * 1024 * 1024 or entry.file_size > 100 * max(
                entry.compress_size, 1
            ):
                raise DocumentParseRejected("document-too-complex")
            data = bytearray()
            actual = 0
            with archive.open(entry) as stream:
                while chunk := stream.read(65536):
                    actual += len(chunk)
                    actual_total += len(chunk)
                    if actual > 8 * 1024 * 1024 or actual_total > _DOCX_MAX_DECLARED_BYTES:
                        raise DocumentParseRejected("document-too-complex")
                    if lower.endswith((".xml", ".rels")):
                        data.extend(chunk)
            if actual != entry.file_size:
                raise DocumentParseRejected("invalid-document")
            if lower.endswith((".xml", ".rels")):
                xml_entries[name] = bytes(data)
        if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= xml_entries.keys():
            raise DocumentParseRejected("invalid-document")
        office_document = False
        main_type = False
        for name, xml_data in xml_entries.items():
            if b"<!DOCTYPE" in xml_data.upper() or b"<!ENTITY" in xml_data.upper():
                raise DocumentParseRejected("invalid-document")
            root = etree.fromstring(
                xml_data,
                etree.XMLParser(
                    resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
                ),
            )
            if root.getroottree().docinfo.doctype:
                raise DocumentParseRejected("invalid-document")
            for node in root.iter():
                kind = str(node.get("ContentType", "")).lower()
                relation = str(node.get("Type", "")).lower()
                if any(
                    token in kind + relation
                    for token in (
                        "macroenabled",
                        "vbaproject",
                        "oleobject",
                        "activex",
                        "javascript",
                    )
                ):
                    raise DocumentParseRejected("invalid-document")
                if node.get("PartName") == "/word/document.xml":
                    main_type = (
                        kind == "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document.main+xml"
                    )
                if name.endswith(".rels") and str(node.tag).endswith("}Relationship"):
                    target = str(node.get("Target", ""))
                    if (
                        node.get("TargetMode", "Internal") != "Internal"
                        or urlsplit(target).scheme
                        or target.startswith(("/", "\\"))
                        or "\\" in target
                    ):
                        raise DocumentParseRejected("invalid-document")
                    base = name.split("_rels/", 1)[0]
                    resolved = posixpath.normpath(posixpath.join(base, target))
                    if resolved.startswith("../") or resolved not in archive.namelist():
                        raise DocumentParseRejected("invalid-document")
                    if (
                        name == "_rels/.rels"
                        and relation.endswith("/officedocument")
                        and resolved == "word/document.xml"
                    ):
                        office_document = True
        if not main_type or not office_document:
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
