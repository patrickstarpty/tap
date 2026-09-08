"""Read-only public projection of approved Agent and Skill revisions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from tap.contracts.http import (
    AiAgentRevisionPage,
    AiAgentRevisionSummary,
    SkillRevisionPage,
    SkillRevisionSummary,
)
from tap.interfaces.http.dependencies import asset_catalog_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization

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
    responses={422: problem_response_metadata("Request validation failed")},
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
    responses={404: problem_response_metadata("Agent revision unavailable")},
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
    responses={422: problem_response_metadata("Request validation failed")},
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
    responses={404: problem_response_metadata("Skill revision unavailable")},
)
async def get_skill(request: Request, revision_id: str) -> SkillRevisionSummary:
    return _skill(
        await asset_catalog_service(request).get_skill(request.state.project_scope, revision_id)
    )
