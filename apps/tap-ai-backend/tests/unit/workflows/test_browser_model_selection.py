"""Nondefault paid model choices never inherit another model's cost assumptions."""

import importlib.util
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[5] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("model_selection_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_nondefault_alias_requires_explicit_limits_and_prices():
    with pytest.raises(ValueError, match="explicit"):
        runner().model_execution_settings("real", {"TAP_BROWSER_CODING_MODEL_ALIAS": "qwen-max"})


def test_real_max_uses_same_planning_and_execution_model_and_declared_rates():
    declared = {
        "TAP_BROWSER_CODING_MODEL_ALIAS": "qwen-max",
        "TAP_BROWSER_CODING_MAX_INPUT_TOKENS": "30720",
        "TAP_BROWSER_CODING_INPUT_PRICE_PER_MILLION": "1.6",
        "TAP_BROWSER_CODING_OUTPUT_PRICE_PER_MILLION": "6.4",
    }
    result = runner().model_execution_settings("real", declared)
    assert result == {**declared, "TAPPER_DEFAULT_CHAT_MODEL": "qwen-max"}


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0"])
def test_paid_model_prices_must_be_finite_positive(bad):
    with pytest.raises(ValueError, match="price"):
        runner().model_execution_settings(
            "real", {"TAP_BROWSER_CODING_INPUT_PRICE_PER_MILLION": bad}
        )


def test_default_and_synthetic_model_remain_compatible():
    for mode in ("real", "synthetic"):
        result = runner().model_execution_settings(mode, {})
        assert result["TAPPER_DEFAULT_CHAT_MODEL"] == "qwen-plus"
        assert result["TAP_BROWSER_CODING_MAX_INPUT_TOKENS"] == "129024"


def test_real_contract_file_is_bounded_and_strictly_user_reviewed(tmp_path):
    import json

    path = tmp_path / "contract.json"
    valid = {
        "version": 1,
        "reviewed": True,
        "fields": [
            {
                "id": "age",
                "value": "30",
                "readbackValue": "30",
                "attribute": "value",
                "targetTexts": ["Age"],
            }
        ],
    }
    path.write_text(json.dumps(valid))
    result = runner().load_reviewed_contract(path)
    assert result["fields"][0]["readbackValue"] == "30"
    assert result["reviewed"] is True
    valid["reviewed"] = "true"
    path.write_text(json.dumps(valid))
    with pytest.raises(ValueError):
        runner().load_reviewed_contract(path)
    path.write_text("x" * 32769)
    with pytest.raises(ValueError, match="32768"):
        runner().load_reviewed_contract(path)
