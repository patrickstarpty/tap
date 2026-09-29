"""Authorized chunk management; saved edits are persisted before index work starts."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response

from tap.contracts.http import (
    KnowledgeChunk,
    KnowledgeChunkBatch,
    KnowledgeChunkBatchResult,
    KnowledgeChunkChange,
    KnowledgeChunkCreate,
    KnowledgeChunkImport,
    KnowledgeChunkPage,
    KnowledgeChunkPreview,
    KnowledgeChunkSettingsRequest,
    KnowledgeChunkSettingsSave,
    KnowledgeChunkSettingsView,
)
from tap.interfaces.http.multipart import BoundedUploadRoute
from tap.interfaces.http.scope import project_authorization
from tap.modules.knowledge.domain.managed_chunks import ChunkSettings

router = APIRouter(prefix="/knowledge/documents/{document_id}", tags=["knowledge"])


def manager(request: Request) -> Any:
    service = getattr(request.app.state.http_services, "chunk_manager", None)
    if service is None:
        raise HTTPException(503, "Chunk management unavailable")
    return service


async def invoke(operation: Any) -> Any:
    try:
        return await operation
    except ValueError as error:
        name = type(error).__name__
        raise HTTPException(
            404 if name == "ChunkNotFound" else 409 if name == "ChunkConflict" else 422, str(error)
        ) from error


def settings_value(value: Any) -> ChunkSettings:
    try:
        return ChunkSettings(**value.model_dump(by_alias=True))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(
    "/chunk-settings",
    response_model=KnowledgeChunkSettingsView,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def get_settings(request: Request, document_id: str) -> Any:
    return await invoke(manager(request).settings(document_id))


@router.put(
    "/chunk-settings",
    response_model=KnowledgeChunkSettingsView,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def save_settings(
    request: Request, document_id: str, body: KnowledgeChunkSettingsSave
) -> Any:
    return await invoke(
        manager(request).configure(
            document_id, settings_value(body.settings), body.version, body.confirm_replace
        )
    )


@router.post(
    "/chunks/preview",
    response_model=KnowledgeChunkPreview,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def preview(request: Request, document_id: str, body: KnowledgeChunkSettingsRequest) -> Any:
    return await invoke(manager(request).preview(document_id, settings_value(body.settings)))


@router.get(
    "/chunks",
    response_model=KnowledgeChunkPage,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def list_chunks(
    request: Request,
    document_id: str,
    q: str = Query(default="", max_length=1000),
    status: Literal["all", "enabled", "disabled"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> Any:
    return await invoke(
        manager(request).list_chunks(
            document_id, q=q, status=status, page=page, page_size=page_size
        )
    )


@router.post(
    "/chunks",
    response_model=KnowledgeChunk,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def create_chunk(
    request: Request,
    document_id: str,
    body: KnowledgeChunkCreate,
    key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=128),
) -> Any:
    return await invoke(manager(request).create(document_id, body.content, key=key))


@router.post(
    "/chunks/batch",
    response_model=KnowledgeChunkBatchResult,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def batch(request: Request, document_id: str, body: KnowledgeChunkBatch) -> Any:
    if body.action == "delete":
        await project_authorization("knowledge.delete")(request)
    return await invoke(
        manager(request).batch(
            document_id, body.action, [item.model_dump(by_alias=True) for item in body.items]
        )
    )


@router.post(
    "/chunks/import",
    response_model=KnowledgeChunkBatchResult,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def import_chunks(
    request: Request,
    document_id: str,
    body: KnowledgeChunkImport,
    key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=128),
) -> Any:
    return await invoke(manager(request).import_contents(document_id, body.contents, key=key))


@router.patch(
    "/chunks/{chunk_id}",
    response_model=KnowledgeChunk,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def change_chunk(
    request: Request, document_id: str, chunk_id: str, body: KnowledgeChunkChange
) -> Any:
    return await invoke(
        manager(request).change(
            document_id,
            chunk_id,
            body.version,
            content=body.content,
            enabled=body.enabled,
            regenerate_children=body.regenerate_children,
        )
    )


@router.delete(
    "/chunks/{chunk_id}",
    status_code=204,
    dependencies=[Depends(project_authorization("knowledge.delete"))],
)
async def delete_chunk(
    request: Request, document_id: str, chunk_id: str, version: int = Query(ge=1)
) -> Response:
    await invoke(manager(request).change(document_id, chunk_id, version, delete=True))
    return Response(status_code=204)


@router.post(
    "/chunks/{chunk_id}/children",
    response_model=KnowledgeChunk,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def create_child(
    request: Request,
    document_id: str,
    chunk_id: str,
    body: KnowledgeChunkCreate,
    key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=128),
) -> Any:
    return await invoke(manager(request).create(document_id, body.content, chunk_id, key=key))


upload_router = APIRouter(
    prefix="/knowledge/chunks", tags=["knowledge"], route_class=BoundedUploadRoute
)


@upload_router.post(
    "/preview",
    response_model=KnowledgeChunkPreview,
    dependencies=[Depends(project_authorization("knowledge.write"))],
)
async def preview_upload(
    request: Request, upload: UploadFile = File(...), settings: str = Form(...)
) -> Any:
    from tap.interfaces.http.routes.knowledge_documents import (
        bounded_upload_bytes,
        sanitize_upload_metadata,
    )

    filename, media_type = sanitize_upload_metadata(upload.filename, upload.content_type)
    configuration = upload_settings(request, settings)
    assert configuration is not None
    content = b"".join([chunk async for chunk in bounded_upload_bytes(upload)])
    return await invoke(
        manager(request).preview_upload(filename, media_type, content, configuration)
    )


def upload_settings(request: Request, value: str | None) -> ChunkSettings | None:
    from tap.contracts.http import KnowledgeChunkSettings

    if value is None:
        return None
    manager(request)
    try:
        return settings_value(KnowledgeChunkSettings.model_validate_json(value))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(
    "/original",
    dependencies=[
        Depends(project_authorization("knowledge.original.read", resource_id_param="document_id"))
    ],
)
async def original(request: Request, document_id: str) -> Response:
    from urllib.parse import quote

    filename, media_type, content = await invoke(manager(request).original(document_id))
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )
