"""Canonical Knowledge artifact codecs shared by storage implementations."""

from __future__ import annotations

import gzip
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import cast

from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    BlockKind,
    ChunkDraft,
    ChunkId,
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
from tap.modules.knowledge.ports.documents import EmbeddingArtifact
from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,512}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NORMALIZED_SCHEMA = "normalized-v1"
_CHUNKS_SCHEMA = "chunks-v1"
_EMBEDDINGS_SCHEMA = "embeddings-v1"
_MAX_BLOCKS = 100_000
_MAX_CHUNKS = 10_000
_MAX_VECTORS = 10_000
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class ArtifactIntegrityError(ArtifactIntegrityFailure):
    """A Blob operation or persisted envelope failed the provider-neutral integrity contract."""


def _persisted_identity(name: str, value: object) -> str:
    try:
        return _identity(name, value)
    except (TypeError, ValueError):
        raise ArtifactIntegrityError(f"persisted {name} is malformed") from None


def encode_normalized_artifact(revision_id: str, artifact: NormalizedArtifact) -> bytes:
    _identity("revision_id", revision_id)
    if (
        not isinstance(artifact, NormalizedArtifact)
        or str(artifact.revision_id) != revision_id
        or artifact.document_id is None
        or str(revision_id_for(artifact.document_id, artifact.source_hash, PARSER_VERSION))
        != revision_id
    ):
        raise ArtifactIntegrityError("normalized artifact identity does not match revision")
    payload = {
        "blocks": [
            {
                "blockId": block.block_id,
                "endOffset": block.end_offset,
                "headingPath": list(block.heading_path),
                "kind": BlockKind(block.kind).value,
                "page": block.page,
                "paragraphIndex": block.paragraph_index,
                "startOffset": block.start_offset,
                "text": block.text,
            }
            for block in artifact.blocks
        ],
        "documentId": str(artifact.document_id),
        "filename": artifact.filename,
        "mediaType": artifact.media_type.value,
        "normalizedSchema": artifact.schema,
    }
    payload_bytes = _canonical_line(payload)
    envelope = {
        "blockCount": len(artifact.blocks),
        "payload": payload,
        "payloadSha256": canonical_sha256(payload_bytes),
        "revisionId": revision_id,
        "schemaVersion": _NORMALIZED_SCHEMA,
        "sourceContentHash": artifact.source_hash,
    }
    return _canonical_line(envelope)


def decode_normalized_artifact(data: bytes, *, expected_revision: str) -> NormalizedArtifact:
    try:
        envelope = _closed_json_line(data)
        _exact_keys(
            envelope,
            {
                "blockCount",
                "payload",
                "payloadSha256",
                "revisionId",
                "schemaVersion",
                "sourceContentHash",
            },
        )
        if (
            envelope["schemaVersion"] != _NORMALIZED_SCHEMA
            or envelope["revisionId"] != expected_revision
        ):
            raise ValueError
        source_hash = _digest(envelope["sourceContentHash"])
        payload = _mapping(envelope["payload"])
        _exact_keys(
            payload,
            {"blocks", "documentId", "filename", "mediaType", "normalizedSchema"},
        )
        if canonical_sha256(_canonical_line(payload)) != _digest(envelope["payloadSha256"]):
            raise ValueError
        raw_blocks = _sequence(payload["blocks"], maximum=_MAX_BLOCKS)
        if envelope["blockCount"] != len(raw_blocks):
            raise ValueError
        blocks = []
        for raw in raw_blocks:
            block = _mapping(raw)
            _exact_keys(
                block,
                {
                    "blockId",
                    "endOffset",
                    "headingPath",
                    "kind",
                    "page",
                    "paragraphIndex",
                    "startOffset",
                    "text",
                },
            )
            heading = _sequence(block["headingPath"], maximum=32, allow_empty=True)
            blocks.append(
                NormalizedBlock(
                    block_id=_text(block["blockId"], maximum=512),
                    kind=BlockKind(_text(block["kind"], maximum=32)),
                    text=_text(block["text"], maximum=8_000_000),
                    heading_path=tuple(_text(item, maximum=256) for item in heading),
                    page=_optional_int(block["page"], minimum=1),
                    paragraph_index=_integer(block["paragraphIndex"], minimum=0),
                    start_offset=_integer(block["startOffset"], minimum=0),
                    end_offset=_integer(block["endOffset"], minimum=1),
                )
            )
        document_id = DocumentId(_text(payload["documentId"], maximum=256))
        if str(revision_id_for(document_id, source_hash, PARSER_VERSION)) != expected_revision:
            raise ValueError
        return NormalizedArtifact(
            filename=_text(payload["filename"], maximum=1024),
            media_type=MediaType(_text(payload["mediaType"], maximum=128)),
            source_hash=source_hash,
            blocks=tuple(blocks),
            document_id=document_id,
            revision_id=RevisionId(expected_revision),
            schema=_text(payload["normalizedSchema"], maximum=128),
        )
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("normalized artifact integrity check failed") from error


