"""Immutable, provider-neutral values for document ingestion."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from os.path import splitext

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_NORMALIZED_CHARACTERS = 8_000_000
MAX_CHUNKS_PER_DOCUMENT = 10_000
PARSER_VERSION = "athena-parser-v1"
CHUNKER_VERSION = "athena-structure-512-v1"
NORMALIZED_ARTIFACT_SCHEMA = "normalized-artifact-v1"


class DocumentId(str):
    """An opaque document root identity."""


class RevisionId(str):
    """An immutable parsing revision identity."""


class ChunkId(str):
    """An immutable, revision-bound chunk identity."""


class LogicalChunkId(str):
    """A document-bound structural position identity."""


class MediaType(str, Enum):
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    MARKDOWN = "text/markdown"
    TEXT = "text/plain"


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    CODE = "code"
    TABLE_TEXT = "table_text"


class DocumentParseRejected(Exception):
    """A safe, closed parser/chunker failure exposed to ingestion orchestration."""

    _CODES = frozenset(
        {
            "unsupported-document",
            "document-too-large",
            "empty-document",
            "invalid-document",
            "ocr-required",
            "document-too-complex",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("document parser failures must use a closed error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DocumentSource:
    """Untrusted original bytes plus optional durable identities assigned by the ledger."""

    filename: str
    media_type: MediaType
    content: bytes
    document_id: DocumentId | None = None
    revision_id: RevisionId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str) or not self.filename:
            raise ValueError("document filename must be a non-empty string")
        if not isinstance(self.media_type, MediaType) or not isinstance(self.content, bytes):
            raise TypeError("document source must use a closed media type and bytes")
        if (self.document_id is None) != (self.revision_id is None):
            raise ValueError("document and revision identity must be supplied together")


@dataclass(frozen=True, slots=True)
class NormalizedBlock:
    """One addressable normalized source region with Unicode code-point offsets."""

    block_id: str
    kind: BlockKind | str
    text: str
    heading_path: tuple[str, ...]
    page: int | None
    paragraph_index: int
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        try:
            kind = BlockKind(self.kind)
        except ValueError as exc:
            raise ValueError("normalized block kind is not supported") from exc
        object.__setattr__(self, "kind", kind)
        if not isinstance(self.block_id, str) or not self.block_id:
            raise ValueError("normalized block requires a stable ID")
        if not isinstance(self.text, str) or not self.text or "\0" in self.text:
            raise ValueError("normalized block must contain safe text")
        if (
            not isinstance(self.heading_path, tuple)
            or len(self.heading_path) > 32
            or any(
                not isinstance(item, str) or not item or len(item) > 256
                for item in self.heading_path
            )
        ):
            raise ValueError("normalized block heading path is invalid")
        if self.page is not None and (type(self.page) is not int or self.page < 1):
            raise ValueError("normalized block page is invalid")
        if type(self.paragraph_index) is not int or self.paragraph_index < 0:
            raise ValueError("normalized block paragraph index is invalid")
        if (
            type(self.start_offset) is not int
            or type(self.end_offset) is not int
            or self.start_offset < 0
            or self.end_offset <= self.start_offset
        ):
            raise ValueError("normalized block offsets must be ordered non-empty code-point bounds")


@dataclass(frozen=True, slots=True)
class NormalizedArtifact:
    """Frozen parser output persisted independently from provider-specific source formats."""

    filename: str
    media_type: MediaType
    source_hash: str
    blocks: tuple[NormalizedBlock, ...]
    document_id: DocumentId | None = None
    revision_id: RevisionId | None = None
    schema: str = NORMALIZED_ARTIFACT_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str) or not self.filename:
            raise ValueError("normalized artifact requires a filename")
        if not isinstance(self.media_type, MediaType):
            raise TypeError("normalized artifact media type must be closed")
        _validate_sha256(self.source_hash)
        if self.schema != NORMALIZED_ARTIFACT_SCHEMA:
            raise ValueError("normalized artifact schema is not supported")
        if not isinstance(self.blocks, tuple) or not all(
            isinstance(block, NormalizedBlock) for block in self.blocks
        ):
            raise TypeError("normalized artifact blocks must be immutable normalized blocks")
        if (self.document_id is None) != (self.revision_id is None):
            raise ValueError("normalized artifact source identity must be complete")
        if sum(len(block.text) for block in self.blocks) > MAX_NORMALIZED_CHARACTERS:
            raise DocumentParseRejected("document-too-complex")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A vector-free manifest row ready for later embedding and index publication."""

    chunk_id: ChunkId
    logical_chunk_id: LogicalChunkId
    root_id: DocumentId
    parent_id: str | None
    content: str
    anchor_json: str
    source_content_hash: str
    chunk_content_hash: str


def canonical_sha256(data: bytes | Iterable[bytes]) -> str:
    """Hash bytes/byte streams using the canonical public provenance representation."""
    parts: Iterable[bytes] = (data,) if isinstance(data, bytes) else data
    digest = sha256()
    for part in parts:
        if not isinstance(part, bytes):
            raise TypeError("canonical SHA-256 input must yield bytes")
        digest.update(part)
    return "sha256:" + digest.hexdigest()


def new_document_id(id_factory: Callable[[], str]) -> DocumentId:
    value = id_factory()
    if not isinstance(value, str) or not value:
        raise ValueError("document identity factory must return a non-empty string")
    return DocumentId("doc_" + sha256(value.encode("utf-8")).hexdigest()[:32])


def revision_id_for(document_id: DocumentId, source_hash: str, parser_version: str) -> RevisionId:
    _validate_sha256(source_hash)
    if not isinstance(document_id, str) or not document_id or not isinstance(parser_version, str):
        raise ValueError("revision identity inputs must be non-empty strings")
    return RevisionId(
        "rev_" + sha256(f"{document_id}\0{source_hash}\0{parser_version}".encode()).hexdigest()
    )


def logical_chunk_id_for(document_id: DocumentId, anchor_json: str) -> LogicalChunkId:
    if not isinstance(document_id, str) or not document_id or not isinstance(anchor_json, str):
        raise ValueError("logical chunk identity inputs must be non-empty strings")
    return LogicalChunkId("lc_" + sha256(f"{document_id}\0{anchor_json}".encode()).hexdigest())


def chunk_id_for(revision_id: RevisionId, anchor_json: str, chunk_hash: str) -> ChunkId:
    _validate_sha256(chunk_hash)
    if not isinstance(revision_id, str) or not revision_id or not isinstance(anchor_json, str):
        raise ValueError("chunk identity inputs must be non-empty strings")
    return ChunkId(
        "h_"
        + sha256(
            f"{revision_id}\0{anchor_json}\0{chunk_hash}\0{CHUNKER_VERSION}".encode()
        ).hexdigest()
    )


def validate_filename_media_type(filename: str, media_type: MediaType) -> None:
    """Keep parser invocation bound to exactly the four public upload media types."""
    extension = splitext(filename)[1].lower()
    expected = {
        MediaType.PDF: {".pdf"},
        MediaType.DOCX: {".docx"},
        MediaType.MARKDOWN: {".md", ".markdown"},
        MediaType.TEXT: {".txt"},
    }[media_type]
    if extension not in expected:
        raise DocumentParseRejected("unsupported-document")


def _validate_sha256(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("source hash must be a canonical sha256 digest")
