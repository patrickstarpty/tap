"""Read and resolve server-approved agent and skill revisions."""

from __future__ import annotations

from dataclasses import dataclass

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionRejected,
    SkillRevision,
    text_digest,
)


def _same_project(left: ProjectScopeContext, right: ProjectScopeContext) -> bool:
    return (left.enterprise_id, left.project_id) == (right.enterprise_id, right.project_id)


class ApprovedAssetCatalog:
    """A closed catalog: callers can select approved identities, never content."""

    def __init__(
        self, *, agents: tuple[AiAgentRevision, ...], skills: tuple[SkillRevision, ...]
    ) -> None:
        self._agents = agents
        self._skills = skills

    async def list_agents(self, scope: ProjectScopeContext) -> tuple[AiAgentRevision, ...]:
        return tuple(
            item
            for item in self._agents
            if _same_project(item.scope, scope) and item.status == "enabled"
        )

    async def list_skills(self, scope: ProjectScopeContext) -> tuple[SkillRevision, ...]:
        return tuple(
            item
            for item in self._skills
            if _same_project(item.scope, scope) and item.status == "enabled"
        )

    async def get_agent(self, scope: ProjectScopeContext, revision_id: str) -> AiAgentRevision:
        return await self._get(scope, revision_id, self._agents, enabled=True)

    async def get_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision:
        return await self._get(scope, revision_id, self._skills, enabled=True)

    async def resolve_historical_agent(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> AiAgentRevision:
        return await self._get(scope, revision_id, self._agents, enabled=False)

    async def resolve_historical_skill(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> SkillRevision:
        return await self._get(scope, revision_id, self._skills, enabled=False)

    async def resolve_agent(
        self,
        scope: ProjectScopeContext,
        revision_id: str,
        *,
        tools: frozenset[str],
        output_schema_digest: str,
    ) -> AiAgentRevision:
        revision = await self.get_agent(scope, revision_id)
        if (
            not tools <= revision.tool_allowlist
            or output_schema_digest != revision.output_schema_digest
        ):
            raise AssetRevisionRejected()
        return revision

    async def resolve_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision:
        return await self.get_skill(scope, revision_id)

    @staticmethod
    async def _get(scope, revision_id, values, *, enabled):
        for item in values:
            if item.revision_id == revision_id and _same_project(item.scope, scope):
                if not enabled or item.status == "enabled":
                    return item
        raise AssetRevisionRejected()


@dataclass(frozen=True, slots=True)
class ValidationAssetSeed:
    version: str
    agents: tuple[AiAgentRevision, ...]
    skills: tuple[SkillRevision, ...]


def validation_asset_seed(scope: ProjectScopeContext) -> ValidationAssetSeed:
    """Versioned server configuration; scope is supplied by trusted composition."""
    return ValidationAssetSeed(
        version="validation-ai-assets-v1",
        agents=(
            AiAgentRevision(
                revision_id="validation-knowledge-agent-v1",
                asset_id="validation-knowledge-agent",
                display_name="Knowledge agent",
                scope=scope,
                content_digest=text_digest("validation-ai-agent-v1"),
                system_instruction_digest=text_digest("knowledge-agent-system-instruction-v1"),
                tool_allowlist=frozenset({"knowledge.search", "knowledge.answer"}),
                output_schema_digest=text_digest("knowledge-agent-output-schema-v1"),
            ),
        ),
        skills=(
            SkillRevision(
                revision_id="validation-citation-skill-v1",
                asset_id="validation-citation-skill",
                display_name="Citation skill",
                scope=scope,
                content_digest=text_digest("validation-citation-skill-v1"),
                instruction_template_digest=text_digest("citation-skill-template-v1"),
                applicable_tasks=frozenset({"knowledge.answer"}),
            ),
        ),
    )