def encode_chunks_artifact(revision_id: str, chunks: tuple[ChunkDraft, ...]) -> bytes:
    _identity("revision_id", revision_id)
    if not isinstance(chunks, tuple) or not 1 <= len(chunks) <= _MAX_CHUNKS:
        raise ArtifactIntegrityError("chunk artifact count is outside the bound")
    if not all(isinstance(chunk, ChunkDraft) for chunk in chunks):
        raise ArtifactIntegrityError("chunk artifact contains an invalid row")
    source_hashes = {chunk.source_content_hash for chunk in chunks}
    document_ids = {str(chunk.root_id) for chunk in chunks}
    if len(source_hashes) != 1 or len(document_ids) != 1:
        raise ArtifactIntegrityError("chunk artifact source hash is not exact")
    source_hash = _digest(next(iter(source_hashes)))
    document_id = DocumentId(_text(next(iter(document_ids)), maximum=256))
    if str(revision_id_for(document_id, source_hash, PARSER_VERSION)) != revision_id:
        raise ArtifactIntegrityError("chunk artifact revision provenance is inconsistent")
    payload = b"".join(_canonical_line(_chunk_payload(chunk, revision_id)) for chunk in chunks)
    header = {
        "documentId": str(document_id),
        "itemCount": len(chunks),
        "parserVersion": PARSER_VERSION,
        "payloadSha256": canonical_sha256(payload),
        "revisionId": revision_id,
        "schemaVersion": _CHUNKS_SCHEMA,
        "sourceContentHash": source_hash,
    }
    return gzip.compress(_canonical_line(header) + payload, mtime=0)


def decode_chunks_artifact(data: bytes, *, expected_revision: str) -> tuple[ChunkDraft, ...]:
    try:
        decoded = _bounded_gunzip(data)
        lines = decoded.splitlines(keepends=True)
        if len(lines) < 2 or any(not line.endswith(b"\n") for line in lines):
            raise ValueError
        header = _closed_json_line(lines[0])
        _exact_keys(
            header,
            {
                "documentId",
                "itemCount",
                "parserVersion",
                "payloadSha256",
                "revisionId",
                "schemaVersion",
                "sourceContentHash",
            },
        )
        if header["schemaVersion"] != _CHUNKS_SCHEMA or header["revisionId"] != expected_revision:
            raise ValueError
        source_hash = _digest(header["sourceContentHash"])
        document_id = DocumentId(_text(header["documentId"], maximum=256))
        parser_version = _text(header["parserVersion"], maximum=128)
        if (
            parser_version != PARSER_VERSION
            or str(revision_id_for(document_id, source_hash, parser_version)) != expected_revision
        ):
            raise ValueError
        payload = b"".join(lines[1:])
        if canonical_sha256(payload) != _digest(header["payloadSha256"]):
            raise ValueError
        if header["itemCount"] != len(lines) - 1 or not 1 <= len(lines) - 1 <= _MAX_CHUNKS:
            raise ValueError
        chunks = tuple(
            _chunk_from_payload(_closed_json_line(line), source_hash, expected_revision)
            for line in lines[1:]
        )
        if any(chunk.root_id != document_id for chunk in chunks):
            raise ValueError
        return chunks
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("chunk artifact integrity check failed") from error


