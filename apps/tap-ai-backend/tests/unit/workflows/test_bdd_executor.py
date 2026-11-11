from copy import deepcopy

import pytest
from pydantic import ValidationError

from tap.modules.workflows.application.bdd_executor import BDDExecutor, BDDPlan


def plan_data():
    return {
        "requirements": [
            {"id": "age", "kind": "input", "source": "age 30", "expected": "30"},
            {
                "id": "premium",
                "kind": "result",
                "source": "nonzero premium",
                "expected": "positive",
            },
        ],
        "steps": [
            {"id": "open", "keyword": "Given", "title": "Open quote", "operation": "navigate"},
            {
                "id": "set-age",
                "keyword": "When",
                "title": "Set age",
                "operation": "fill",
                "requirement": "age",
                "target_texts": ["Age"],
                "value": "30",
            },
            {
                "id": "check-age",
                "keyword": "Then",
                "title": "Check age",
                "operation": "assert",
                "requirement": "age",
                "target_texts": ["Age"],
                "attribute": "value",
                "assertion": {"kind": "equals", "expected": "30"},
            },
            {
                "id": "quote",
                "keyword": "Then",
                "title": "Read premium",
                "operation": "assert",
                "requirement": "premium",
                "target_texts": ["Premium"],
                "assertion": {"kind": "positive"},
            },
        ],
    }


def executor():
    return BDDExecutor(BDDPlan.model_validate(plan_data()), "https://example.com/quote")


def receipt(**observation):
    return {
        "status": "completed",
        "observation": {"url": "https://example.com/quote", **observation},
    }


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "unknown", "then_write", "weak", "value", "artifact"]
)
def test_plan_rejects_incomplete_or_inconsistent_contract(change):
    data = plan_data()
    if change == "missing":
        data["steps"].pop(2)
    elif change == "duplicate":
        copy = deepcopy(data["steps"][2])
        copy["id"] = "repeat-age"
        data["steps"].append(copy)
    elif change == "unknown":
        data["steps"][2]["requirement"] = "unknown"
    elif change == "then_write":
        data["steps"][2]["operation"] = "click"
    elif change == "weak":
        data["steps"][2]["assertion"] = {"kind": "nonempty"}
    elif change == "value":
        data["steps"][1]["value"] = "31"
    else:
        data["steps"][-1]["operation"] = "save_json"
    with pytest.raises((ValidationError, ValueError)):
        BDDPlan.model_validate(data)


def test_when_receipt_and_then_assertion_have_distinct_completion_rules():
    run = executor()
    run.accept(run.action(), receipt())
    assert run.current.id == "set-age"
    run.authorize("set-age", run.digest)
    action = run.action("#age")
    assert action["type"] == "fill" and action["value"] == "30"
    run.accept(action, receipt())
    assert run.current.id == "check-age" and run.outputs == {}
    check = run.action("#age")
    assert check["equals"] == "30" and check["name"] == "age"
    run.accept(check, receipt(extracted={"name": "age", "value": "30"}))
    run.accept(
        run.action("#premium"), receipt(extracted={"name": "premium", "value": "HK$12.50/month"})
    )
    assert run.current is None and run.outputs["premium"] == "HK$12.50/month"
    assert [a["step"] for a in run.actions] == ["open", "set-age", "check-age", "quote"]
    assert all(a["plan_digest"] == run.digest for a in run.actions)


def test_stale_or_future_decisions_and_fabricated_actions_are_rejected():
    run = executor()
    for step, digest in [("quote", run.digest), ("open", "old")]:
        with pytest.raises(ValueError):
            run.authorize(step, digest)
    action = run.action()
    action["url"] = "https://example.com/submit"
    with pytest.raises(ValueError, match="contract"):
        run.accept(action, receipt())
    assert run.current.id == "open"


def test_unknown_receipt_stops_without_repeating_the_write():
    run = executor()
    with pytest.raises(ValueError, match="unknown"):
        run.accept(run.action(), {"status": "unknown"})
    with pytest.raises(ValueError, match="stopped"):
        run.authorize("open", run.digest)
    assert run.actions == []


def test_failed_assertion_cannot_advance_even_if_receipt_claims_success():
    run = executor()
    run.accept(run.action(), receipt())
    run.accept(run.action("#age"), receipt())
    with pytest.raises(ValueError, match="assertion"):
        run.accept(run.action("#age"), receipt(extracted={"name": "age", "value": "31"}))
    assert run.current.id == "check-age" and run.outputs == {}


@pytest.mark.parametrize("value", ["0.00", "HK$0.00/month", "10% discount, HK$12.50", "NaN"])
def test_positive_result_requires_one_nonzero_number(value):
    run = executor()
    run.accept(run.action(), receipt())
    run.accept(run.action("#age"), receipt())
    run.accept(run.action("#age"), receipt(extracted={"name": "age", "value": "30"}))
    with pytest.raises(ValueError, match="assertion"):
        run.accept(run.action("#premium"), receipt(extracted={"name": "premium", "value": value}))


def test_no_progress_and_repeated_failure_have_separate_finite_limits():
    run = executor()
    for i in range(3):
        run.failed("same", "target mismatch")
    assert run.error and run.current.id == "open"
    other = executor()
    for i in range(other.max_observations):
        other.observe()
    with pytest.raises(ValueError, match="progress"):
        other.observe()


def test_compiler_rejects_unsupported_actions_and_preserves_frozen_guards():
    from tap.modules.workflows.application.direct_browser import DirectBrowserResult, compile_replay

    run = executor()
    run.accept(run.action(), receipt())
    run.accept(run.action("#age"), receipt())
    run.accept(run.action("#age"), receipt(extracted={"name": "age", "value": "30"}))
    run.accept(
        run.action("#premium"), receipt(extracted={"name": "premium", "value": "HK$12.50/month"})
    )
    result = DirectBrowserResult("passed", run.outputs, run.actions, run.display_steps())
    script = compile_replay(result)
    assert "guardTarget" in script and "target_texts" in script
    assert "positive" in script and run.digest in script
    assert "HK$12.50/month" not in script
    result.actions.append({"type": "unsupported", "step": "quote"})
    with pytest.raises(ValueError, match="Unsupported"):
        compile_replay(result)


@pytest.mark.asyncio
async def test_browser_use_script_generation_never_calls_a_model_or_browser():
    from tap.modules.workflows.adapters.browser_use_runtime import BrowserUseRuntime
    from tap.modules.workflows.adapters.opencode_runtime import CodingConfig
    from tap.modules.workflows.application.direct_browser import DirectBrowserResult

    run = executor()
    result = DirectBrowserResult(
        "passed",
        {"premium": "12.50"},
        [
            {
                "type": "read",
                "step": "quote",
                "selector": "#premium",
                "name": "premium",
                "attribute": "text",
                "positive": True,
                "target_texts": ["Premium"],
            },
        ],
        run.display_steps(),
    )
    runtime = BrowserUseRuntime(CodingConfig.for_test(), store=None, gateway_factory=None)
    script = await runtime._make_script(result, {})
    assert "positive" in script and "testInfo.attach" in script


def test_outbound_plan_schema_rejects_navigation_fields_before_model_execution():
    import jsonschema

    data = plan_data()
    data["steps"][0]["target_texts"] = ["Online Insurance"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, BDDPlan.model_json_schema())
    del data["steps"][0]["target_texts"]
    jsonschema.validate(data, BDDPlan.model_json_schema())
