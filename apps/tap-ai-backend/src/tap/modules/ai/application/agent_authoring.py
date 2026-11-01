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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.agent_team.domain.classify import classify_revision
from tap.modules.agent_team.domain.models import MaintenanceEvent, MaintenanceKind, new_event
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
    # Deprecated: each bound revision is mapped to its Skill id and merged
    # into `pinned_skill_ids` (spec 5); still stored as given, read-only.
    skill_revision_ids: tuple[str, ...] = ()
    tool_ids: frozenset[str] = frozenset()
    default_model_alias: str | None = None
    pinned_skill_ids: tuple[str, ...] = ()
    excluded_skill_ids: tuple[str, ...] = ()


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
        self,
        revision: AiAgentRevision,
        *,
        base_revision_id: str | None,
        event: MaintenanceEvent | None = None,
    ) -> AiAgentRevision: ...

    async def set_asset_enabled(
        self,
        kind: Literal["agent", "skill"],
        asset_id: str,
        enabled: bool,
        *,
        event: MaintenanceEvent | None = None,
    ) -> None: ...

    async def find_agent_by_name(
        self, scope: ProjectScopeContext, name: str
    ) -> AiAgentRevision | None: ...


class ModelCatalogPort(Protocol):
    async def list_models(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]: ...


async def compute_outdated_skill_ids(
    catalog: SkillLookup, scope: ProjectScopeContext, skill_revision_ids: tuple[str, ...]
) -> frozenset[str]:
    """Deprecated: always empty. Since Skill Hub (spec 1.2) a pinned Skill
    always loads its latest enabled revision, so there is no "upgrade" to
    offer; kept so `hasSkillUpdates` stays `false` for existing callers."""
    return frozenset()


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


