import json
from copy import deepcopy

import pytest

from tap_platform.lca.definition import (
    canonical_bytes,
    resolve_parameters,
    validate_definition,
)


def definition() -> dict:
    return {
        "schema_version": "lca-debug-v1",
        "allowed_origins": ["https://fixture.example"],
        "targets": [{"id": "field", "kind": "test_id", "value": "name"}],
        "parameters": [{"name": "name", "type": "string", "required": True}],
        "steps": [
            {
                "id": "open",
                "operation": "navigate",
                "arguments": {"url": "https://fixture.example/form"},
                "timeout_ms": 1000,
            },
            {
                "id": "fill",
                "operation": "fill",
                "target_id": "field",
                "arguments": {"value": {"parameter": "name"}},
                "timeout_ms": 1000,
            },
            {
                "id": "select",
                "operation": "select",
                "target_id": "field",
                "arguments": {"value": "a"},
                "timeout_ms": 1000,
            },
            {
                "id": "click",
                "operation": "click",
                "target_id": "field",
                "arguments": {},
                "timeout_ms": 1000,
            },
            {
                "id": "text",
                "operation": "assert_text",
                "target_id": "field",
                "arguments": {"text": "Done"},
                "timeout_ms": 1000,
            },
            {
                "id": "url",
                "operation": "assert_url",
                "arguments": {"url": "https://fixture.example/done"},
                "timeout_ms": 1000,
            },
        ],
    }


def test_canonical_and_exact_parameter_resolution() -> None:
    raw = definition()
    parsed = validate_definition(raw)
    assert resolve_parameters(parsed, {"name": "${literal}"}) == {"name": "${literal}"}
    assert canonical_bytes(parsed) == canonical_bytes(
        validate_definition(deepcopy(raw))
    )
    raw["steps"][0]["id"] = "changed"
    assert parsed.steps[0].id == "open"


@pytest.mark.parametrize("value", [True, 1.5, "100", 0, -1, 30001])
def test_timeout_is_bounded_strict_integer(value: object) -> None:
    raw = definition()
    raw["steps"][0]["timeout_ms"] = value
    with pytest.raises(ValueError):
        validate_definition(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["steps"][1].update(id="open"),
        lambda d: d["steps"][0].update(operation="evaluate"),
        lambda d: d["steps"][1].update(target_id="missing"),
        lambda d: d["steps"][0].update(target_id="field"),
        lambda d: d["steps"][1].update(arguments={"value": 42}),
        lambda d: d["steps"][1].update(arguments={"value": {"parameter": "missing"}}),
        lambda d: d["steps"][1].update(
            arguments={"value": {"parameter": "name", "fallback": "x"}}
        ),
        lambda d: d["steps"][0].update(arguments={"url": "https://evil.example/"}),
        lambda d: d["steps"][0].update(arguments={"url": "javascript:alert(1)"}),
        lambda d: d.update(allowed_origins=["https://fixture.example/path"]),
        lambda d: d.update(allowed_origins=["https://user:pass@fixture.example"]),
        lambda d: d.update(steps=d["steps"] * 100),
        lambda d: d["targets"][0].update(kind="css", value="#name"),
        lambda d: d["parameters"][0].update(secret=True),
        lambda d: d["parameters"][0].update(type="integer"),
        lambda d: d.update(source="evil"),
    ],
)
def test_invalid_definitions_rejected(mutation) -> None:
    raw = definition()
    mutation(raw)
    with pytest.raises(ValueError):
        validate_definition(raw)


@pytest.mark.parametrize("inputs", [{}, {"name": 1}, {"name": "ok", "other": "x"}])
def test_invalid_inputs_rejected(inputs: dict) -> None:
    with pytest.raises(ValueError):
        resolve_parameters(validate_definition(definition()), inputs)


def test_typed_defaults_and_reference_origin_checked_at_resolution() -> None:
    raw = definition()
    raw["parameters"] += [
        {"name": "count", "type": "integer", "required": False, "default": 2}
    ]
    parsed = validate_definition(raw)
    assert resolve_parameters(parsed, {"name": "ok"}) == {"name": "ok", "count": 2}
    with pytest.raises(ValueError):
        resolve_parameters(parsed, {"name": "ok", "count": True})
    raw["steps"][0]["arguments"]["url"] = {"parameter": "name"}
    with pytest.raises(ValueError):
        resolve_parameters(validate_definition(raw), {"name": "https://evil.example/"})


