"""C-1 regression (fix round 1): `_input_json` (the MySQL write path) must
round-trip through the same field mapping `MysqlConversationRepository._load_turn`
uses to reconstruct a `TurnInput` from the stored row -- in particular
`skill_notices`, which is part of `TurnInput.material()`'s digest and so must
survive a write/read cycle byte-for-byte or the reloaded snapshot's digest
check fails.

This mirrors `_load_turn`'s raw-dict-to-`TurnInput` mapping without a MySQL
connection; the isolated MySQL integration test
(`tests/integration/test_conversation_persistence.py::
test_turn_with_skill_notices_round_trips_through_mysql`) covers the real
write/read path end to end.
"""

from __future__ import annotations

import hashlib

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.skill_activation import SkillCatalogEntry
from tap.modules.chat.adapters.mysql_conversations import _input_json
from tap.modules.chat.domain.conversations import FrozenResource, TurnInput, content_digest


def _reconstruct(raw: dict) -> TurnInput:
    """The same field mapping as `MysqlConversationRepository._load_turn`."""

    return TurnInput(
        message=raw["message"],
        actor_id=raw["actor_id"],
        identity_mode=raw["identity_mode"],
        model_alias=raw["model_alias"],
        source_revision_ids=tuple(raw["source_revision_ids"]),
        document_revision_ids=tuple(raw["document_revision_ids"]),
        resolved_resources=tuple(
            FrozenResource(**item) for item in raw.get("resolved_resources", [])
        ),
        agent_revision_id=raw["agent_revision_id"],
        agent_revision_digest=raw["agent_revision_digest"],
        agent_label=raw.get("agent_label"),
        skill_revision_ids=tuple(raw["skill_revision_ids"]),
        skill_revision_digests=tuple(raw["skill_revision_digests"]),
        skill_labels=tuple(raw.get("skill_labels", [])),
        skill_notices=tuple(raw.get("skill_notices", [])),
        requested_skill_revision_ids=tuple(raw.get("requested_skill_revision_ids", [])),
        skill_catalog=tuple(SkillCatalogEntry(**item) for item in raw.get("skill_catalog", [])),
        agent_system_instruction=raw.get("agent_system_instruction"),
        agent_system_instruction_digest=raw.get("agent_system_instruction_digest"),
        agent_tool_allowlist=tuple(raw.get("agent_tool_allowlist", [])),
        agent_output_schema_json=raw.get("agent_output_schema_json"),
        agent_output_schema_digest=raw.get("agent_output_schema_digest"),
        skill_instruction_templates=tuple(raw.get("skill_instruction_templates", [])),
        skill_instruction_template_digests=tuple(raw.get("skill_instruction_template_digests", [])),
        acl_digest=raw.get("acl_digest", "sha256:" + "0" * 64),
        retrieval_policy_digest=raw["retrieval_policy_digest"],
        insights_query_id=raw.get("insights_query_id"),
        insights_report_refs=tuple(raw.get("insights_report_refs", [])),
    )


def test_input_json_round_trips_skill_notices():
    turn_input = TurnInput(
        message="Persist a notice",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        agent_revision_id="agent-1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("skill-1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        skill_notices=("skill_disabled:skill-0",),
        requested_skill_revision_ids=("skill-0", "skill-1"),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )

    raw = _input_json(turn_input)

    assert raw["skill_notices"] == ["skill_disabled:skill-0"]
    assert raw["requested_skill_revision_ids"] == ["skill-0", "skill-1"]
    reconstructed = _reconstruct(raw)
    assert reconstructed == turn_input
    assert reconstructed.material(project_id="p", turn_id="t") == turn_input.material(
        project_id="p", turn_id="t"
    )


def test_input_json_round_trips_without_skill_notices():
    turn_input = TurnInput(
        message="No notices here",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        retrieval_policy_digest="sha256:" + "c" * 64,
    )

    raw = _input_json(turn_input)

    assert raw["skill_notices"] == []
    assert raw["requested_skill_revision_ids"] == []
    reconstructed = _reconstruct(raw)
    assert reconstructed == turn_input