# Same bound as the contract's `pinnedSkillIds`/`excludedSkillIds` lists: the
# deprecated `skillRevisionIds` mapping must not push the stored pinned set
# past what `AiAgentRevisionSummary` can serialize.
MAX_AGENT_SKILL_IDS = 64


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
            tolerate_disabled_skill_ids=frozenset(latest.pinned_skill_ids),
        )
        skill_of = await self._skill_of(
            scope, set(latest.skill_revision_ids) | set(revision.skill_revision_ids)
        )
        kind = classify_revision(latest, revision, skill_of)
        event = new_event(
            scope, agent_id, kind, revision_id=revision.revision_id, now=datetime.now(UTC)
        )
        return await self._catalog.insert_agent_revision(
            revision, base_revision_id=base_revision_id, event=event
        )

    async def set_enabled(self, scope: ProjectScopeContext, agent_id: str, enabled: bool) -> None:
        self._assert_scope(scope)
        history = await self._catalog.list_revisions("agent", agent_id)
        if not history:
            raise AssetRevisionRejected()
        latest = cast(AiAgentRevision, history[-1])
        if latest.source_kind == "built_in":
            raise AgentAuthoringRejected(
                "agent_built_in_immutable",
                "built-in agents cannot be disabled or enabled",
            )
        target_status = AssetRevisionStatus.ENABLED if enabled else AssetRevisionStatus.DISABLED
        event: MaintenanceEvent | None = None
        if latest.status != target_status:
            kind: MaintenanceKind = "enabled" if enabled else "disabled"
            event = new_event(scope, agent_id, kind, now=datetime.now(UTC))
        await self._catalog.set_asset_enabled("agent", agent_id, enabled, event=event)

    async def set_skills(
        self,
        scope: ProjectScopeContext,
        agent_id: str,
        pinned: tuple[str, ...],
        excluded: tuple[str, ...],
    ) -> AiAgentRevision:
        """Write a new revision of the Agent that only changes its pinned and
        excluded Skills, based on its latest revision."""
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
        pinned_ids, excluded_ids = _unique(tuple(pinned)), _unique(tuple(excluded))
        await self._validate_skill_ids(
            pinned=pinned_ids,
            excluded=excluded_ids,
            tolerate_disabled=frozenset(latest.pinned_skill_ids),
        )
        # Keep the deprecated binding consistent with the new pinned set: a
        # bound revision stays only while its Skill is still pinned, so a
        # client round-tripping `skillRevisionIds` cannot re-pin a Skill just
        # removed here, and the binding-based chat path keeps the rest.
        skill_of = await self._skill_of(scope, set(latest.skill_revision_ids))
        kept_revision_ids = tuple(
            revision_id
            for revision_id in latest.skill_revision_ids
            if skill_of.get(revision_id) in pinned_ids
        )
        revision = replace(
            latest,
            revision_id=f"agent-rev-{uuid4().hex[:16]}",
            # The writer's scope, not the base revision's creator.
            scope=scope,
            adopted_from_revision_id=latest.revision_id,
            pinned_skill_ids=pinned_ids,
            excluded_skill_ids=excluded_ids,
            skill_revision_ids=kept_revision_ids,
        )
        event = new_event(
            scope, agent_id, "revised", revision_id=revision.revision_id, now=datetime.now(UTC)
        )
        return await self._catalog.insert_agent_revision(
            revision, base_revision_id=latest.revision_id, event=event
        )

    async def _validate_skill_ids(
        self,
        *,
        pinned: tuple[str, ...],
        excluded: tuple[str, ...],
        tolerate_disabled: frozenset[str],
    ) -> None:
        """Validate pinned/excluded Skill ids: disjoint, every id a Skill of
        this project, and a newly pinned Skill enabled. A Skill the Agent
        already pinned tolerates having been disabled since (it is skipped
        at Turn time), so saving is never blocked by it."""
        if len(pinned) > MAX_AGENT_SKILL_IDS or len(excluded) > MAX_AGENT_SKILL_IDS:
            raise AgentAuthoringRejected(
                "agent_skill_limit_exceeded",
                f"an agent can pin and exclude at most {MAX_AGENT_SKILL_IDS} skills each",
            )
        if set(pinned) & set(excluded):
            raise AgentAuthoringRejected(
                "agent_skill_conflict", "a skill cannot be both pinned and excluded"
            )
        for skill_id in (*pinned, *excluded):
            history = await self._catalog.list_revisions("skill", skill_id)
            latest = cast(SkillRevision, history[-1]) if history else None
            if latest is None or (
                skill_id in pinned
                and skill_id not in tolerate_disabled
                and latest.status != AssetRevisionStatus.ENABLED
            ):
                raise AgentAuthoringRejected(
                    "agent_skill_unavailable", f"skill {skill_id!r} is not an available skill"
                )

    async def _skill_of(
        self, scope: ProjectScopeContext, skill_revision_ids: set[str]
    ) -> dict[str, str]:
        skill_of: dict[str, str] = {}
        for revision_id in skill_revision_ids:
            try:
                skill = await self._catalog.resolve_historical_skill(scope, revision_id)
            except AssetRevisionRejected:
                continue
            skill_of[revision_id] = skill.asset_id
        return skill_of

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
        tolerate_disabled_skill_ids: frozenset[str] = frozenset(),
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
        bound_skill_ids: list[str] = []
        for revision_id in draft.skill_revision_ids:
            try:
                if revision_id in tolerate_disabled_ids:
                    # I-6: a Skill the Agent already had bound tolerates
                    # becoming disabled since -- resolve any status so a
                    # revise is never blocked by it.
                    bound = await self._catalog.resolve_historical_skill(scope, revision_id)
                else:
                    bound = await self._catalog.get_skill(scope, revision_id)
            except AssetRevisionRejected as error:
                raise AgentAuthoringRejected(
                    "agent_skill_unavailable",
                    f"skill revision {revision_id!r} is not an enabled skill",
                ) from error
            bound_skill_ids.append(bound.asset_id)
        # The deprecated binding counts as pinned (spec 5); the Skills it
        # names were just validated above, so tolerate them here.
        pinned_ids = _unique((*draft.pinned_skill_ids, *bound_skill_ids))
        excluded_ids = _unique(tuple(draft.excluded_skill_ids))
        await self._validate_skill_ids(
            pinned=pinned_ids,
            excluded=excluded_ids,
            tolerate_disabled=tolerate_disabled_skill_ids | frozenset(bound_skill_ids),
        )
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
            pinned_skill_ids=pinned_ids,
            excluded_skill_ids=excluded_ids,
            default_model_alias=draft.default_model_alias,
            adopted_from_revision_id=adopted_from_revision_id,
            status=status,
        )