@pytest.mark.parametrize(
    "kind,value,name,review",
    [("role", "notarole", "Submit", False), ("css", "#x >> text=y", None, True)],
)
def test_unsupported_locator_syntax_rejected(kind, value, name, review) -> None:
    raw = definition()
    raw["targets"] = [
        {
            "id": "field",
            "kind": kind,
            "value": value,
            "name": name,
            "review_required": review,
        }
    ]
    with pytest.raises(ValueError):
        validate_definition(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_numeric_default_must_be_finite_json_number(value) -> None:
    raw = definition()
    raw["parameters"].append(
        {"name": "count", "type": "number", "required": False, "default": value}
    )
    with pytest.raises(ValueError):
        validate_definition(raw)


def test_port_zero_is_not_equivalent_to_absent_port() -> None:
    raw = definition()
    raw["allowed_origins"] = ["http://fixture.example"]
    raw["steps"][0]["arguments"]["url"] = "http://fixture.example:0/form"
    raw["steps"] = raw["steps"][:1]
    with pytest.raises(ValueError):
        validate_definition(raw)


def test_origin_limit_matches_browser_execution_boundary() -> None:
    raw = definition()
    raw["allowed_origins"] += [f"https://fixture{i}.example" for i in range(8)]
    with pytest.raises(ValueError):
        validate_definition(raw)


def grouped_definition() -> dict:
    raw = definition()
    raw["bdd_steps"] = [
        {
            "id": "given_page",
            "keyword": "Given",
            "description": "The form is open",
            "action_ids": ["open"],
        },
        {
            "id": "when_submit",
            "keyword": "When",
            "description": "Submit the form",
            "action_ids": ["fill", "select", "click"],
        },
        {
            "id": "then_done",
            "keyword": "Then",
            "description": "Completion is visible",
            "action_ids": ["text", "url"],
        },
        {
            "id": "and_more",
            "keyword": "And",
            "description": "Next authoring container",
            "action_ids": [],
        },
        {
            "id": "but_not",
            "keyword": "But",
            "description": "An exception",
            "action_ids": [],
        },
    ]
    return raw


def test_legacy_canonical_bytes_remain_exactly_unchanged() -> None:
    raw = definition()
    expected = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    assert canonical_bytes(validate_definition(raw)) == expected
    assert "bdd_steps" not in json.loads(expected)


def test_bdd_groups_round_trip_with_empty_authoring_containers() -> None:
    raw = grouped_definition()
    parsed = validate_definition(raw)
    assert json.loads(canonical_bytes(parsed)) == raw
    assert [action for group in parsed.bdd_steps for action in group.action_ids] == [
        step.id for step in parsed.steps
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(bdd_steps=[]),
        lambda d: d.update(bdd_steps=None),
        lambda d: d["bdd_steps"][1].update(id="given_page"),
        lambda d: d["bdd_steps"][0].update(keyword="given"),
        lambda d: d["bdd_steps"][0].update(description=""),
        lambda d: d["bdd_steps"][0].update(description="  \n"),
        lambda d: d["bdd_steps"][0].update(description="x" * 1025),
        lambda d: d["bdd_steps"][0].update(action_ids=[]),
        lambda d: d["bdd_steps"][0].update(action_ids=["missing"]),
        lambda d: d["bdd_steps"][1].update(
            action_ids=["fill", "fill", "select", "click"]
        ),
        lambda d: d["bdd_steps"][1].update(action_ids=["select", "fill", "click"]),
        lambda d: d["bdd_steps"].reverse(),
        lambda d: d["bdd_steps"][0].update(operation="navigate"),
        lambda d: d["bdd_steps"].extend(
            {
                "id": f"group_{i}",
                "keyword": "And",
                "description": "More",
                "action_ids": [],
            }
            for i in range(100)
        ),
    ],
)
def test_invalid_bdd_grouping_is_rejected(mutation) -> None:
    raw = grouped_definition()
    mutation(raw)
    with pytest.raises(ValueError):
        validate_definition(raw)
