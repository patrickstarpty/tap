"""Compose Agents from Skills, tools and a default model.

Agent authoring never stages or promotes file bytes (unlike Skill authoring):
an ``AgentDraft`` is pure text plus references to already-approved Skill
revisions and tool ids. This service validates those references against the
closed tool registry (Task 1) and the project's enabled Skills/models, then
writes an immutable revision through the narrow ``AgentAuthoringCatalog``
port (Task 2's ``MysqlAssetCatalog``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionRejected,
    AssetRevisionStatus,
    SkillRevision,
    text_digest,
)
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor, schema_digest
from tap.modules.ai.domain.tools import known_tool_ids, required_tool_ids

_AGENT_TASK = "knowledge.answer"
# Authoring (Task 6) has no per-Agent custom output-schema field — every
# knowledge.answer turn (built-in or authored Agent alike) is governed by
# this one fixed answer+claims JSON schema (see
# `knowledge_service.py::_generation_governance` and
# `resolve_agent_selection`, which both require a *matching*
# non-null `output_schema_json`/`_digest`, not merely a present one).
# Leaving these `None`/empty here — Task 6's original placeholder — is
# exactly why authored/imported Agents 404 as `asset-revision-unavailable`
# the moment anyone tries to use one in a conversation.
_AGENT_OUTPUT_SCHEMA_JSON = json.dumps(
    VALIDATION_OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":")
)
_AGENT_OUTPUT_SCHEMA_DIGEST = schema_digest(VALIDATION_OUTPUT_SCHEMA)


class AgentAuthoringRejected(Exception):
    """An Agent draft failed validation; carries the service's own closed
    error vocabulary, mirroring Skill authoring's ``SkillPackageRejected``."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentDraft:
    name: str
    description: str
    system_instruction: str
    skill_revision_ids: tuple[str, ...] = ()
    tool_ids: frozenset[str] = frozenset()
    default_model_alias: str | None = None


