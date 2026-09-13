"""Async application parser client; no Docker, process or provider capability."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from tap.modules.knowledge.adapters.artifact_codecs import decode_normalized_artifact
from tap.modules.knowledge.adapters.parser_protocol import (
    encode_header,
    encode_request,
    read_result,
)
from tap.modules.knowledge.domain.documents import (
    DocumentParseRejected,
    DocumentSource,
    NormalizedArtifact,
    canonical_sha256,
)


class ParserUnavailable(Exception):
    """The isolated parser cannot prove a terminal, cleaned execution."""


class IsolatedParser:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    async def check_ready(self) -> None:
        try:
            async with asyncio.timeout(5):
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
                try:
                    writer.write(encode_header({"version": 1, "health": True}))
                    await writer.drain()
                    error, payload = await read_result(reader, "0" * 32)
                    if error is not None or payload:
                        raise ParserUnavailable()
                finally:
                    writer.close()
                    await writer.wait_closed()
        except Exception:
            raise ParserUnavailable() from None

    async def parse(self, source: DocumentSource) -> NormalizedArtifact:
        request_id = uuid4().hex
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(55):
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
                try:
                    writer.write(encode_request(source, request_id))
                    await writer.drain()
                    error, payload = await read_result(reader, request_id)
                except asyncio.CancelledError:

                    async def cancel() -> None:
                        assert writer is not None
                        async with asyncio.timeout(16):
                            writer.write(encode_header({"version": 1, "cancel": request_id}))
                            await writer.drain()
                            await read_result(reader, request_id)

                    cleanup = asyncio.create_task(cancel())
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not cleanup.cancelled():
                        cleanup.exception()
                    raise
                if error in {"parser-unavailable", "cancelled"}:
                    raise ParserUnavailable()
                if error is not None:
                    raise DocumentParseRejected(error)
                result = decode_normalized_artifact(
                    payload, expected_revision=str(source.revision_id)
                )
                if (
                    result.document_id != source.document_id
                    or result.revision_id != source.revision_id
                    or result.filename != source.filename
                    or result.media_type != source.media_type
                    or result.source_hash != canonical_sha256(source.content)
                ):
                    raise ParserUnavailable()
                return result
        except DocumentParseRejected:
            raise
        except Exception:
            raise ParserUnavailable() from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