def test_persona_version_is_frozen_and_legacy_digest_is_stable():
    """Task 1: `persona_version` is a frozen Turn fact when a caller sets it,
    but -- like `skill_notices`/`requested_skill_revision_ids` -- `material()`
    drops it when it is `None` (every Turn frozen before this field existed,
    and any new Turn that does not explicitly pin a persona) so an old Turn's
    persisted digest is not retroactively changed by this field's addition."""

    base_kwargs = dict(
        message="Persona framing",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        retrieval_policy_digest="sha256:" + "c" * 64,
    )

    versioned = TurnInput(**base_kwargs, persona_version="tapper-persona-v1")
    material = versioned.material(project_id="p", turn_id="t")
    assert material["persona_version"] == "tapper-persona-v1"

    legacy = TurnInput(**base_kwargs)
    legacy_material = legacy.material(project_id="p", turn_id="t")
    assert "persona_version" not in legacy_material
    # Every other key is unaffected by this field's addition -- the digest
    # over this dict is exactly what it was before `persona_version` existed.
    assert set(legacy_material) == set(material) - {"persona_version"}


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


_CATALOG = (
    SkillCatalogEntry(
        skill_id="skill-a",
        revision_id="skill-a-r2",
        name="Boundary checks",
        description="Find boundary values",
        category="test-design",
    ),
    SkillCatalogEntry(
        skill_id="skill-b",
        revision_id="skill-b-r1",
        name="数据 summary",
        description="",
        category="other",
    ),
)


def test_tapper_default_turn_accepts_manual_skills():
    """Tapper default (no Agent) loads manual Skills too: the frozen Skill
    content no longer requires a frozen Agent authority."""

    turn_input = TurnInput(
        message="Use my Skill",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        skill_revision_ids=("skill-1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        skill_instruction_templates=("manual skill text",),
        skill_instruction_template_digests=(_sha("manual skill text"),),
        requested_skill_revision_ids=("skill-1",),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )

    assert turn_input.agent_revision_id is None
    assert turn_input.skill_instruction_templates == ("manual skill text",)


def test_empty_catalog_keeps_legacy_digest():
    base_kwargs = dict(
        message="Legacy digest",
        actor_id="tapper-local-user",
        identity_mode="validation",
        model_alias="qwen-plus",
        retrieval_policy_digest="sha256:" + "c" * 64,
    )
    legacy = TurnInput(**base_kwargs)
    material = legacy.material(project_id="p", turn_id="t")

    assert "skill_catalog" not in material
    # Recorded from this exact input before `skill_catalog` existed.
    assert content_digest(material) == (
        "sha256:9032d3453fb4de7ffe659dd7d5a4fa6c9a0cfd507d9b34d7c3a105b19a8f54e2"
    )

    with_catalog = TurnInput(**base_kwargs, skill_catalog=_CATALOG)
    catalog_material = with_catalog.material(project_id="p", turn_id="t")
    assert catalog_material["skill_catalog"] == [
        {
            "skill_id": "skill-a",
            "revision_id": "skill-a-r2",
            "name": "Boundary checks",
            "description": "Find boundary values",
            "category": "test-design",
        },
        {
            "skill_id": "skill-b",
            "revision_id": "skill-b-r1",
            "name": "数据 summary",
            "description": "",
            "category": "other",
        },
    ]
    assert set(catalog_material) - {"skill_catalog"} == set(material)
    assert content_digest(catalog_material) != content_digest(material)


def test_catalog_round_trips_through_input_json():
    turn_input = TurnInput(
        message="Freeze a catalog",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        skill_catalog=_CATALOG,
        retrieval_policy_digest="sha256:" + "c" * 64,
    )

    raw = _input_json(turn_input)

    assert raw["skill_catalog"] == [
        {
            "skill_id": entry.skill_id,
            "revision_id": entry.revision_id,
            "name": entry.name,
            "description": entry.description,
            "category": entry.category,
        }
        for entry in _CATALOG
    ]
    reconstructed = _reconstruct(raw)
    assert reconstructed == turn_input
    assert reconstructed.material(project_id="p", turn_id="t") == turn_input.material(
        project_id="p", turn_id="t"
    )
