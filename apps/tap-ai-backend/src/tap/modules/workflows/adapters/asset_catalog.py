"""Freeze approved AI-owned Agent revisions for one workflow request."""

from __future__ import annotations

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.assets import AiAgentRevision, SkillRevision
from tap.modules.workflows.ports.catalog import AgentSnapshot, FrozenAgentCatalog

ROOT_AGENT_ID = "orchestrator"


class AgentAssetReader(Protocol):
    async def list_agents(self, scope: ProjectScopeContext) -> tuple[AiAgentRevision, ...]: ...
    async def list_skills(self, scope: ProjectScopeContext) -> tuple[SkillRevision, ...]: ...


class AssetWorkflowCatalog:
    def __init__(self, assets: AgentAssetReader) -> None:
        self._assets = assets

    async def freeze_agents(
        self, scope: ProjectScopeContext, requested_ids: tuple[str, ...]
    ) -> FrozenAgentCatalog:
        agents = await self._assets.list_agents(scope)
        roots = [
            item for item in agents if item.asset_id == ROOT_AGENT_ID and item.status == "enabled"
        ]
        if len(roots) != 1:
            raise ValueError("orchestrator configuration unavailable")
        candidates = tuple(
            sorted(
                (
                    item
                    for item in agents
                    if item.asset_id != ROOT_AGENT_ID and item.status == "enabled"
                ),
                key=lambda item: item.asset_id,
            )
        )
        allowed = {item.asset_id for item in candidates}
        if len(set(requested_ids)) != len(requested_ids) or any(
            item not in allowed for item in requested_ids
        ):
            raise ValueError("delegate unavailable")
        skills = {
            item.asset_id: item.revision_id
            for item in await self._assets.list_skills(scope)
            if item.status == "enabled"
        }

        def snapshot(item: AiAgentRevision) -> AgentSnapshot:
            if item.system_instruction is None:
                raise ValueError("agent instruction unavailable")
            return AgentSnapshot(
                agent_id=item.asset_id,
                revision_id=item.revision_id,
                name=item.display_name,
                description=item.description or "",
                instruction=item.system_instruction,
                tool_ids=tuple(sorted(item.tool_allowlist)),
                model_alias=item.default_model_alias,
                skill_revision_ids=tuple(
                    skills[skill_id] for skill_id in item.pinned_skill_ids if skill_id in skills
                ),
            )

        return FrozenAgentCatalog(
            root=snapshot(roots[0]),
            candidates=tuple(snapshot(item) for item in candidates),
            requested_ids=requested_ids,
        )
