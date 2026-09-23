"""Closed, local parsers for the four text-extractable document formats."""

from __future__ import annotations

import io
import posixpath
import re
import stat
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree  # type: ignore[import-untyped]
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
    canonical_sha256,
    validate_filename_media_type,
)
from tap.modules.knowledge.domain.parse_inventory import (
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


class PdfParser:
    """Extract page text only; image-only and encrypted PDFs deliberately fail closed."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
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
            page_item = _inventory_item(
                source,
                ParseInventoryKind.PAGE,
                f"page:{page_number}",
                ParseInventoryStatus.PARSED if text else ParseInventoryStatus.FAILED,
                text.encode("utf-8") if text else page_bytes,
                reason=None if text else "ocr-required",
            )
            inventory.append(page_item)
            try:
                images = tuple(page.images)
            except Exception:
                images = ()
            for image_number, image in enumerate(images, start=1):
                inventory.append(
                    _inventory_item(
                        source,
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
                    _source_revision_id(source),
                    canonical_sha256(source.content),
                    "empty-document",
                )
            )
        return _artifact(source, builder.blocks, inventory)


class DocxParser:
    """Parse a DOCX only after checking its ZIP envelope for unsafe expansion or paths."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        _validate_docx_zip(source.content)
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
                item = _inventory_item(
                    source,
                    kind,
                    f"paragraph:{paragraph_number}",
                    ParseInventoryStatus.PARSED,
                    text.encode("utf-8"),
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
                    source,
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
                        source,
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
                    _source_revision_id(source),
                    canonical_sha256(source.content),
                    "empty-document",
                )
            )
        return _artifact(source, builder.blocks, inventory)


class MarkdownParser:
    """Retain headings, fenced code, lists, and pipe-table regions as addressable text."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        try:
            text = _decode_text(source.content)
        except UnicodeError:
            return _failed_text_artifact(source, "invalid-encoding")
        builder = _BlockBuilder()
        inventory: list[ParseInventoryItem] = []
        headings: list[str] = []
        lines = text.split("\n")
        index = 0

        def add(kind: BlockKind, content: str, locator: str) -> None:
            item = _inventory_item(
                source,
                _inventory_kind(kind),
                locator,
                ParseInventoryStatus.PARSED,
                _normalize_text(content).strip("\n").encode("utf-8"),
            )
            inventory.append(item)
            builder.add(kind, content, tuple(headings), inventory_item_id=item.item_id)

        while index < len(lines):
            line = lines[index]
            match = _ATX_HEADING.match(line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                headings = headings[: level - 1]
                headings.append(title)
                add(BlockKind.HEADING, title, f"line:{index + 1}:heading")
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
                add(BlockKind.CODE, "\n".join(lines[index:end]), f"line:{index + 1}:code")
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
                )
                index = end
                continue
            if _LIST.match(line):
                end = index + 1
                while end < len(lines) and _LIST.match(lines[end]):
                    end += 1
                add(BlockKind.LIST, "\n".join(lines[index:end]), f"line:{index + 1}:list")
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
            )
            index = end
        if not inventory:
            return _failed_text_artifact(source, "empty-document")
        return _artifact(source, builder.blocks, inventory)


class TextParser:
    """Split normalized plain text into ordered Unicode paragraphs."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact:
        try:
            text = _decode_text(source.content)
        except UnicodeError:
            return _failed_text_artifact(source, "invalid-encoding")
        builder = _BlockBuilder()
        inventory: list[ParseInventoryItem] = []
        for paragraph_number, paragraph in enumerate(re.split(r"\n[ \t]*\n+", text), start=1):
            clean = paragraph.strip()
            if not clean:
                continue
            item = _inventory_item(
                source,
                ParseInventoryKind.PARAGRAPH,
                f"paragraph:{paragraph_number}",
                ParseInventoryStatus.PARSED,
                clean.encode("utf-8"),
            )
            inventory.append(item)
            builder.add(
                BlockKind.PARAGRAPH,
                clean,
                (),
                inventory_item_id=item.item_id,
            )
        if not inventory:
            return _failed_text_artifact(source, "empty-document")
        return _artifact(source, builder.blocks, inventory)


PARSERS: Mapping[MediaType, DocumentParser] = {
    MediaType.PDF: PdfParser(),
    MediaType.DOCX: DocxParser(),
    MediaType.MARKDOWN: MarkdownParser(),
    MediaType.TEXT: TextParser(),
}


def _artifact(
    source: DocumentSource,
    blocks: list[NormalizedBlock],
    inventory: list[ParseInventoryItem],
) -> NormalizedArtifact:
    items = tuple(inventory)
    return NormalizedArtifact(
        filename=source.filename,
        media_type=source.media_type,
        source_hash=canonical_sha256(source.content),
        blocks=tuple(blocks),
        document_id=source.document_id,
        revision_id=source.revision_id,
        parse_inventory=items,
        parser_config_digest=parser_config_digest(source.media_type.value),
        parse_inventory_digest=parse_inventory_digest(items),
    )


def _failed_text_artifact(source: DocumentSource, reason: str) -> NormalizedArtifact:
    items = failed_document_inventory(
        _source_revision_id(source),
        canonical_sha256(source.content),
        reason,
        locator="document:text",
    )
    return _artifact(source, [], list(items))


def _inventory_item(
    source: DocumentSource,
    kind: ParseInventoryKind,
    locator: str,
    status: ParseInventoryStatus,
    content: bytes,
    *,
    reason: str | None = None,
) -> ParseInventoryItem:
    return ParseInventoryItem.create(
        source_revision_id=_source_revision_id(source),
        kind=kind,
        locator=locator,
        status=status,
        reason=reason,
        artifact_digest=canonical_sha256(content),
    )


def _source_revision_id(source: DocumentSource) -> str:
    return str(source.revision_id or canonical_sha256(source.content))


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