def encode_embeddings_artifact(
    revision_id: str,
    source_content_hash: str,
    artifact: EmbeddingArtifact,
) -> bytes:
    _identity("revision_id", revision_id)
    source_hash = _digest(source_content_hash)
    if (
        not isinstance(artifact, EmbeddingArtifact)
        or not 1 <= len(artifact.vectors) <= _MAX_VECTORS
    ):
        raise ArtifactIntegrityError("embedding artifact count is outside the bound")
    payload = b"".join(
        _canonical_line(
            {"chunkId": artifact.chunk_ids[index], "ordinal": index, "vector": list(vector)}
        )
        for index, vector in enumerate(artifact.vectors)
    )
    header = {
        "dimension": artifact.dimension,
        "itemCount": len(artifact.vectors),
        "model": artifact.model_alias,
        "payloadSha256": canonical_sha256(payload),
        "revisionId": revision_id,
        "schemaVersion": _EMBEDDINGS_SCHEMA,
        "sourceContentHash": source_hash,
    }
    return gzip.compress(_canonical_line(header) + payload, mtime=0)


def decode_embeddings_artifact(data: bytes, *, expected_revision: str) -> EmbeddingArtifact:
    try:
        decoded = _bounded_gunzip(data)
        lines = decoded.splitlines(keepends=True)
        if len(lines) < 2 or any(not line.endswith(b"\n") for line in lines):
            raise ValueError
        header = _closed_json_line(lines[0])
        _exact_keys(
            header,
            {
                "dimension",
                "itemCount",
                "model",
                "payloadSha256",
                "revisionId",
                "schemaVersion",
                "sourceContentHash",
            },
        )
        if (
            header["schemaVersion"] != _EMBEDDINGS_SCHEMA
            or header["revisionId"] != expected_revision
        ):
            raise ValueError
        _digest(header["sourceContentHash"])
        payload = b"".join(lines[1:])
        if canonical_sha256(payload) != _digest(header["payloadSha256"]):
            raise ValueError
        count = _integer(header["itemCount"], minimum=1, maximum=_MAX_VECTORS)
        dimension = _integer(header["dimension"], minimum=1, maximum=4096)
        if count != len(lines) - 1:
            raise ValueError
        vectors: list[tuple[float, ...]] = []
        chunk_ids: list[str] = []
        for expected_ordinal, line in enumerate(lines[1:]):
            row = _closed_json_line(line)
            _exact_keys(row, {"chunkId", "ordinal", "vector"})
            if row["ordinal"] != expected_ordinal:
                raise ValueError
            chunk_ids.append(_chunk_identity(row["chunkId"]))
            raw_vector = _sequence(row["vector"], maximum=dimension)
            if len(raw_vector) != dimension:
                raise ValueError
            vectors.append(tuple(_strict_float(item) for item in raw_vector))
        return EmbeddingArtifact(
            model_alias=_text(header["model"], maximum=256),
            dimension=dimension,
            vectors=tuple(vectors),
            chunk_ids=tuple(chunk_ids),
        )
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("embedding artifact integrity check failed") from error


