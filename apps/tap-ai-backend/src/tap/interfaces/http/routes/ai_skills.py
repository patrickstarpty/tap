"""Skill catalog reads and the Skill import/authoring write surface.

Package bytes are never executed here: multipart uploads are read as bounded
byte streams and handed to ``SkillAuthoringService`` (Task 4), which owns all
parsing, validation and object-store promotion. This module only shapes the
public contract and dispatches.
"""

from __future__ import annotations

from typing import Literal, NoReturn, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import JsonValue, ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from tap.contracts.http import (
    SkillDraftRequest,
    SkillFileView,
    SkillImportCommitRequest,
    SkillImportCommitResult,
    SkillImportPreview,
    SkillImportPreviewItem,
    SkillImportTextRequest,
    SkillReviseRequest,
    SkillRevisionDetail,
    SkillRevisionPage,
    SkillRevisionSummary,
)
from tap.interfaces.http.dependencies import asset_catalog_service, skill_authoring_service
from tap.interfaces.http.problems import problem_response_metadata, skill_problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.ai.application.skill_authoring import (
    FilesUpload,
    ImportPreview,
    ImportSelection,
    SkillDraft,
    TextUpload,
    ZipUpload,
)
from tap.modules.ai.application.skill_package import MAX_ARCHIVE_BYTES, SkillPackageRejected
from tap.modules.ai.domain.assets import AssetRevisionRejected, SkillFile, SkillRevision

_ApplicableTask = Literal["knowledge.answer", "test-plan.generate", "automation.generate"]

router = APIRouter(prefix="/ai", tags=["ai"])

_WRITE = Depends(project_authorization("ai.skills.write"))
_READ = Depends(project_authorization("ai.skills.read"))

# Same bound as a staged archive's own compressed-size limit (Task 3's
# `scan_zip`): a single multipart `archive` part, or the sum of every
# `files` part in one import request, must not exceed it. Reusing the
# shared `bounded_upload_bytes` (25 MiB, raising the Knowledge-only
# `InvalidDocumentUpload` -> RFC 9457 `ProblemDetails`) would both pick the
# wrong limit and the wrong error shape for this endpoint, whose errors are
# the bespoke `{"code", "detail"}` `SkillProblem` body.
_MAX_SKILL_UPLOAD_BYTES = MAX_ARCHIVE_BYTES
_SKILL_UPLOAD_READ_CHUNK_BYTES = 1_048_576


# -- serialization ------------------------------------------------------


def _file_view(file: SkillFile) -> SkillFileView:
    return SkillFileView(
        relative_path=file.relative_path,
        size_bytes=file.size_bytes,
        sha256=file.sha256,
        category=file.category,
        attached=file.attached,
    )


def _summary(value: SkillRevision, *, is_latest: bool) -> SkillRevisionSummary:
    return SkillRevisionSummary(
        revision_id=value.revision_id,
        asset_id=value.asset_id,
        skill_id=value.asset_id,
        display_name=value.display_name,
        description=value.description,
        content_digest=value.content_digest,
        applicable_tasks=cast(list[_ApplicableTask], sorted(value.applicable_tasks)),
        source_kind=value.source_kind,
        built_in=value.source_kind == "built_in",
        status=value.status.value,
        is_latest=is_latest,
    )


def _detail(value: SkillRevision, *, is_latest: bool) -> SkillRevisionDetail:
    summary = _summary(value, is_latest=is_latest)
    return SkillRevisionDetail(
        **summary.model_dump(),
        instructions=value.instruction_template,
        attached_references=value.attached_references,
        frontmatter=(
            cast(dict[str, JsonValue], dict(value.frontmatter))
            if value.frontmatter is not None
            else None
        ),
        files=[_file_view(file) for file in value.files],
    )


async def _latest_revision_ids(request: Request, asset_ids: set[str]) -> dict[str, str]:
    catalog = asset_catalog_service(request)
    latest: dict[str, str] = {}
    for asset_id in asset_ids:
        revisions = await catalog.list_revisions("skill", asset_id)
        if revisions:
            latest[asset_id] = revisions[-1].revision_id
    return latest


def _import_preview(value: ImportPreview) -> SkillImportPreview:
    return SkillImportPreview(
        import_id=value.import_id,
        expires_at=value.expires_at,
        skills=[
            SkillImportPreviewItem(
                root=skill.root,
                name=skill.name,
                description=skill.description,
                frontmatter=cast(dict[str, JsonValue], dict(skill.frontmatter)),
                files=[_file_view(file) for file in skill.files],
                warnings=list(skill.warnings),
                conflict_skill_id=skill.conflict_skill_id,
            )
            for skill in value.skills
        ],
    )


# -- multipart/JSON dispatch for skill imports ---------------------------


