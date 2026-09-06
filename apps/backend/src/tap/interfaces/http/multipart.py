"""Bound multipart before FastAPI extracts File parameters; retain its schema."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import Response

from tap.interfaces.http.problems import InvalidDocumentUpload, problem_response
from tap.interfaces.http.scope import _authority_key
from tap.modules.access.domain.policy import AuthorizationDenied

MAX_FILE = 25 * 1024 * 1024
MAX_BODY = MAX_FILE + 64 * 1024


class BoundedMultipartParser(MultiPartParser):
    def __init__(self, request: Request) -> None:
        super().__init__(request.headers, self._stream(request), max_files=1, max_fields=0)
        self.file_bytes = 0
        self.header_bytes = 0
        self.header_count = 0
        self.ended = False
        _, params = parse_options_header(request.headers["content-type"])
        self.closing = b"--" + params[b"boundary"] + b"--"
        self.seen_headers: set[bytes] = set()

    async def _stream(self, request: Request) -> AsyncGenerator[bytes, None]:
        total = 0
        pending = b""
        ending = b""
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BODY:
                raise InvalidDocumentUpload("document-too-large")
            for offset in range(0, len(chunk), 65536):
                pending += chunk[offset : offset + 65536]
                while pending:
                    if self.ended:
                        ending += pending
                        pending = b""
                        if ending not in (b"", b"\r", b"\r\n"):
                            raise InvalidDocumentUpload()
                        break
                    candidate = pending.find(self.closing)
                    size = (
                        candidate + len(self.closing)
                        if candidate >= 0
                        else max(0, len(pending) - len(self.closing) + 1)
                    )
                    if not size:
                        break
                    piece, pending = pending[:size], pending[size:]
                    # Stop each write at a candidate terminator. Only the actual
                    # parser callback identifies whether it ended the multipart;
                    # the next iteration rejects its epilogue before parser.write.
                    yield piece
        if pending:
            if self.ended:
                ending += pending
            else:
                yield pending
        if ending not in (b"", b"\r\n"):
            raise InvalidDocumentUpload()

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self.file_bytes = 0
        self.header_bytes = 0
        self.header_count = 0
        self.seen_headers.clear()

    def _header(self, size: int) -> None:
        self.header_bytes += size
        if self.header_bytes > 8192:
            raise InvalidDocumentUpload()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header(end - start)
        super().on_header_value(data, start, end)

    def on_header_end(self) -> None:
        self.header_count += 1
        key = self._current_partial_header_name.lower()
        if self.header_count > 16 or key in self.seen_headers:
            raise InvalidDocumentUpload()
        self.seen_headers.add(key)
        super().on_header_end()

    def on_headers_finished(self) -> None:
        disposition, params = parse_options_header(self._current_part.content_disposition)
        name = params.get(b"name", b"").decode("utf-8", errors="strict")
        if _authority_key(name):
            raise AuthorizationDenied("caller-authority-forbidden")
        if disposition != b"form-data" or name != "upload" or b"filename" not in params:
            raise InvalidDocumentUpload()
        super().on_headers_finished()

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.file_bytes += end - start
        if self.file_bytes > MAX_FILE:
            raise InvalidDocumentUpload("document-too-large")
        super().on_part_data(data, start, end)

    def on_end(self) -> None:
        self.ended = True

    def close_files(self) -> None:
        for spool in self._files_to_close_on_error:
            spool.close()


class BoundedUploadRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()
        if self.endpoint.__name__ != "upload_document":
            return original

        async def bounded(request: Request) -> Response:
            active = getattr(request.app.state, "active_upload_forms", 0)
            if active >= 2:
                return problem_response("knowledge-runtime-unavailable", request)
            request.app.state.active_upload_forms = active + 1
            parser: BoundedMultipartParser | None = None
            try:
                if any(
                    _authority_key(k)
                    for values in (request.headers, request.cookies, request.query_params)
                    for k in values
                ):
                    raise AuthorizationDenied("caller-authority-forbidden")
                lengths = request.headers.getlist("content-length")
                if (
                    len(lengths) > 1
                    or (lengths and not lengths[0].isascii())
                    or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 10))
                ):
                    raise InvalidDocumentUpload()
                if lengths and int(lengths[0]) > MAX_BODY:
                    raise InvalidDocumentUpload("document-too-large")
                media, params = parse_options_header(request.headers.get("content-type", ""))
                boundary = params.get(b"boundary", b"")
                if media != b"multipart/form-data" or not 1 <= len(boundary) <= 70:
                    raise InvalidDocumentUpload()
                parser = BoundedMultipartParser(request)
                async with asyncio.timeout(30):
                    request._form = await parser.parse()  # bounded cache shared with dependencies
                if not parser.ended or len(request._form.multi_items()) != 1:
                    raise InvalidDocumentUpload()
                return await original(request)
            except (MultiPartException, MultipartParseError, UnicodeError, TimeoutError):
                raise InvalidDocumentUpload() from None
            finally:
                if parser is not None:
                    parser.close_files()
                request.app.state.active_upload_forms -= 1

        return bounded