def _chunk_payload(chunk: ChunkDraft, revision_id: str) -> dict[str, object]:
    if not isinstance(chunk, ChunkDraft):
        raise ArtifactIntegrityError("chunk artifact contains an invalid row")
    try:
        anchor = json.loads(chunk.anchor_json, parse_constant=_reject_constant)
        content_hash = canonical_sha256(chunk.content.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("chunk artifact anchor is malformed") from error
    if (
        not isinstance(anchor, dict)
        or json.dumps(anchor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        != chunk.anchor_json
        or content_hash != chunk.chunk_content_hash
        or str(logical_chunk_id_for(chunk.root_id, chunk.anchor_json))
        != str(chunk.logical_chunk_id)
        or str(
            chunk_id_for(
                RevisionId(revision_id),
                chunk.anchor_json,
                chunk.chunk_content_hash,
            )
        )
        != str(chunk.chunk_id)
    ):
        raise ArtifactIntegrityError("chunk artifact provenance is inconsistent")
    return {
        "anchorJson": chunk.anchor_json,
        "chunkContentHash": chunk.chunk_content_hash,
        "chunkId": str(chunk.chunk_id),
        "content": chunk.content,
        "logicalChunkId": str(chunk.logical_chunk_id),
        "parentId": chunk.parent_id,
        "rootId": str(chunk.root_id),
    }


def _chunk_from_payload(
    value: Mapping[str, object], source_hash: str, expected_revision: str
) -> ChunkDraft:
    _exact_keys(
        value,
        {
            "anchorJson",
            "chunkContentHash",
            "chunkId",
            "content",
            "logicalChunkId",
            "parentId",
            "rootId",
        },
    )
    parent = value["parentId"]
    chunk = ChunkDraft(
        chunk_id=ChunkId(_chunk_identity(value["chunkId"])),
        logical_chunk_id=LogicalChunkId(_text(value["logicalChunkId"], maximum=128)),
        root_id=DocumentId(_text(value["rootId"], maximum=256)),
        parent_id=None if parent is None else _text(parent, maximum=256),
        content=_text(value["content"], maximum=32768),
        anchor_json=_text(value["anchorJson"], maximum=16384),
        source_content_hash=source_hash,
        chunk_content_hash=_digest(value["chunkContentHash"]),
    )
    _chunk_payload(chunk, expected_revision)
    return chunk


def _chunk_identity(value: object) -> str:
    text = _text(value, maximum=128)
    if re.fullmatch(r"h_[0-9a-f]{64}", text) is None:
        raise ValueError("chunk identity is not canonical")
    return text


def _canonical_line(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ArtifactIntegrityError("artifact is not canonically serializable") from error


def _closed_json_line(data: bytes) -> Mapping[str, object]:
    if not isinstance(data, bytes) or not data.endswith(b"\n") or data.count(b"\n") != 1:
        raise ValueError("canonical JSON line is malformed")
    value = json.loads(
        data,
        object_pairs_hook=_closed_pairs,
        parse_constant=_reject_constant,
    )
    return _mapping(value)


def _closed_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("artifact JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise ValueError(f"artifact JSON constant is unsupported: {value}")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("artifact JSON value must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, maximum: int, allow_empty: bool = False) -> Sequence[object]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) > maximum
        or (not value and not allow_empty)
    ):
        raise ValueError("artifact JSON array is outside the bound")
    return cast(Sequence[object], value)


def _exact_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("artifact JSON object fields are not closed")


def _bounded_gunzip(data: bytes) -> bytes:
    if not isinstance(data, bytes) or len(data) > _MAX_ARTIFACT_BYTES:
        raise ValueError("compressed artifact is outside the bound")
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
        decoded = stream.read(_MAX_ARTIFACT_BYTES + 1)
    if len(decoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError("decompressed artifact is outside the bound")
    return decoded


def _text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 and character not in "\t\n\r" for character in value)
    ):
        raise ValueError("artifact text is outside the bound")
    return value


def _digest(value: object) -> str:
    text = _text(value, maximum=71)
    if _DIGEST.fullmatch(text) is None:
        raise ValueError("artifact digest is not canonical")
    return text


def _integer(value: object, *, minimum: int, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("artifact integer is outside the bound")
    return value


def _optional_int(value: object, *, minimum: int) -> int | None:
    return None if value is None else _integer(value, minimum=minimum)


def _strict_float(value: object) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("embedding value must be a finite float")
    return value


def _identity(name: str, value: object) -> str:
    try:
        return _safe_path_segment(cast(str, value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a safe identity") from error


def _safe_path_segment(value: str) -> str:
    if not isinstance(value, str) or _SAFE_SEGMENT.fullmatch(value) is None:
        raise ValueError("artifact path segment is unsafe")
    return value
