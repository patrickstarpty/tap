"""Immutable, provider-neutral values for document ingestion."""

from __future__ import annotations

import json
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
STABLE_ID_SCHEMA = "stable-id-v1"


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
        if len(self.content) > MAX_UPLOAD_BYTES:
            raise DocumentParseRejected("document-too-large")
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
        if not isinstance(self.text, str) or not self.text.strip() or "\0" in self.text:
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
        if self.end_offset - self.start_offset != len(self.text):
            raise ValueError("normalized block offsets must match Unicode code-point length")


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
        block_ids: set[str] = set()
        previous_end = -1
        for position, block in enumerate(self.blocks):
            if block.block_id in block_ids:
                raise ValueError("normalized artifact block IDs must be unique")
            if block.start_offset < previous_end:
                raise ValueError("normalized artifact blocks must be ordered and non-overlapping")
            if block.paragraph_index != position:
                raise ValueError("normalized artifact paragraph indices must be contiguous")
            block_ids.add(block.block_id)
            previous_end = block.end_offset


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
    _required_identity_component("document identity factory value", value)
    return DocumentId("doc_" + _stable_identity_digest("document", {"factoryValue": value})[:32])


def revision_id_for(document_id: DocumentId, source_hash: str, parser_version: str) -> RevisionId:
    _validate_sha256(source_hash)
    _required_identity_component("document ID", document_id)
    _required_identity_component("parser version", parser_version)
    return RevisionId(
        "rev_"
        + _stable_identity_digest(
            "revision",
            {
                "documentId": document_id,
                "parserVersion": parser_version,
                "sourceHash": source_hash,
            },
        )
    )


def logical_chunk_id_for(document_id: DocumentId, anchor_json: str) -> LogicalChunkId:
    _required_identity_component("document ID", document_id)
    _required_identity_component("anchor JSON", anchor_json)
    return LogicalChunkId(
        "lc_"
        + _stable_identity_digest(
            "logical-chunk",
            {"anchorJson": anchor_json, "documentId": document_id},
        )
    )


def chunk_id_for(revision_id: RevisionId, anchor_json: str, chunk_hash: str) -> ChunkId:
    _validate_sha256(chunk_hash)
    _required_identity_component("revision ID", revision_id)
    _required_identity_component("anchor JSON", anchor_json)
    return ChunkId(
        "h_"
        + _stable_identity_digest(
            "chunk",
            {
                "anchorJson": anchor_json,
                "chunkHash": chunk_hash,
                "chunkerVersion": CHUNKER_VERSION,
                "revisionId": revision_id,
            },
        )
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


def _required_identity_component(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _stable_identity_digest(kind: str, fields: dict[str, str]) -> str:
    """Hash a length-unambiguous, sorted JSON identity preimage.

    JSON string escaping and the fixed field names preserve every component boundary, including
    embedded NUL characters. The schema discriminator permits future intentional framing changes.
    """
    payload = {
        "fields": fields,
        "kind": kind,
        "schema": STABLE_ID_SCHEMA,
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()
