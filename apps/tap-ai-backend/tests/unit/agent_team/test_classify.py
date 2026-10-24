"""Unit tests for `classify_revision` (Task 2, 2026-10-23 agent team view)."""

from __future__ import annotations

import json

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.agent_team.domain.classify import classify_revision
from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
from tap.modules.ai.domain.assets import AiAgentRevision, text_digest
from tap.modules.ai.domain.models import schema_digest

_OUTPUT_SCHEMA_JSON = json.dumps(VALIDATION_OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":"))
_OUTPUT_SCHEMA_DIGEST = schema_digest(VALIDATION_OUTPUT_SCHEMA)


def _revision(**overrides) -> AiAgentRevision:
    fields = {
        "revision_id": "agent-rev-1",
        "asset_id": "agent-1",
        "display_name": "demo-agent",
        "scope": VALIDATION_SCOPE,
        "content_digest": text_digest("demo-agent\0a demo agent"),
        "system_instruction_digest": text_digest("Answer using approved knowledge."),
        "tool_allowlist": frozenset({"knowledge.search", "knowledge.answer"}),
        "output_schema_digest": _OUTPUT_SCHEMA_DIGEST,
        "system_instruction": "Answer using approved knowledge.",
        "output_schema_json": _OUTPUT_SCHEMA_JSON,
        "description": "a demo agent",
        "source_kind": "authored",
        "skill_revision_ids": (),
        "default_model_alias": None,
    }
    fields.update(overrides)
    return AiAgentRevision(**fields)


def test_only_newer_revisions_of_same_skills_is_skill_upgraded():
    previous = _revision(skill_revision_ids=("s1r1",))
    current = _revision(revision_id="agent-rev-2", skill_revision_ids=("s1r2",))
    skill_of = {"s1r1": "skill-1", "s1r2": "skill-1"}

    assert classify_revision(previous, current, skill_of) == "skill_upgraded"


def test_prompt_change_with_skill_upgrade_is_revised():
    previous = _revision(skill_revision_ids=("s1r1",))
    current = _revision(
        revision_id="agent-rev-2",
        skill_revision_ids=("s1r2",),
        system_instruction="Answer differently now.",
        system_instruction_digest=text_digest("Answer differently now."),
    )
    skill_of = {"s1r1": "skill-1", "s1r2": "skill-1"}

    assert classify_revision(previous, current, skill_of) == "revised"


def test_adding_a_skill_is_revised():
    previous = _revision(skill_revision_ids=("s1r1",))
    current = _revision(revision_id="agent-rev-2", skill_revision_ids=("s1r1", "s2r1"))
    skill_of = {"s1r1": "skill-1", "s2r1": "skill-2"}

    assert classify_revision(previous, current, skill_of) == "revised"


def test_model_change_is_revised():
    previous = _revision(skill_revision_ids=("s1r1",), default_model_alias="model-a")
    current = _revision(
        revision_id="agent-rev-2",
        skill_revision_ids=("s1r2",),
        default_model_alias="model-b",
    )
    skill_of = {"s1r1": "skill-1", "s1r2": "skill-1"}

    assert classify_revision(previous, current, skill_of) == "revised"


def test_no_skill_change_at_all_is_revised():
    previous = _revision(skill_revision_ids=("s1r1",))
    current = _revision(revision_id="agent-rev-2", skill_revision_ids=("s1r1",))
    skill_of = {"s1r1": "skill-1"}

    assert classify_revision(previous, current, skill_of) == "revised"


def test_unresolvable_skill_ids_are_revised_not_upgraded():
    previous = _revision(skill_revision_ids=("gone-1",))
    current = _revision(revision_id="agent-rev-2", skill_revision_ids=("gone-2",))

    assert classify_revision(previous, current, {}) == "revised"
    assert classify_revision(previous, current, {"gone-1": "skill-1"}) == "revised"
    assert classify_revision(previous, current, {"gone-2": "skill-1"}) == "revised"
