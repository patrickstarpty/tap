"""AA import is a review projection, never execution or script interpretation."""

from copy import deepcopy
import pytest
from tap_platform.lca.agent_projection import project_agent_actions
from tap_platform.lca.definition import validate_definition

SOURCE = {
    "task_id": "task",
    "execution_id": "execution",
    "bundle_id": "bundle",
    "input_sha256": "a" * 64,
}
PLAN = [
    {"id": "open", "keyword": "Given", "title": "Open product"},
    {"id": "choose", "keyword": "When", "title": "Choose product"},
    {"id": "check", "keyword": "Then", "title": "Check quote"},
]


def action(n, kind, **args):
    return {
        "id": f"action-{n}",
        "sequence": n,
        "actor_kind": "agent",
        "status": "completed",
        "request": {"type": kind, **args},
        "observation": {"sequence": n, "error": None, "fields": []},
    }


def example():
    return [
        action(1, "navigate", url="https://example.com/product"),
        action(2, "click", selector="#choose"),
        action(
            3, "read", selector="#quote", attribute="text", equals="Ready", name="quote"
        ),
    ]


def project(rows=None, **kw):
    return project_agent_actions(
        rows or example(),
        ["https://example.com"],
        PLAN,
        {"action-1": "open", "action-2": "choose", "action-3": "check"},
        SOURCE,
        **kw,
    )


def test_complete_projection_keeps_order_bdd_provenance_and_requires_review():
    result = project()
    assert result["complete"] is True and result["issues"] == []
    definition = validate_definition(result["definition"])
    assert [s.operation for s in definition.steps] == [
        "navigate",
        "click",
        "assert_read",
    ]
    assert [b.description for b in definition.bdd_steps] == [
        "Open product",
        "Choose product",
        "Check quote",
    ]
    assert definition.targets[0].review_required
    assert definition.steps[1].provenance["source_action_id"] == "action-2"
    assert result["source"] == SOURCE


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"status": "unknown"}, "unconfirmed_action"),
        (
            {"request": {"type": "click", "selector": "aria-ref=e3"}},
            "unstable_or_unsupported_locator",
        ),
        (
            {"request": {"type": "fill", "selector": "#name", "input_ref": "private"}},
            "protected_input",
        ),
        (
            {"request": {"type": "press", "selector": "#name", "key": "Enter"}},
            "unsupported_operation",
        ),
    ],
)
def test_incomplete_projection_never_returns_executable_definition(change, reason):
    rows = example()
    rows[1].update(change)
    result = project(rows)
    assert result["complete"] is False
    assert result["definition"] is None
    assert any(i["reason"] == reason for i in result["issues"])
    assert result["source"] == SOURCE


def test_custom_select_is_not_misrepresented_as_native_select():
    rows = example()
    rows[1]["request"] = {"type": "select", "selector": "#age", "value": "30"}
    rows[1]["observation"]["fields"] = [
        {
            "context": "action_target",
            "selector": "#age",
            "tag": "input",
            "readonly": True,
        }
    ]
    assert project(rows)["issues"][0]["reason"] == "custom_select_requires_review"


def test_disjoint_repeated_business_steps_keep_actual_action_order():
    rows = example()
    rows.insert(2, action(4, "click", selector="#another"))
    rows[3]["sequence"] = 5
    result = project_agent_actions(
        rows,
        ["https://example.com"],
        PLAN,
        {
            "action-1": "open",
            "action-2": "choose",
            "action-4": "open",
            "action-3": "check",
        },
        SOURCE,
    )
    assert result["complete"]
    assert [b["description"] for b in result["definition"]["bdd_steps"]] == [
        "Open product",
        "Choose product",
        "Open product",
        "Check quote",
    ]


def test_missing_bdd_or_duplicate_receipts_reject_instead_of_inventing_steps():
    rows = example()
    rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError):
        project(rows)
    assert (
        project_agent_actions(example(), ["https://example.com"], PLAN, {}, SOURCE)[
            "complete"
        ]
        is False
    )


def test_native_select_label_is_projected_to_observed_option_value():
    rows = example()
    rows[1]["request"] = {
        "type": "select",
        "selector": "#product",
        "value": "Product A",
    }
    rows[1]["observation"]["fields"] = [
        {
            "context": "action_target",
            "selector": "#product",
            "tag": "select",
            "value": "prod-a",
            "options": [{"label": "Product A", "value": "prod-a"}],
        }
    ]
    result = project(rows)
    assert result["complete"]
    assert result["definition"]["steps"][1]["arguments"] == {"value": "prod-a"}


def test_live_ref_requires_unique_server_observed_action_target_for_conversion():
    rows = example()
    rows[1]["request"]["selector"] = "aria-ref=e9"
    rows[1]["observation"]["fields"] = [
        {"context": "action_target", "selector": "#choose", "tag": "button"}
    ]
    result = project(rows)
    assert result["complete"]
    assert result["definition"]["targets"][0]["value"] == "#choose"
    assert result["definition"]["targets"][0]["review_required"]
    rows[1]["observation"]["fields"].append(
        {"context": "action_target", "selector": "#other", "tag": "button"}
    )
    assert not project(rows)["complete"]


@pytest.mark.parametrize("operation", ["fill", "select"])
def test_controlled_input_uses_only_confirmed_safe_receipt(operation):
    rows = example()
    rows[1]["request"] = {
        "type": operation,
        "selector": "aria-ref=e8",
        "input_ref": "vault-secret",
    }
    rows[1]["observation"]["extracted"] = {
        "control_receipt": {
            "pre_observation_sequence": 1,
            "target": {"kind": "test_id", "value": "product"},
            "args": {"value": "prod-a"},
        }
    }
    result = project(rows)
    assert result["complete"]
    assert result["definition"]["steps"][1]["arguments"] == {"value": "prod-a"}
    assert result["definition"]["targets"][0]["kind"] == "test_id"
    assert "vault-secret" not in str(result)
    rows[1]["observation"]["extracted"]["control_receipt"]["args"] = {}
    assert not project(rows)["complete"]


def test_malformed_control_receipt_blocks_import():
    rows = example()
    rows[1]["observation"]["extracted"] = {
        "control_receipt": {
            "pre_observation_sequence": 200,
            "target": {"kind": "test_id", "value": "x"},
            "args": {},
        }
    }
    assert project(rows)["issues"][0]["reason"] == "invalid_control_receipt"


def test_failed_observation_is_not_silently_dropped():
    rows = example()
    rows[1]["request"] = {"type": "snapshot"}
    rows[1]["status"] = "failed"
    assert project(rows)["issues"][0]["reason"] == "unconfirmed_action"


def test_read_constraints_and_named_output_remain_exact():
    rows = example()
    rows[2]["request"] = {
        "type": "read",
        "selector": "#quote",
        "attribute": "text",
        "name": "quote",
        "positive": True,
        "nonempty": True,
        "pattern": "^[0-9]+",
    }
    result = project(rows)
    assert result["complete"]
    assert result["definition"]["steps"][2]["arguments"] == {
        "attribute": "text",
        "name": "quote",
        "positive": True,
        "nonempty": True,
        "pattern": "^[0-9]+",
    }


def test_unsafe_read_regex_is_reported_without_weakening():
    rows = example()
    rows[2]["request"] = {
        "type": "read",
        "selector": "#quote",
        "attribute": "text",
        "name": "quote",
        "pattern": "(a+)+$",
    }
    result = project(rows)
    assert not result["complete"]
    assert result["definition"] is None
    assert result["issues"][0]["reason"] == "definition_requires_manual_review"
