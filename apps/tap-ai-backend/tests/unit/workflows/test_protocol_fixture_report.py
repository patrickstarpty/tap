"""Report semantics only; no provider, server, browser, or fixture execution."""

import ast
from pathlib import Path

import pytest


def result_check(action, observation):
    script = Path(__file__).resolve().parents[5] / "scripts/verify-opencode-protocol.py"
    tree = ast.parse(script.read_text())
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "fixture_result_check"
    )
    namespace = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(script), "exec"), namespace)
    return namespace["fixture_result_check"](action, observation)


@pytest.mark.parametrize("value", ["CLICKED", "STARTED", "Ada"])
def test_fixture_readback_uses_each_scenarios_actual_expected_value(value):
    assert result_check({"equals": value}, {"extracted": {"value": value}}) == {
        "expected_result": value,
        "result_verified": True,
    }


def test_wrong_or_absent_result_is_not_hidden_as_success():
    assert (
        result_check({"equals": "STARTED"}, {"extracted": {"value": "WRONG"}})["result_verified"]
        is False
    )
    assert result_check({"equals": "STARTED"}, {})["result_verified"] is False
    assert result_check({}, {})["result_verified"] is None
