"""Pure, bounded bytes protocol shared by parser client and isolated worker."""

from __future__ import annotations

import asyncio
import json
import re
import struct
from typing import Any

from tap.modules.knowledge.domain.documents import (
    MAX_UPLOAD_BYTES,
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    RevisionId,
    canonical_sha256,
    revision_id_for,
    validate_filename_media_type,
)

MAX_HEADER = 4096
MAX_REPLY = 32 * 1024 * 1024
MAX_STDERR = 16 * 1024
REQUEST_KEYS = {
    "version",
    "requestId",
    "filename",
    "mediaType",
    "documentId",
    "revisionId",
    "sourceHash",
    "contentLength",
}
SAFE_ERRORS = frozenset(
    {
        "unsupported-document",
        "document-too-large",
        "empty-document",
        "invalid-document",
        "ocr-required",
        "document-too-complex",
        "parser-unavailable",
        "cancelled",
    }
)


class ParserProtocolError(ValueError):
    """Untrusted framing or identity failed; never include input in the message."""

    def __init__(self) -> None:
        super().__init__("invalid parser protocol")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ParserProtocolError()
        result[key] = value
    return result


def encode_header(value: dict[str, Any]) -> bytes:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if not 0 < len(data) <= MAX_HEADER:
        raise ParserProtocolError()
    return struct.pack("!I", len(data)) + data


def decode_header(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or not 0 < len(data) <= MAX_HEADER:
            raise ParserProtocolError()
        return value
    except (ValueError, UnicodeError, TypeError):
        raise ParserProtocolError() from None


async def read_header(reader: asyncio.StreamReader) -> dict[str, Any]:
    try:
        length = struct.unpack("!I", await reader.readexactly(4))[0]
        if not 0 < length <= MAX_HEADER:
            raise ParserProtocolError()
        return decode_header(await reader.readexactly(length))
    except (asyncio.IncompleteReadError, struct.error):
        raise ParserProtocolError() from None


async def read_control(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Only EOF before any control bytes is a normal completed half-close."""
    first = await reader.read(1)
    if not first:
        return None
    try:
        length = struct.unpack("!I", first + await reader.readexactly(3))[0]
        if not 0 < length <= MAX_HEADER:
            raise ParserProtocolError()
        return decode_header(await reader.readexactly(length))
    except (asyncio.IncompleteReadError, struct.error):
        raise ParserProtocolError() from None


def request_length(header: dict[str, Any]) -> int:
    if (
        set(header) != REQUEST_KEYS
        or type(header["version"]) is not int
        or header["version"] != 1
        or type(header["contentLength"]) is not int
        or not 0 <= header["contentLength"] <= MAX_UPLOAD_BYTES
        or not isinstance(header["requestId"], str)
        or not re.fullmatch("[0-9a-f]{32}", header["requestId"])
    ):
        raise ParserProtocolError()
    if any(not isinstance(header[key], str) for key in REQUEST_KEYS - {"version", "contentLength"}):
        raise ParserProtocolError()
    return int(header["contentLength"])


def decode_request(header: dict[str, Any], content: bytes) -> tuple[str, DocumentSource]:
    length = request_length(header)
    try:
        if len(content) != length or canonical_sha256(content) != header["sourceHash"]:
            raise ParserProtocolError()
        document = DocumentId(header["documentId"])
        revision = RevisionId(header["revisionId"])
        if not re.fullmatch("doc_[0-9a-f]{32}", document):
            raise ParserProtocolError()
        if revision_id_for(document, header["sourceHash"], PARSER_VERSION) != revision:
            raise ParserProtocolError()
        filename = header["filename"]
        if not 0 < len(filename) <= 255 or any(ord(c) < 32 for c in filename):
            raise ParserProtocolError()
        media = MediaType(header["mediaType"])
        validate_filename_media_type(filename, media)
        return header["requestId"], DocumentSource(filename, media, content, document, revision)
    except (ValueError, TypeError):
        raise ParserProtocolError() from None


def encode_request(source: DocumentSource, request_id: str) -> bytes:
    header = {
        "version": 1,
        "requestId": request_id,
        "filename": source.filename,
        "mediaType": source.media_type.value,
        "documentId": source.document_id,
        "revisionId": source.revision_id,
        "sourceHash": canonical_sha256(source.content),
        "contentLength": len(source.content),
    }
    decode_request(header, source.content)
    return encode_header(header) + source.content


async def read_request(reader: asyncio.StreamReader) -> tuple[str, DocumentSource]:
    header = await read_header(reader)
    try:
        content = await reader.readexactly(request_length(header))
    except asyncio.IncompleteReadError:
        raise ParserProtocolError() from None
    return decode_request(header, content)


def encode_result(request_id: str, *, payload: bytes = b"", error: str | None = None) -> bytes:
    if len(payload) > MAX_REPLY or (error is not None and error not in SAFE_ERRORS):
        raise ParserProtocolError()
    return (
        encode_header(
            {"version": 1, "requestId": request_id, "error": error, "contentLength": len(payload)}
        )
        + payload
    )


async def read_result(reader: asyncio.StreamReader, request_id: str) -> tuple[str | None, bytes]:
    header = await read_header(reader)
    if (
        set(header) != {"version", "requestId", "error", "contentLength"}
        or type(header["version"]) is not int
        or header["version"] != 1
        or header["requestId"] != request_id
        or type(header["contentLength"]) is not int
        or not 0 <= header["contentLength"] <= MAX_REPLY
        or (header["error"] is not None and not isinstance(header["error"], str))
        or header["error"] not in SAFE_ERRORS | {None}
        or (header["error"] is not None and header["contentLength"] != 0)
    ):
        raise ParserProtocolError()
    try:
        return header["error"], await reader.readexactly(header["contentLength"])
    except asyncio.IncompleteReadError:
        raise ParserProtocolError() from None
