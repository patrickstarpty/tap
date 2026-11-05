"""Structured-output profiles must change when the gateway changes what the
model is actually shown for a schema (the trusted schema instruction), so
fragments/drafts produced under the old instructions read as stale."""

import json

import pytest

import tap.modules.ai.domain.models as models


def _schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }


def test_schema_instruction_is_the_redaction_checked_compact_schema():
    instruction = models.structured_schema_instruction(_schema())
    assert instruction.startswith("\n")
    assert instruction.endswith("\n" + json.dumps(_schema(), sort_keys=True, separators=(",", ":")))


def test_profile_digest_binds_prompt_schema_and_gateway_instruction(monkeypatch):
    before = models.structured_profile_digest("prompt", _schema())
    assert before != models.text_digest("prompt\n" + models.schema_digest(_schema()))
    assert before != models.structured_profile_digest("other prompt", _schema())
    monkeypatch.setattr(
        models,
        "structured_schema_instruction",
        lambda schema: "\nchanged gateway wording\n" + json.dumps(schema),
    )
    assert models.structured_profile_digest("prompt", _schema()) != before


@pytest.mark.parametrize(
    ("module_name", "digest_name", "prompt_name", "schema_name"),
    [
        (
            "tap.modules.graph.adapters.model_gateway_extraction",
            "GRAPH_EXTRACTION_PROFILE_DIGEST",
            "GRAPH_EXTRACTION_PROMPT",
            "GRAPH_EXTRACTION_SCHEMA",
        ),
        (
            "tap.modules.test_management.adapters.model_gateway_generation",
            "TEST_DESIGN_PROFILE_DIGEST",
            "TEST_DESIGN_PROMPT",
            "TEST_DESIGN_SCHEMA",
        ),
    ],
)
def test_stale_checked_profiles_include_the_gateway_instruction(
    module_name, digest_name, prompt_name, schema_name
):
    module = __import__(module_name, fromlist=[digest_name])
    prompt, schema = getattr(module, prompt_name), getattr(module, schema_name)
    assert getattr(module, digest_name) == models.structured_profile_digest(prompt, schema)
    # The pre-gateway-instruction profile (prompt + schema only) is now stale.
    assert getattr(module, digest_name) != models.text_digest(
        prompt + "\n" + models.schema_digest(schema)
    )
