"""Author immutable Agent revisions from instructions, Skills, tools and a model."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
from tap.modules.ai.application.tool_registry import ToolRegistry
from tap.modules.ai.domain.assets import (
    MAX_AGENT_DESCRIPTION_LENGTH,
    MAX_AGENT_SKILLS,
    MAX_AGENT_SYSTEM_INSTRUCTION_LENGTH,
    MAX_MODEL_ALIAS_LENGTH,
    AiAgentRevision,
    AssetRevisionRejected,
    AssetRevisionStatus,
    SkillRevision,
    text_digest,
)
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor, schema_digest
from tap.modules.ai.domain.tools import ToolRejected

ANSWER_CAPABILITY = "knowledge.answer"
_MAX_DISPLAY_NAME_LENGTH = 128
_OUTPUT_SCHEMA_JSON = json.dumps(VALIDATION_OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":"))
_OUTPUT_SCHEMA_DIGEST = schema_digest(VALIDATION_OUTPUT_SCHEMA)


class AgentAuthoringRejected(ValueError):
    """An Agent draft violates the authoring contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AgentDraft:
    display_name: str
    description: str
    system_instruction: str
    skill_revision_ids: tuple[str, ...]
    tool_allowlist: frozenset[str]
    model_alias: str | None


class AgentAuthoringStore(Protocol):
    async def get_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision: ...

    async def latest_agent_revision(
        self, scope: ProjectScopeContext, asset_id: str
    ) -> AiAgentRevision: ...

    async def insert_agent_revision(
        self, scope: ProjectScopeContext, revision: AiAgentRevision
    ) -> AiAgentRevision: ...

    async def set_agent_status(
        self, scope: ProjectScopeContext, revision_id: str, status: AssetRevisionStatus
    ) -> AiAgentRevision: ...


class ModelCatalogPort(Protocol):
    async def list_models(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]: ...


class AgentAuthoringService:
    def __init__(
        self,
        store: AgentAuthoringStore,
        tools: ToolRegistry,
        models: ModelCatalogPort,
        *,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._store = store
        self._tools = tools
        self._models = models
        self._ids = id_factory

    async def create(self, scope: ProjectScopeContext, draft: AgentDraft) -> AiAgentRevision:
        revision = await self._build(scope, draft, asset_id=self._ids(), predecessor=None)
        return await self._store.insert_agent_revision(scope, revision)

    async def revise(
        self, scope: ProjectScopeContext, asset_id: str, draft: AgentDraft
    ) -> AiAgentRevision:
        latest = await self._store.latest_agent_revision(scope, asset_id)
        revision = await self._build(
            scope, draft, asset_id=latest.asset_id, predecessor=latest.revision_id
        )
        return await self._store.insert_agent_revision(scope, revision)

    async def set_status(
        self, scope: ProjectScopeContext, revision_id: str, status: AssetRevisionStatus
    ) -> AiAgentRevision:
        if not isinstance(status, AssetRevisionStatus):
            raise AssetRevisionRejected()
        return await self._store.set_agent_status(scope, revision_id, status)

    async def _build(
        self,
        scope: ProjectScopeContext,
        draft: AgentDraft,
        *,
        asset_id: str,
        predecessor: str | None,
    ) -> AiAgentRevision:
        display_name = _text(draft.display_name, _MAX_DISPLAY_NAME_LENGTH, "agent-name-invalid")
        description = draft.description
        if not isinstance(description, str) or len(description) > MAX_AGENT_DESCRIPTION_LENGTH:
            raise AgentAuthoringRejected("agent-description-invalid")
        instruction = _text(
            draft.system_instruction,
            MAX_AGENT_SYSTEM_INSTRUCTION_LENGTH,
            "agent-instruction-invalid",
            strip=False,
        )
        skills = await self._skills(scope, draft.skill_revision_ids)
        allowlist = await self._allowlist(scope, draft.tool_allowlist)
        model_alias = await self._model(
            scope, draft.model_alias, needs_tools=bool(allowlist - {ANSWER_CAPABILITY})
        )
        content = {
            "displayName": display_name,
            "description": description,
            "systemInstruction": instruction,
            "skillRevisionIds": list(skills),
            "toolAllowlist": sorted(allowlist),
            "modelAlias": model_alias,
            "outputSchemaDigest": _OUTPUT_SCHEMA_DIGEST,
        }
        try:
            return AiAgentRevision(
                revision_id=self._ids(),
                asset_id=asset_id,
                display_name=display_name,
                scope=scope,
                content_digest=text_digest(
                    json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                ),
                system_instruction_digest=text_digest(instruction),
                tool_allowlist=allowlist,
                output_schema_digest=_OUTPUT_SCHEMA_DIGEST,
                adopted_from_revision_id=predecessor,
                system_instruction=instruction,
                output_schema_json=_OUTPUT_SCHEMA_JSON,
                model_alias=model_alias,
                description=description,
                skill_revision_ids=skills,
                origin="editor",
            )
        except AssetRevisionRejected as error:
            raise AgentAuthoringRejected("agent-definition-invalid") from error

    async def _skills(self, scope: ProjectScopeContext, values: tuple[str, ...]) -> tuple[str, ...]:
        if (
            type(values) is not tuple
            or len(values) > MAX_AGENT_SKILLS
            or len(set(values)) != len(values)
            or any(not isinstance(item, str) for item in values)
        ):
            raise AgentAuthoringRejected("agent-skills-invalid")
        for revision_id in values:
            try:
                skill = await self._store.get_skill(scope, revision_id)
            except AssetRevisionRejected as error:
                raise AgentAuthoringRejected("agent-skill-unavailable") from error
            if (
                skill.status is not AssetRevisionStatus.ENABLED
                or ANSWER_CAPABILITY not in skill.applicable_tasks
                or skill.instruction_template is None
            ):
                raise AgentAuthoringRejected("agent-skill-unavailable")
        return values

    async def _allowlist(
        self, scope: ProjectScopeContext, values: frozenset[str]
    ) -> frozenset[str]:
        if isinstance(values, str | bytes):
            raise AgentAuthoringRejected("agent-tool-unavailable")
        try:
            # Authored Agents always carry the governed answer capability that conversations
            # require; it is reserved and never exposed as a callable tool.
            return await self._tools.require_allowlist(
                scope, frozenset(values) | {ANSWER_CAPABILITY}
            )
        except (ToolRejected, TypeError) as error:
            raise AgentAuthoringRejected("agent-tool-unavailable") from error

    async def _model(
        self, scope: ProjectScopeContext, alias: str | None, *, needs_tools: bool
    ) -> str | None:
        if alias is None:
            return None
        if not isinstance(alias, str) or not alias or len(alias) > MAX_MODEL_ALIAS_LENGTH:
            raise AgentAuthoringRejected("agent-model-unsupported")
        for item in await self._models.list_models(scope):
            if (
                item.alias == alias
                and item.enabled
                and ModelCapability.STRUCTURED in item.capabilities
                and (not needs_tools or item.supports_tool_calls)
            ):
                return alias
        raise AgentAuthoringRejected("agent-model-unsupported")


def _text(value: object, limit: int, code: str, *, strip: bool = True) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AgentAuthoringRejected(code)
    return value.strip() if strip else value
