"""Project-owned Source commands and bounded current-source views."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response

from tap.contracts.http import SourceAccepted, SourceDetail, SourcePage, SourceRetryRequest
from tap.interfaces.http.dependencies import UploadInput, knowledge_service, source_command_key
from tap.interfaces.http.multipart import BoundedUploadRoute
from tap.interfaces.http.problems import InvalidDocumentUpload, problem_response_metadata
from tap.interfaces.http.routes.knowledge_chunks import upload_settings
from tap.interfaces.http.routes.knowledge_documents import (
    MAX_DOCUMENT_BYTES,
    bounded_upload_bytes,
    sanitize_upload_metadata,
)
from tap.interfaces.http.scope import project_authorization

router = APIRouter(
    prefix="/knowledge/sources",
    tags=["knowledge"],
    route_class=BoundedUploadRoute,
    responses={
        code: problem_response_metadata("Source request failed")
        for code in (400, 404, 409, 413, 422, 429, 503)
    },
)


@router.post(
    "",
    operation_id="knowledge_upload_source",
    response_model=SourceAccepted,
    status_code=202,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def upload_source(
    request: Request,
    upload: UploadFile = File(...),
    settings: str | None = Form(default=None),
    key: str = Depends(source_command_key),
) -> SourceAccepted:
    form = await request.form()
    if (
        set(form) - {"upload", "settings"}
        or len(form.getlist("upload")) != 1
        or len(form.getlist("settings")) > 1
    ):
        raise InvalidDocumentUpload("invalid multipart request")
    if upload.size is not None and upload.size > MAX_DOCUMENT_BYTES:
        raise InvalidDocumentUpload("document-too-large")
    configuration = upload_settings(request, settings)
    filename, media_type = sanitize_upload_metadata(upload.filename, upload.content_type)
    result = await knowledge_service(request).upload_source(
        UploadInput(
            filename,
            media_type,
            bounded_upload_bytes(upload),
            asdict(configuration) if configuration is not None else None,
        ),
        key,
        request.state.correlation_id,
    )
    return result


@router.get(
    "",
    operation_id="knowledge_list_sources",
    response_model=SourcePage,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def list_sources(
    request: Request,
    cursor: str | None = Query(None, min_length=1, max_length=512),
    limit: int = Query(25, ge=1, le=50),
) -> SourcePage:
    return await knowledge_service(request).list_sources(cursor, limit)


@router.get(
    "/{source_id}",
    operation_id="knowledge_get_source",
    response_model=SourceDetail,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def get_source(
    request: Request,
    source_id: str,
    cursor: str | None = Query(None, min_length=1, max_length=512),
    limit: int = Query(25, ge=1, le=50),
) -> SourceDetail:
    return await knowledge_service(request).get_source(source_id, cursor, limit)


@router.post(
    "/{source_id}/retry",
    operation_id="knowledge_retry_source",
    response_model=SourceAccepted,
    status_code=202,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def retry_source(
    request: Request,
    source_id: str,
    body: SourceRetryRequest,
    key: str = Depends(source_command_key),
) -> SourceAccepted:
    return await knowledge_service(request).retry_source(
        source_id, body, key, request.state.correlation_id
    )


@router.delete(
    "/{source_id}",
    operation_id="knowledge_delete_source",
    status_code=204,
    dependencies=[Depends(project_authorization("knowledge.delete"))],
)
async def delete_source(
    request: Request, source_id: str, key: str = Depends(source_command_key)
) -> Response:
    await knowledge_service(request).delete_source(source_id, key, request.state.correlation_id)
    return Response(status_code=204)
