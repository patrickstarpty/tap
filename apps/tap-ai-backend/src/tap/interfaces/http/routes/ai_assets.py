"""Public projection of approved Agent and Skill revisions and inert Skill imports."""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exceptions import RequestValidationError
from python_multipart.multipart import parse_options_header
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from tap.contracts.http import (
    AiAgentRevisionPage,
    AiAgentRevisionSummary,
    SkillCreateRequest,
    SkillImportResult,
    SkillRevisionPage,
    SkillRevisionSummary,
    SkillRevisionView,
    SkillStatusRequest,
)
from tap.interfaces.http.dependencies import asset_catalog_service, skill_import_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.ai.application.skill_import import SkillImportRejected
from tap.modules.ai.domain.assets import (
    MAX_SKILL_SOURCE_BYTES,
    AssetRevisionStatus,
    SkillRevision,
)

_MAX_IMPORT_BODY = MAX_SKILL_SOURCE_BYTES + 16 * 1024
_SKILL_WRITE_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"model": SkillImportResult, "description": "The identical latest revision"},
    413: problem_response_metadata("Skill too large"),
    422: problem_response_metadata("Skill rejected"),
    503: problem_response_metadata("Knowledge runtime unavailable"),
}

router = APIRouter(prefix="/ai", tags=["ai"])


def _agent(value) -> AiAgentRevisionSummary:
    return AiAgentRevisionSummary(
        revision_id=value.revision_id,
        asset_id=value.asset_id,
        display_name=value.display_name,
        content_digest=value.content_digest,
        tool_allowlist=sorted(value.tool_allowlist),
        output_schema_digest=value.output_schema_digest,
    )


def _skill(value) -> SkillRevisionSummary:
    return SkillRevisionSummary(
        revision_id=value.revision_id,
        asset_id=value.asset_id,
        display_name=value.display_name,
        content_digest=value.content_digest,
        applicable_tasks=sorted(value.applicable_tasks),
    )


@router.get(
    "/agents",
    operation_id="ai_list_agent_revisions",
    response_model=AiAgentRevisionPage,
    dependencies=[Depends(project_authorization("ai.agents.read"))],
    responses={
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_agents(request: Request) -> AiAgentRevisionPage:
    return AiAgentRevisionPage(
        items=[
            _agent(item)
            for item in await asset_catalog_service(request).list_agents(
                request.state.project_scope
            )
        ]
    )


@router.get(
    "/agents/{revision_id}",
    operation_id="ai_get_agent_revision",
    response_model=AiAgentRevisionSummary,
    dependencies=[Depends(project_authorization("ai.agents.read"))],
    responses={
        404: problem_response_metadata("Agent revision unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def get_agent(request: Request, revision_id: str) -> AiAgentRevisionSummary:
    return _agent(
        await asset_catalog_service(request).get_agent(request.state.project_scope, revision_id)
    )


@router.get(
    "/skills",
    operation_id="ai_list_skill_revisions",
    response_model=SkillRevisionPage,
    dependencies=[Depends(project_authorization("ai.skills.read"))],
    responses={
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_skills(request: Request) -> SkillRevisionPage:
    return SkillRevisionPage(
        items=[
            _skill(item)
            for item in await asset_catalog_service(request).list_skills(
                request.state.project_scope
            )
        ]
    )


@router.get(
    "/skills/{revision_id}",
    operation_id="ai_get_skill_revision",
    response_model=SkillRevisionSummary,
    dependencies=[Depends(project_authorization("ai.skills.read"))],
    responses={
        404: problem_response_metadata("Skill revision unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def get_skill(request: Request, revision_id: str) -> SkillRevisionSummary:
    return _skill(
        await asset_catalog_service(request).get_skill(request.state.project_scope, revision_id)
    )


def _skill_view(value: SkillRevision) -> SkillRevisionView:
    return SkillRevisionView(
        revision_id=value.revision_id,
        asset_id=value.asset_id,
        display_name=value.display_name,
        content_digest=value.content_digest,
        applicable_tasks=cast(
            list[Literal["knowledge.answer", "test-plan.generate", "automation.generate"]],
            sorted(value.applicable_tasks),
        ),
        status=value.status.value,
        origin=cast(Literal["seed", "import", "editor"], value.origin),
        description=value.description,
        requested_tools=list(value.requested_tools),
        adopted_from_revision_id=value.adopted_from_revision_id,
    )


def _invalid_upload() -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", "file"), "msg": "invalid upload"}]
    )


async def _skill_upload(request: Request) -> bytes:
    """Read one bounded SKILL.md part; the bytes are only parsed as text, never run."""
    lengths = request.headers.getlist("content-length")
    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
        raise _invalid_upload()
    if int(lengths[0]) > _MAX_IMPORT_BODY:
        raise SkillImportRejected("skill-too-large")
    media, _ = parse_options_header(request.headers.get("content-type", ""))
    if media != b"multipart/form-data":
        raise _invalid_upload()
    try:
        form = await request.form(max_files=1, max_fields=1)
    except MultiPartException:
        raise _invalid_upload() from None
    try:
        values = form.getlist("file")
        if set(form) != {"file"} or len(values) != 1 or not isinstance(values[0], UploadFile):
            raise _invalid_upload()
        raw = await values[0].read(MAX_SKILL_SOURCE_BYTES + 1)
    finally:
        await form.close()
    if len(raw) > MAX_SKILL_SOURCE_BYTES:
        raise SkillImportRejected("skill-too-large")
    return raw


@router.post(
    "/skills/imports",
    operation_id="ai_import_skill",
    response_model=SkillImportResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(project_authorization("ai.skills.write"))],
    responses=_SKILL_WRITE_RESPONSES,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def import_skill(request: Request, response: Response) -> SkillImportResult:
    raw = await _skill_upload(request)
    revision, created, warnings = await skill_import_service(request).import_with_warnings(
        request.state.project_scope, raw
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return SkillImportResult(revision=_skill_view(revision), warnings=list(warnings))


@router.post(
    "/skills",
    operation_id="ai_create_skill",
    response_model=SkillImportResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(project_authorization("ai.skills.write"))],
    responses=_SKILL_WRITE_RESPONSES,
)
async def create_skill(
    request: Request, response: Response, body: SkillCreateRequest
) -> SkillImportResult:
    revision, created, warnings = await skill_import_service(request).create_with_warnings(
        request.state.project_scope,
        name=body.name,
        description=body.description,
        instructions=body.instructions,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return SkillImportResult(revision=_skill_view(revision), warnings=list(warnings))


@router.get(
    "/skills/{revision_id}/content",
    operation_id="ai_get_skill_content",
    response_class=Response,
    dependencies=[Depends(project_authorization("ai.skills.read"))],
    responses={
        200: {
            "description": "The original SKILL.md bytes",
            "content": {"text/markdown": {"schema": {"type": "string"}}},
        },
        404: problem_response_metadata("Skill revision unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def get_skill_content(request: Request, revision_id: str) -> Response:
    raw = await skill_import_service(request).source(request.state.project_scope, revision_id)
    return Response(
        content=raw,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="SKILL.md"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/skills/revisions/{revision_id}/status",
    operation_id="ai_set_skill_revision_status",
    response_model=SkillRevisionView,
    dependencies=[Depends(project_authorization("ai.skills.write"))],
    responses={
        404: problem_response_metadata("Skill revision unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def set_skill_status(
    request: Request, revision_id: str, body: SkillStatusRequest
) -> SkillRevisionView:
    return _skill_view(
        await skill_import_service(request).set_status(
            request.state.project_scope, revision_id, AssetRevisionStatus(body.status)
        )
    )
