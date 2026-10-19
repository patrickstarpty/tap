"""Read-only public projection of the closed built-in tool registry.

Agent and Skill revision routes live in ``ai_agents.py``/``ai_skills.py``.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from tap.contracts.http import ToolDescriptor, ToolPage
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.ai.domain.tools import TOOL_REGISTRY

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get(
    "/tools",
    operation_id="ai_list_tools",
    response_model=ToolPage,
    dependencies=[Depends(project_authorization("ai.agents.read"))],
    responses={
        422: problem_response_metadata("Request validation failed"),
    },
)
async def list_tools(locale: Literal["en", "zh"] | None = Query(default=None)) -> ToolPage:
    resolved_locale = locale or "en"
    return ToolPage(
        items=[
            ToolDescriptor(
                tool_id=definition.tool_id,
                name=definition.names.get(resolved_locale, definition.names["en"]),
                description=definition.descriptions.get(
                    resolved_locale, definition.descriptions["en"]
                ),
                required=definition.required,
            )
            for definition in TOOL_REGISTRY.values()
        ]
    )
