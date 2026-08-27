"""Typed, bounded public document routes."""

from __future__ import annotations

import unicodedata
from collections.abc import AsyncIterator

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import Response

from tap.contracts.http import DocumentAccepted, DocumentDetail, DocumentPage
from tap.interfaces.http.dependencies import UploadInput, knowledge_service
from tap.interfaces.http.problems import InvalidDocumentUpload, problem_response_metadata

router = APIRouter(prefix="/v1/knowledge/documents", tags=["knowledge"])
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
READ_CHUNK_BYTES = 1_048_576
_MEDIA_TYPES_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


def sanitize_upload_metadata(filename: str | None, media_type: str | None) -> tuple[str, str]:
    """Return display-safe metadata after enforcing the closed extension/media pairs."""
    if not isinstance(filename, str):
        raise InvalidDocumentUpload("filename is required")
    display_name = unicodedata.normalize("NFC", filename)
    if (
        not display_name
        or len(display_name) > 255
        or "/" in display_name
        or "\\" in display_name
        or any(unicodedata.category(character) == "Cc" for character in display_name)
    ):
        raise InvalidDocumentUpload("filename is not a safe display name")
    extension = display_name[display_name.rfind(".") :].lower()
    expected_media_type = _MEDIA_TYPES_BY_EXTENSION.get(extension)
    if expected_media_type is None or media_type != expected_media_type:
        raise InvalidDocumentUpload("filename extension and media type are not supported")
    return display_name, expected_media_type


async def bounded_upload_bytes(upload: UploadFile) -> AsyncIterator[bytes]:
    """Yield multipart bytes in fixed chunks while enforcing the 25 MiB hard limit."""
    total = 0
    while chunk := await upload.read(READ_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_DOCUMENT_BYTES:
            raise InvalidDocumentUpload("document-too-large")
        yield chunk


@router.post(
    "",
    operation_id="knowledge_upload_document",
    response_model=DocumentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_400_BAD_REQUEST: problem_response_metadata("Unsupported document"),
        status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata("Invalid document upload"),
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: problem_response_metadata("Document too large"),
        status.HTTP_429_TOO_MANY_REQUESTS: problem_response_metadata("Document limit reached"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata(
            "Knowledge runtime unavailable"
        ),
    },
)
async def upload_document(
    request: Request,
    upload: UploadFile = File(...),
) -> DocumentAccepted:
    form = await request.form()
    if set(form) != {"upload"} or len(form.getlist("upload")) != 1:
        raise InvalidDocumentUpload("invalid multipart request")
    if upload.size is not None and upload.size > MAX_DOCUMENT_BYTES:
        raise InvalidDocumentUpload("document-too-large")
    filename, media_type = sanitize_upload_metadata(upload.filename, upload.content_type)
    return await knowledge_service(request).upload(
        UploadInput(filename=filename, media_type=media_type, content=bounded_upload_bytes(upload))
    )


@router.get(
    "",
    operation_id="knowledge_list_documents",
    response_model=DocumentPage,
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata("Invalid list request"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata(
            "Knowledge runtime unavailable"
        ),
    },
)
async def list_documents(
    request: Request,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    limit: int = Query(default=25, ge=1, le=50),
) -> DocumentPage:
    return await knowledge_service(request).list_documents(cursor, limit)


@router.get(
    "/{document_id}",
    operation_id="knowledge_get_document",
    response_model=DocumentDetail,
    responses={
        status.HTTP_404_NOT_FOUND: problem_response_metadata("Document not found"),
        status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata("Invalid document ID"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata(
            "Knowledge runtime unavailable"
        ),
    },
)
async def get_document(request: Request, document_id: str) -> DocumentDetail:
    return await knowledge_service(request).get_document(document_id)


@router.post(
    "/{document_id}/retry",
    operation_id="knowledge_retry_document",
    response_model=DocumentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_404_NOT_FOUND: problem_response_metadata("Document not found"),
        status.HTTP_409_CONFLICT: problem_response_metadata("Document is not retryable"),
        status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata("Invalid document ID"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata(
            "Knowledge runtime unavailable"
        ),
    },
)
async def retry_document(request: Request, document_id: str) -> DocumentAccepted:
    return await knowledge_service(request).retry_document(document_id)


@router.delete(
    "/{document_id}",
    operation_id="knowledge_delete_document",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: problem_response_metadata("Document not found"),
        status.HTTP_409_CONFLICT: problem_response_metadata("Document state changed"),
        status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata("Invalid document ID"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata(
            "Knowledge runtime unavailable"
        ),
    },
)
async def delete_document(request: Request, document_id: str) -> Response:
    await knowledge_service(request).delete_document(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