def _reject_upload_shape(message: str) -> NoReturn:
    raise RequestValidationError(
        [{"type": "value_error", "loc": ("body",), "msg": message, "input": None}]
    )


async def _bounded_skill_upload_bytes(upload: UploadFile, remaining: list[int]) -> bytes:
    """Read one multipart part in chunks against a shared, mutable byte budget.

    ``remaining`` is a one-element box shared across every part of a single
    import request (the lone ``archive`` part, or every ``files`` part), so
    many small files cannot together exceed the aggregate archive-size bound
    any more than one oversized archive could. Rejects before buffering the
    chunk that would exceed the budget, never after.
    """
    chunks: list[bytes] = []
    while chunk := await upload.read(_SKILL_UPLOAD_READ_CHUNK_BYTES):
        remaining[0] -= len(chunk)
        if remaining[0] < 0:
            raise SkillPackageRejected(
                "skill_package_too_large",
                "the uploaded skill package exceeds the archive size limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _parse_skill_upload(request: Request) -> ZipUpload | FilesUpload | TextUpload:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        try:
            body = await request.json()
            payload = SkillImportTextRequest.model_validate(body)
        except (ValueError, ValidationError):
            _reject_upload_shape("the request body is not valid JSON")
        return TextUpload(text=payload.text)
    if media_type != "multipart/form-data":
        _reject_upload_shape("expected a multipart upload or a JSON text body")
    form = await request.form()
    archive_items = [
        item for item in form.getlist("archive") if isinstance(item, StarletteUploadFile)
    ]
    file_items = [item for item in form.getlist("files") if isinstance(item, StarletteUploadFile)]
    if bool(archive_items) == bool(file_items):
        _reject_upload_shape("provide exactly one of an archive field or one or more files")
    budget = [_MAX_SKILL_UPLOAD_BYTES]
    if archive_items:
        if len(archive_items) != 1:
            _reject_upload_shape("only one archive upload is accepted")
        archive = cast(UploadFile, archive_items[0])
        data = await _bounded_skill_upload_bytes(archive, budget)
        return ZipUpload(data=data)
    files: dict[str, bytes] = {}
    for item in file_items:
        if not item.filename:
            _reject_upload_shape("each uploaded file requires a relative-path filename")
        if item.filename in files:
            # A silent overwrite would drop one of the two uploaded files
            # without any signal to the caller.
            _reject_upload_shape(f"duplicate uploaded file path {item.filename!r}")
        upload = cast(UploadFile, item)
        files[item.filename] = await _bounded_skill_upload_bytes(upload, budget)
    return FilesUpload(files=files)


# -- read routes ----------------------------------------------------------


@router.get(
    "/skills",
    operation_id="ai_list_skill_revisions",
    response_model=SkillRevisionPage,
    dependencies=[_READ],
    responses={
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_skills(request: Request) -> SkillRevisionPage:
    items = await asset_catalog_service(request).list_skills(request.state.project_scope)
    latest = await _latest_revision_ids(request, {item.asset_id for item in items})
    return SkillRevisionPage(
        items=[
            _summary(item, is_latest=latest.get(item.asset_id) == item.revision_id)
            for item in items
        ]
    )


@router.get(
    "/skills/{revision_id}",
    operation_id="ai_get_skill_revision",
    response_model=SkillRevisionDetail,
    dependencies=[_READ],
    responses={
        404: problem_response_metadata("Skill revision unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def get_skill(request: Request, revision_id: str) -> SkillRevisionDetail:
    # Resolve any status (C-2, spec §4.3): a link opened from a past Turn
    # must still open even if the Skill was disabled since, and the Skill
    # detail drawer needs to show a disabled revision too.
    value = await asset_catalog_service(request).resolve_historical_skill(
        request.state.project_scope, revision_id
    )
    latest = await _latest_revision_ids(request, {value.asset_id})
    return _detail(value, is_latest=latest.get(value.asset_id) == value.revision_id)


@router.get(
    "/skills/{skill_id}/revisions",
    operation_id="ai_list_skill_revision_history",
    response_model=SkillRevisionPage,
    dependencies=[_READ],
    responses={
        404: problem_response_metadata("Skill unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_skill_revision_history(request: Request, skill_id: str) -> SkillRevisionPage:
    revisions = cast(
        tuple[SkillRevision, ...],
        await asset_catalog_service(request).list_revisions("skill", skill_id),
    )
    if not revisions:
        raise AssetRevisionRejected()
    latest_id = revisions[-1].revision_id
    return SkillRevisionPage(
        items=[
            _summary(revision, is_latest=revision.revision_id == latest_id)
            for revision in revisions
        ]
    )


@router.get(
    "/skills/{revision_id}/files/{path:path}",
    operation_id="ai_download_skill_file",
    dependencies=[_READ],
    responses={
        400: skill_problem_response_metadata("Skill file path rejected"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def download_skill_file(request: Request, revision_id: str, path: str) -> Response:
    opened = await skill_authoring_service(request).open_file(
        request.state.project_scope, revision_id, path
    )
    filename = path.rsplit("/", 1)[-1] or "file"
    return Response(
        opened.data,
        media_type=_download_content_type(path, opened.data),
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )


def _download_content_type(path: str, data: bytes) -> str:
    if path.lower().endswith((".md", ".txt")):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


# -- write routes -----------------------------------------------------------


@router.post(
    "/skills/imports",
    operation_id="ai_import_skill",
    response_model=SkillImportPreview,
    status_code=201,
    dependencies=[_WRITE],
    responses={
        400: skill_problem_response_metadata("Skill package rejected"),
        413: skill_problem_response_metadata("Skill package too large"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def import_skill(request: Request) -> SkillImportPreview:
    upload = await _parse_skill_upload(request)
    preview = await skill_authoring_service(request).stage_import(
        request.state.project_scope, upload=upload
    )
    return _import_preview(preview)


@router.post(
    "/skills/imports/{import_id}/commit",
    operation_id="ai_commit_skill_import",
    response_model=SkillImportCommitResult,
    dependencies=[_WRITE],
    responses={
        400: skill_problem_response_metadata("Skill not found in package"),
        409: skill_problem_response_metadata("Skill name or revision conflict"),
        410: skill_problem_response_metadata("Staged import expired"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def commit_skill_import(
    request: Request, import_id: str, body: SkillImportCommitRequest
) -> SkillImportCommitResult:
    selections = tuple(
        ImportSelection(root=item.root, as_new_revision_of=item.as_new_revision_of)
        for item in body.selections
    )
    revisions = await skill_authoring_service(request).commit_import(
        request.state.project_scope, import_id, selections
    )
    return SkillImportCommitResult(revisions=[_detail(r, is_latest=True) for r in revisions])


def _draft(body: SkillDraftRequest) -> SkillDraft:
    if not body.applicable_tasks:
        return SkillDraft(
            name=body.name, description=body.description, instructions=body.instructions
        )
    return SkillDraft(
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        applicable_tasks=frozenset(body.applicable_tasks),
    )


@router.post(
    "/skills",
    operation_id="ai_create_skill",
    response_model=SkillRevisionDetail,
    status_code=201,
    dependencies=[_WRITE],
    responses={
        400: skill_problem_response_metadata("Skill frontmatter invalid"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def create_skill(request: Request, body: SkillDraftRequest) -> SkillRevisionDetail:
    revision = await skill_authoring_service(request).create_authored(
        request.state.project_scope, _draft(body)
    )
    return _detail(revision, is_latest=True)


@router.post(
    "/skills/{skill_id}/revisions",
    operation_id="ai_revise_skill",
    response_model=SkillRevisionDetail,
    status_code=201,
    dependencies=[_WRITE],
    responses={
        400: skill_problem_response_metadata("Skill frontmatter invalid"),
        409: skill_problem_response_metadata("Revision conflict"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def revise_skill(
    request: Request, skill_id: str, body: SkillReviseRequest
) -> SkillRevisionDetail:
    revision = await skill_authoring_service(request).revise(
        request.state.project_scope, skill_id, body.base_revision_id, _draft(body)
    )
    return _detail(revision, is_latest=True)


async def _toggle_enabled(
    request: Request, skill_id: str, *, enabled: bool
) -> SkillRevisionSummary:
    await skill_authoring_service(request).set_enabled(
        request.state.project_scope, skill_id, enabled
    )
    revisions = cast(
        tuple[SkillRevision, ...],
        await asset_catalog_service(request).list_revisions("skill", skill_id),
    )
    if not revisions:
        raise AssetRevisionRejected()
    return _summary(revisions[-1], is_latest=True)


@router.post(
    "/skills/{skill_id}/disable",
    operation_id="ai_disable_skill",
    response_model=SkillRevisionSummary,
    dependencies=[_WRITE],
    responses={
        404: problem_response_metadata("Skill unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def disable_skill(request: Request, skill_id: str) -> SkillRevisionSummary:
    return await _toggle_enabled(request, skill_id, enabled=False)


@router.post(
    "/skills/{skill_id}/enable",
    operation_id="ai_enable_skill",
    response_model=SkillRevisionSummary,
    dependencies=[_WRITE],
    responses={
        404: problem_response_metadata("Skill unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def enable_skill(request: Request, skill_id: str) -> SkillRevisionSummary:
    return await _toggle_enabled(request, skill_id, enabled=True)
