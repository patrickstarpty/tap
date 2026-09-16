"""Read and resolve server-approved agent and skill revisions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionRejected,
    SkillRevision,
    text_digest,
)
from tap.modules.ai.domain.models import schema_digest

VALIDATION_SYSTEM_INSTRUCTION = (
    "Use only authorized Knowledge evidence and follow the selected skills."
)
VALIDATION_SKILL_INSTRUCTION = "Cite every factual claim with one or more supplied evidence labels."
VALIDATION_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "claims"],
    "properties": {
        "answer": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "evidenceLabels"],
                "properties": {
                    "text": {"type": "string"},
                    "evidenceLabels": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


def _same_project(left: ProjectScopeContext, right: ProjectScopeContext) -> bool:
    return (left.enterprise_id, left.project_id) == (right.enterprise_id, right.project_id)


def resolve_agent_selection(
    revision: AiAgentRevision, *, tools: frozenset[str], output_schema_digest: str
) -> AiAgentRevision:
    if (
        type(tools) is not frozenset
        or not tools <= revision.tool_allowlist
        or output_schema_digest != revision.output_schema_digest
        or revision.system_instruction is None
        or revision.output_schema_json is None
    ):
        raise AssetRevisionRejected()
    return revision


def resolve_skill_selection(revision: SkillRevision, *, task: str) -> SkillRevision:
    if task not in revision.applicable_tasks or revision.instruction_template is None:
        raise AssetRevisionRejected()
    return revision


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
            if _same_project(item.scope, scope)
            and item.status == "enabled"
            and item.system_instruction is not None
            and item.output_schema_json is not None
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
        return resolve_agent_selection(
            revision, tools=tools, output_schema_digest=output_schema_digest
        )

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
        version="validation-ai-assets-v2",
        agents=(
            AiAgentRevision(
                revision_id="validation-knowledge-agent-v2",
                asset_id="validation-knowledge-agent",
                display_name="Knowledge agent",
                scope=scope,
                content_digest=text_digest("validation-ai-agent-v2"),
                system_instruction_digest=text_digest(VALIDATION_SYSTEM_INSTRUCTION),
                tool_allowlist=frozenset({"knowledge.search", "knowledge.answer"}),
                output_schema_digest=schema_digest(VALIDATION_OUTPUT_SCHEMA),
                system_instruction=VALIDATION_SYSTEM_INSTRUCTION,
                output_schema_json=json.dumps(
                    VALIDATION_OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":")
                ),
            ),
        ),
        skills=(
            SkillRevision(
                revision_id="validation-citation-skill-v2",
                asset_id="validation-citation-skill",
                display_name="Citation skill",
                scope=scope,
                content_digest=text_digest("validation-citation-skill-v2"),
                instruction_template_digest=text_digest(VALIDATION_SKILL_INSTRUCTION),
                applicable_tasks=frozenset({"knowledge.answer"}),
                instruction_template=VALIDATION_SKILL_INSTRUCTION,
            ),
        ),
    )
