"""AgentAuthoringService: compose Agents from Skills, tools and a default model.

Uses in-memory doubles for the catalog and model-catalog ports so these tests
exercise only the service's own validation and orchestration logic.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.agent_authoring import (
    AgentAuthoringRejected,
    AgentAuthoringService,
    AgentDraft,
)
from tap.modules.ai.domain.assets import (
    AssetRevisionRejected,
    AssetRevisionStatus,
    RevisionConflict,
    SkillRevision,
    text_digest,
)
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor
from tap.modules.ai.domain.tools import required_tool_ids


class _FakeCatalog:
    def __init__(self) -> None:
        self.scope = VALIDATION_SCOPE
        self._agents: dict[str, list] = {}
        self._skills: dict[str, list[SkillRevision]] = {}

    # -- agent write port -------------------------------------------------

    async def insert_agent_revision(self, revision, *, base_revision_id):
        history = self._agents.setdefault(revision.asset_id, [])
        latest = history[-1].revision_id if history else None
        if latest != base_revision_id:
            raise RevisionConflict()
        history.append(revision)
        return revision

    async def set_asset_enabled(self, kind, asset_id, enabled):
        store = self._agents if kind == "agent" else self._skills
        history = store.get(asset_id)
        if not history:
            raise AssetRevisionRejected()
        status = AssetRevisionStatus.ENABLED if enabled else AssetRevisionStatus.DISABLED
        history[-1] = replace(history[-1], status=status)

    async def list_revisions(self, kind, asset_id):
        store = self._agents if kind == "agent" else self._skills
        return tuple(store.get(asset_id, ()))

    async def find_agent_by_name(self, scope, name):
        for history in self._agents.values():
            if history and history[-1].display_name == name:
                return history[-1]
        return None

    # -- skill read port (shared with AssetCatalogHttpService) -----------

    async def get_skill(self, scope, revision_id):
        for history in self._skills.values():
            for revision in history:
                if revision.revision_id == revision_id and (
                    revision.status == AssetRevisionStatus.ENABLED
                ):
                    return revision
        raise AssetRevisionRejected()

    # -- test seeding helper -----------------------------------------------

    def seed_skill(self, revision: SkillRevision) -> None:
        self._skills.setdefault(revision.asset_id, []).append(revision)


class _FakeModels:
    def __init__(self, models: tuple[ModelDescriptor, ...]) -> None:
        self._models = models

    async def list_models(self, scope):
        return self._models


def _skill_revision(
    *, asset_id: str, revision_id: str, status: AssetRevisionStatus = AssetRevisionStatus.ENABLED
) -> SkillRevision:
    return SkillRevision(
        revision_id=revision_id,
        asset_id=asset_id,
        display_name="demo-skill",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("demo-skill-content"),
        instruction_template_digest=text_digest("demo-skill-instructions"),
        applicable_tasks=frozenset({"knowledge.answer"}),
        instruction_template="demo-skill-instructions",
        status=status,
        source_kind="authored",
    )


def _service(catalog=None, models=None):
    catalog = catalog if catalog is not None else _FakeCatalog()
    models = models if models is not None else _FakeModels(())
    return AgentAuthoringService(catalog, models, scope=VALIDATION_SCOPE), catalog


def _draft(**overrides) -> AgentDraft:
    fields = {
        "name": "demo-agent",
        "description": "A demo agent for authoring tests.",
        "system_instruction": "Answer using only approved knowledge.",
        "skill_revision_ids": (),
        "tool_ids": frozenset({"knowledge.search", "knowledge.answer"}),
        "default_model_alias": None,
    }
    fields.update(overrides)
    return AgentDraft(**fields)


# -- create ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_authored_enabled_revision() -> None:
    service, _catalog = _service()

    revision = await service.create(VALIDATION_SCOPE, _draft())

    assert revision.source_kind == "authored"
    assert revision.status == AssetRevisionStatus.ENABLED
    assert revision.display_name == "demo-agent"
    assert revision.system_instruction == "Answer using only approved knowledge."
    assert revision.tool_allowlist == frozenset({"knowledge.search", "knowledge.answer"})
    assert revision.adopted_from_revision_id is None


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name_in_project() -> None:
    service, _catalog = _service()
    await service.create(VALIDATION_SCOPE, _draft())

    with pytest.raises(AgentAuthoringRejected) as excinfo:
        await service.create(VALIDATION_SCOPE, _draft())

    assert excinfo.value.code == "agent_name_conflict"


@pytest.mark.asyncio
async def test_create_rejects_system_instruction_over_32kb() -> None:
    service, _catalog = _service()
    oversized = "x" * (32 * 1024 + 1)

    with pytest.raises(AgentAuthoringRejected) as excinfo:
        await service.create(VALIDATION_SCOPE, _draft(system_instruction=oversized))

    assert excinfo.value.code == "agent_system_instruction_too_large"


@pytest.mark.asyncio
async def test_create_fills_in_required_tools_when_draft_omits_them() -> None:
    # The UI never sends `toolIds` (built-in tools are always enabled and
    # shown read-only, per spec), so the service must union the registry's
    # required ids into the draft before validating — a bare `AgentDraft()`
    # default (empty `tool_ids`) must succeed, not 400.
    service, _catalog = _service()

    revision = await service.create(VALIDATION_SCOPE, _draft(tool_ids=frozenset()))

    assert revision.tool_allowlist == required_tool_ids("knowledge.answer")


@pytest.mark.asyncio
async def test_create_rejects_unknown_tool_id() -> None:
    service, _catalog = _service()

    with pytest.raises(AgentAuthoringRejected) as excinfo:
        await service.create(
            VALIDATION_SCOPE,
            _draft(tool_ids=frozenset({"knowledge.search", "knowledge.answer", "not-a-real-tool"})),
        )

    assert excinfo.value.code == "agent_tool_allowlist_invalid"


@pytest.mark.asyncio
async def test_create_rejects_unknown_default_model_alias() -> None:
    service, _catalog = _service(models=_FakeModels(()))

    with pytest.raises(AgentAuthoringRejected) as excinfo:
        await service.create(VALIDATION_SCOPE, _draft(default_model_alias="not-a-model"))

    assert excinfo.value.code == "agent_model_not_selectable"


@pytest.mark.asyncio
async def test_create_rejects_default_model_without_structured_capability() -> None:
    models = _FakeModels(
        (
            ModelDescriptor(
                alias="chat-only",
                display_name="Chat only",
                capabilities=frozenset({ModelCapability.CHAT}),
            ),
        )
    )
    service, _catalog = _service(models=models)

    with pytest.raises(AgentAuthoringRejected) as excinfo:
        await service.create(VALIDATION_SCOPE, _draft(default_model_alias="chat-only"))

    assert excinfo.value.code == "agent_model_not_selectable"


@pytest.mark.asyncio
async def test_create_accepts_structured_default_model_alias() -> None:
    models = _FakeModels(
        (
            ModelDescriptor(
                alias="structured-model",
                display_name="Structured model",
                capabilities=frozenset({ModelCapability.STRUCTURED}),
            ),
        )
    )
    service, _catalog = _service(models=models)

    revision = await service.create(
        VALIDATION_SCOPE, _draft(default_model_alias="structured-model")
    )

    assert revision.default_model_alias == "structured-model"


@pytest.mark.asyncio
async def test_create_rejects_disabled_skill_binding() -> None:
    service, catalog = _service()
    catalog.seed_skill(
        _skill_revision(
            asset_id="skill-1", revision_id="skill-rev-1", status=AssetRevisionStatus.DISABLED
        )
    )

    with pytest.raises(AgentAuthoringRejected) as excinfo:
        await service.create(VALIDATION_SCOPE, _draft(skill_revision_ids=("skill-rev-1",)))

    assert excinfo.value.code == "agent_skill_unavailable"


@pytest.mark.asyncio
async def test_create_accepts_enabled_skill_binding() -> None:
    service, catalog = _service()
    catalog.seed_skill(_skill_revision(asset_id="skill-1", revision_id="skill-rev-1"))

    revision = await service.create(VALIDATION_SCOPE, _draft(skill_revision_ids=("skill-rev-1",)))

    assert revision.skill_revision_ids == ("skill-rev-1",)


# -- revise ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revise_requires_matching_base_revision() -> None:
    service, _catalog = _service()
    created = await service.create(VALIDATION_SCOPE, _draft())

    with pytest.raises(RevisionConflict):
        await service.revise(
            VALIDATION_SCOPE, created.asset_id, "not-the-real-base", _draft(description="v2")
        )

    revised = await service.revise(
        VALIDATION_SCOPE, created.asset_id, created.revision_id, _draft(description="v2")
    )
    assert revised.revision_id != created.revision_id
    assert revised.description == "v2"
    assert revised.adopted_from_revision_id == created.revision_id


@pytest.mark.asyncio
async def test_revise_unknown_agent_is_asset_revision_rejected() -> None:
    service, _catalog = _service()

    with pytest.raises(AssetRevisionRejected):
        await service.revise(VALIDATION_SCOPE, "unknown-agent", "unknown-rev", _draft())


@pytest.mark.asyncio
async def test_built_in_agent_cannot_be_revised_but_can_be_copied() -> None:
    service, catalog = _service()
    built_in = await service.create(VALIDATION_SCOPE, _draft(name="built-in-agent"))
    catalog._agents[built_in.asset_id][-1] = replace(built_in, source_kind="built_in")

    with pytest.raises(AgentAuthoringRejected) as excinfo:
        await service.revise(
            VALIDATION_SCOPE, built_in.asset_id, built_in.revision_id, _draft(name="built-in-agent")
        )
    assert excinfo.value.code == "agent_built_in_immutable"

    copy = await service.create(
        VALIDATION_SCOPE,
        _draft(name="built-in-agent-copy", system_instruction=built_in.system_instruction),
    )
    assert copy.source_kind == "authored"
    assert copy.system_instruction == built_in.system_instruction


# -- set_enabled ---------------------------------------------------------


@pytest.mark.asyncio
async def test_set_enabled_toggles_latest_revision_status() -> None:
    service, catalog = _service()
    created = await service.create(VALIDATION_SCOPE, _draft())

    await service.set_enabled(VALIDATION_SCOPE, created.asset_id, False)
    disabled = (await catalog.list_revisions("agent", created.asset_id))[-1]
    assert disabled.status == AssetRevisionStatus.DISABLED

    await service.set_enabled(VALIDATION_SCOPE, created.asset_id, True)
    enabled = (await catalog.list_revisions("agent", created.asset_id))[-1]
    assert enabled.status == AssetRevisionStatus.ENABLED


# -- outdated_skill_ids / hasSkillUpdates --------------------------------


@pytest.mark.asyncio
async def test_outdated_skill_ids_is_empty_when_bound_revision_is_latest() -> None:
    service, catalog = _service()
    catalog.seed_skill(_skill_revision(asset_id="skill-1", revision_id="skill-rev-1"))
    agent = await service.create(VALIDATION_SCOPE, _draft(skill_revision_ids=("skill-rev-1",)))

    outdated = await service.outdated_skill_ids(VALIDATION_SCOPE, agent)

    assert outdated == frozenset()


@pytest.mark.asyncio
async def test_outdated_skill_ids_flags_superseded_revision() -> None:
    service, catalog = _service()
    catalog.seed_skill(_skill_revision(asset_id="skill-1", revision_id="skill-rev-1"))
    agent = await service.create(VALIDATION_SCOPE, _draft(skill_revision_ids=("skill-rev-1",)))

    # A new Skill revision supersedes the one the Agent is bound to.
    catalog.seed_skill(_skill_revision(asset_id="skill-1", revision_id="skill-rev-2"))

    outdated = await service.outdated_skill_ids(VALIDATION_SCOPE, agent)

    assert outdated == frozenset({"skill-rev-1"})


@pytest.mark.asyncio
async def test_outdated_skill_ids_flags_disabled_bound_revision() -> None:
    service, catalog = _service()
    catalog.seed_skill(_skill_revision(asset_id="skill-1", revision_id="skill-rev-1"))
    agent = await service.create(VALIDATION_SCOPE, _draft(skill_revision_ids=("skill-rev-1",)))

    catalog._skills["skill-1"][-1] = replace(
        catalog._skills["skill-1"][-1], status=AssetRevisionStatus.DISABLED
    )

    outdated = await service.outdated_skill_ids(VALIDATION_SCOPE, agent)

    assert outdated == frozenset({"skill-rev-1"})