class SkillLookup(Protocol):
    """The slice of the catalog needed to resolve bound Skill revisions and
    detect when a bound revision has been superseded."""

    async def get_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision: ...

    async def resolve_historical_skill(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> SkillRevision: ...

    async def list_revisions(
        self, kind: Literal["agent", "skill"], asset_id: str
    ) -> tuple[AiAgentRevision, ...] | tuple[SkillRevision, ...]: ...


class AgentAuthoringCatalog(SkillLookup, Protocol):
    """The narrow slice of the approved AI catalog this service depends on."""

    async def insert_agent_revision(
        self, revision: AiAgentRevision, *, base_revision_id: str | None
    ) -> AiAgentRevision: ...

    async def set_asset_enabled(
        self, kind: Literal["agent", "skill"], asset_id: str, enabled: bool
    ) -> None: ...

    async def find_agent_by_name(
        self, scope: ProjectScopeContext, name: str
    ) -> AiAgentRevision | None: ...


class ModelCatalogPort(Protocol):
    async def list_models(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]: ...


async def compute_outdated_skill_ids(
    catalog: SkillLookup, scope: ProjectScopeContext, skill_revision_ids: tuple[str, ...]
) -> frozenset[str]:
    """Which of an Agent's bound Skill revision ids are no longer the latest
    (or no longer enabled) revision of their Skill.

    A bound revision that is no longer resolvable at all (disabled or
    removed) is conservatively treated as outdated: the caller cannot tell
    whether a newer revision superseded it, but it is certainly not current.
    """

    outdated: set[str] = set()
    for revision_id in skill_revision_ids:
        try:
            skill = await catalog.get_skill(scope, revision_id)
        except AssetRevisionRejected:
            outdated.add(revision_id)
            continue
        history = await catalog.list_revisions("skill", skill.asset_id)
        if not history or history[-1].revision_id != revision_id:
            outdated.add(revision_id)
    return frozenset(outdated)


class AgentAuthoringService:
    def __init__(
        self,
        catalog: AgentAuthoringCatalog,
        models: ModelCatalogPort,
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self._catalog = catalog
        self._models = models
        self._scope = scope

    def _assert_scope(self, scope: ProjectScopeContext) -> None:
        if type(scope) is not ProjectScopeContext or scope != self._scope:
            raise AssetRevisionRejected()

    async def create(self, scope: ProjectScopeContext, draft: AgentDraft) -> AiAgentRevision:
        self._assert_scope(scope)
        existing = await self._catalog.find_agent_by_name(scope, draft.name)
        if existing is not None:
            raise AgentAuthoringRejected(
                "agent_name_conflict", f"an agent named {draft.name!r} already exists"
            )
        revision = await self._build_revision(scope, draft, asset_id=f"agent-{uuid4().hex[:16]}")
        return await self._catalog.insert_agent_revision(revision, base_revision_id=None)

    async def revise(
        self,
        scope: ProjectScopeContext,
        agent_id: str,
        base_revision_id: str,
        draft: AgentDraft,
    ) -> AiAgentRevision:
        self._assert_scope(scope)
        history = await self._catalog.list_revisions("agent", agent_id)
        if not history:
            raise AssetRevisionRejected()
        latest = cast(AiAgentRevision, history[-1])
        if latest.source_kind == "built_in":
            raise AgentAuthoringRejected(
                "agent_built_in_immutable",
                "built-in agents cannot be revised; create a new agent instead",
            )
        existing = await self._catalog.find_agent_by_name(scope, draft.name)
        if existing is not None and existing.asset_id != agent_id:
            raise AgentAuthoringRejected(
                "agent_name_conflict", f"an agent named {draft.name!r} already exists"
            )
        revision = await self._build_revision(
            scope,
            draft,
            asset_id=agent_id,
            adopted_from_revision_id=base_revision_id,
            # Decide revise-of-disabled explicitly (C-2): keep the asset
            # disabled across a revise rather than silently re-enabling it.
            status=latest.status,
            # Skills the Agent already had bound tolerate becoming disabled
            # in the meantime (I-6): revise must not be a permanent save
            # dead-end for them. A newly-added skill id still has to be
            # enabled, same as `create`.
            tolerate_disabled_ids=frozenset(latest.skill_revision_ids),
        )
        return await self._catalog.insert_agent_revision(
            revision, base_revision_id=base_revision_id
        )

    async def set_enabled(self, scope: ProjectScopeContext, agent_id: str, enabled: bool) -> None:
        self._assert_scope(scope)
        history = await self._catalog.list_revisions("agent", agent_id)
        if not history:
            raise AssetRevisionRejected()
        if history[-1].source_kind == "built_in":
            raise AgentAuthoringRejected(
                "agent_built_in_immutable",
                "built-in agents cannot be disabled or enabled",
            )
        await self._catalog.set_asset_enabled("agent", agent_id, enabled)

    async def outdated_skill_ids(
        self, scope: ProjectScopeContext, revision: AiAgentRevision
    ) -> frozenset[str]:
        self._assert_scope(scope)
        return await compute_outdated_skill_ids(self._catalog, scope, revision.skill_revision_ids)

    async def _build_revision(
        self,
        scope: ProjectScopeContext,
        draft: AgentDraft,
        *,
        asset_id: str,
        adopted_from_revision_id: str | None = None,
        status: AssetRevisionStatus = AssetRevisionStatus.ENABLED,
        tolerate_disabled_ids: frozenset[str] = frozenset(),
    ) -> AiAgentRevision:
        if not isinstance(draft, AgentDraft):
            raise AgentAuthoringRejected("agent_frontmatter_invalid", "draft must be an AgentDraft")
        if (
            not isinstance(draft.system_instruction, str)
            or not draft.system_instruction.strip()
            or len(draft.system_instruction.encode("utf-8")) > 32 * 1024
        ):
            raise AgentAuthoringRejected(
                "agent_system_instruction_too_large",
                "systemInstruction must be non-empty and at most 32 KiB",
            )
        # Built-in tools (knowledge search/answer) are always enabled per
        # spec and shown read-only in the UI, which never sends `toolIds`
        # (defaults to `[]`) — union the registry's required ids in before
        # validating, so an empty/partial draft still succeeds. An unknown
        # id the caller did supply is not swept away by this and still
        # fails the upper-bound check below.
        required = required_tool_ids(_AGENT_TASK)
        tool_ids = frozenset(draft.tool_ids) | required
        if not (required <= tool_ids <= known_tool_ids()):
            raise AgentAuthoringRejected(
                "agent_tool_allowlist_invalid",
                "toolIds must include every required tool and only known tool ids",
            )
        for revision_id in draft.skill_revision_ids:
            try:
                if revision_id in tolerate_disabled_ids:
                    # I-6: a Skill the Agent already had bound tolerates
                    # becoming disabled since -- resolve any status so a
                    # revise is never blocked by it.
                    await self._catalog.resolve_historical_skill(scope, revision_id)
                else:
                    await self._catalog.get_skill(scope, revision_id)
            except AssetRevisionRejected as error:
                raise AgentAuthoringRejected(
                    "agent_skill_unavailable",
                    f"skill revision {revision_id!r} is not an enabled skill",
                ) from error
        if draft.default_model_alias is not None:
            models = await self._models.list_models(scope)
            selectable = {
                model.alias for model in models if ModelCapability.STRUCTURED in model.capabilities
            }
            if draft.default_model_alias not in selectable:
                raise AgentAuthoringRejected(
                    "agent_model_not_selectable",
                    f"model {draft.default_model_alias!r} is not a selectable structured model",
                )
        return AiAgentRevision(
            revision_id=f"agent-rev-{uuid4().hex[:16]}",
            asset_id=asset_id,
            display_name=draft.name,
            scope=scope,
            content_digest=text_digest(f"{draft.name}\0{draft.description}"),
            system_instruction_digest=text_digest(draft.system_instruction),
            tool_allowlist=tool_ids,
            output_schema_digest=_AGENT_OUTPUT_SCHEMA_DIGEST,
            system_instruction=draft.system_instruction,
            output_schema_json=_AGENT_OUTPUT_SCHEMA_JSON,
            description=draft.description,
            source_kind="authored",
            skill_revision_ids=tuple(draft.skill_revision_ids),
            default_model_alias=draft.default_model_alias,
            adopted_from_revision_id=adopted_from_revision_id,
            status=status,
        )
