"""Agent catalog reads and the Agent authoring write surface.

Agents are composed from already-approved Skills, a closed tool allowlist
and an optional default model (Task 1/2); this module only shapes the public
contract and dispatches to ``AgentAuthoringService`` (Task 6), which owns all
validation and immutable-revision writes.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request

from tap.contracts.http import (
    AgentDraftRequest,
    AgentReviseRequest,
    AiAgentRevisionDetail,
    AiAgentRevisionPage,
    AiAgentRevisionSummary,
)
from tap.interfaces.http.dependencies import agent_authoring_service, asset_catalog_service
from tap.interfaces.http.problems import problem_response_metadata, skill_problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.ai.application.agent_authoring import AgentDraft, compute_outdated_skill_ids
from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionRejected

router = APIRouter(prefix="/ai", tags=["ai"])

_WRITE = Depends(project_authorization("ai.agents.write"))
_READ = Depends(project_authorization("ai.agents.read"))


# -- serialization --------------------------------------------------------


async def _summary(
    request: Request, value: AiAgentRevision, *, is_latest: bool
) -> AiAgentRevisionSummary:
    outdated = await compute_outdated_skill_ids(
        asset_catalog_service(request), value.scope, value.skill_revision_ids
    )
    return AiAgentRevisionSummary(
        revision_id=value.revision_id,
        asset_id=value.asset_id,
        agent_id=value.asset_id,
        display_name=value.display_name,
        description=value.description,
        content_digest=value.content_digest,
        tool_allowlist=sorted(value.tool_allowlist),
        output_schema_digest=value.output_schema_digest,
        source_kind=value.source_kind,
        built_in=value.source_kind == "built_in",
        status=value.status.value,
        is_latest=is_latest,
        skill_revision_ids=list(value.skill_revision_ids),
        default_model_alias=value.default_model_alias,
        has_skill_updates=bool(outdated),
    )


async def _detail(
    request: Request, value: AiAgentRevision, *, is_latest: bool
) -> AiAgentRevisionDetail:
    summary = await _summary(request, value, is_latest=is_latest)
    return AiAgentRevisionDetail(
        **summary.model_dump(), system_instruction=value.system_instruction
    )


async def _latest_revision_ids(request: Request, asset_ids: set[str]) -> dict[str, str]:
    catalog = asset_catalog_service(request)
    latest: dict[str, str] = {}
    for asset_id in asset_ids:
        revisions = await catalog.list_revisions("agent", asset_id)
        if revisions:
            latest[asset_id] = revisions[-1].revision_id
    return latest


def _draft(body: AgentDraftRequest) -> AgentDraft:
    return AgentDraft(
        name=body.name,
        description=body.description,
        system_instruction=body.system_instruction,
        skill_revision_ids=tuple(body.skill_revision_ids),
        tool_ids=frozenset(body.tool_ids),
        default_model_alias=body.default_model_alias,
    )


# -- read routes ------------------------------------------------------------


@router.get(
    "/agents",
    operation_id="ai_list_agent_revisions",
    response_model=AiAgentRevisionPage,
    dependencies=[_READ],
    responses={
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_agents(request: Request) -> AiAgentRevisionPage:
    items = await asset_catalog_service(request).list_agents(request.state.project_scope)
    latest = await _latest_revision_ids(request, {item.asset_id for item in items})
    return AiAgentRevisionPage(
        items=[
            await _summary(request, item, is_latest=latest.get(item.asset_id) == item.revision_id)
            for item in items
        ]
    )


@router.get(
    "/agents/{revision_id}",
    operation_id="ai_get_agent_revision",
    response_model=AiAgentRevisionDetail,
    dependencies=[_READ],
    responses={
        404: problem_response_metadata("Agent revision unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def get_agent(request: Request, revision_id: str) -> AiAgentRevisionDetail:
    # Resolve any status (C-2, spec §4.3): a link opened from a past Turn
    # must still open even if the Agent was disabled since.
    value = await asset_catalog_service(request).resolve_historical_agent(
        request.state.project_scope, revision_id
    )
    latest = await _latest_revision_ids(request, {value.asset_id})
    return await _detail(request, value, is_latest=latest.get(value.asset_id) == value.revision_id)


@router.get(
    "/agents/{agent_id}/revisions",
    operation_id="ai_list_agent_revision_history",
    response_model=AiAgentRevisionPage,
    dependencies=[_READ],
    responses={
        404: problem_response_metadata("Agent unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_agent_revision_history(request: Request, agent_id: str) -> AiAgentRevisionPage:
    revisions = cast(
        tuple[AiAgentRevision, ...],
        await asset_catalog_service(request).list_revisions("agent", agent_id),
    )
    if not revisions:
        raise AssetRevisionRejected()
    latest_id = revisions[-1].revision_id
    return AiAgentRevisionPage(
        items=[
            await _summary(request, revision, is_latest=revision.revision_id == latest_id)
            for revision in revisions
        ]
    )


# -- write routes -----------------------------------------------------------


@router.post(
    "/agents",
    operation_id="ai_create_agent",
    response_model=AiAgentRevisionDetail,
    status_code=201,
    dependencies=[_WRITE],
    responses={
        400: skill_problem_response_metadata("Agent draft rejected"),
        409: skill_problem_response_metadata("Agent name conflict"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def create_agent(request: Request, body: AgentDraftRequest) -> AiAgentRevisionDetail:
    revision = await agent_authoring_service(request).create(
        request.state.project_scope, _draft(body)
    )
    return await _detail(request, revision, is_latest=True)


@router.post(
    "/agents/{agent_id}/revisions",
    operation_id="ai_revise_agent",
    response_model=AiAgentRevisionDetail,
    status_code=201,
    dependencies=[_WRITE],
    responses={
        400: skill_problem_response_metadata("Agent draft rejected"),
        403: skill_problem_response_metadata("Built-in agent is immutable"),
        404: problem_response_metadata("Agent unavailable"),
        409: skill_problem_response_metadata("Revision conflict"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def revise_agent(
    request: Request, agent_id: str, body: AgentReviseRequest
) -> AiAgentRevisionDetail:
    revision = await agent_authoring_service(request).revise(
        request.state.project_scope, agent_id, body.base_revision_id, _draft(body)
    )
    return await _detail(request, revision, is_latest=True)


async def _toggle_enabled(
    request: Request, agent_id: str, *, enabled: bool
) -> AiAgentRevisionSummary:
    await agent_authoring_service(request).set_enabled(
        request.state.project_scope, agent_id, enabled
    )
    revisions = cast(
        tuple[AiAgentRevision, ...],
        await asset_catalog_service(request).list_revisions("agent", agent_id),
    )
    if not revisions:
        raise AssetRevisionRejected()
    return await _summary(request, revisions[-1], is_latest=True)


@router.post(
    "/agents/{agent_id}/disable",
    operation_id="ai_disable_agent",
    response_model=AiAgentRevisionSummary,
    dependencies=[_WRITE],
    responses={
        404: problem_response_metadata("Agent unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def disable_agent(request: Request, agent_id: str) -> AiAgentRevisionSummary:
    return await _toggle_enabled(request, agent_id, enabled=False)


@router.post(
    "/agents/{agent_id}/enable",
    operation_id="ai_enable_agent",
    response_model=AiAgentRevisionSummary,
    dependencies=[_WRITE],
    responses={
        404: problem_response_metadata("Agent unavailable"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def enable_agent(request: Request, agent_id: str) -> AiAgentRevisionSummary:
    return await _toggle_enabled(request, agent_id, enabled=True)
