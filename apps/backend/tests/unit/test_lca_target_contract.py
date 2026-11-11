"""Keep deterministic replay target guard semantics aligned with AA."""

from pathlib import Path
import pytest
from tap_platform.lca.generator import TARGET_CONTRACT_HELPER, ASSOCIATED_CONTROL_HELPER
from tap_platform.lca.source_acceptance import (
    ReviewedAcceptanceContract,
    TargetContract,
)


def test_generated_guard_matches_the_authoritative_aa_guard_body():
    root = Path(__file__).resolve().parents[4]
    body = (
        (root / "deploy/browser/page-actions.mjs")
        .read_text()
        .split("export async function guardTarget(page, action) {", 1)[1]
        .split("\n}\n", 1)[0]
    )
    expected = (
        "  const assertTargetContract = async (locator, contract, timeout) => {"
        + body
        + "\n  };"
    )
    expected = (
        expected.replace("page.locator(action.selector)", "locator")
        .replace("action.", "contract.")
        .replace("{timeout:30000}", "{timeout}")
    )
    assert TARGET_CONTRACT_HELPER.strip("\n") == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"target_texts": []},
        {"target_texts": ["  "]},
        {"target_texts": ["Age"], "scope_text": " "},
        {"target_texts": ["Age"], "extra": True},
    ],
)
def test_target_guard_is_bounded_and_strict(bad):
    with pytest.raises(ValueError):
        TargetContract.model_validate(bad)


@pytest.mark.parametrize(
    "change", [{"reviewed": 1}, {"version": True}, {"fields": []}, {"unexpected": True}]
)
def test_original_review_contract_rejects_wire_type_coercion(change):
    value = {
        "version": 1,
        "reviewed": True,
        "fields": [
            {
                "id": "age",
                "value": "30",
                "readback_value": "30",
                "attribute": "value",
                "target_texts": ["Age"],
            }
        ],
    }
    with pytest.raises(ValueError):
        ReviewedAcceptanceContract.model_validate({**value, **change})


def test_reviewed_observed_state_supports_exact_empty_data_active():
    from tap_platform.lca.source_acceptance import ReviewedAcceptanceContract

    contract = ReviewedAcceptanceContract.model_validate(
        {
            "version": 1,
            "reviewed": True,
            "fields": [
                {
                    "id": "frequency",
                    "value": "Monthly",
                    "readback_value": "",
                    "attribute": "data-active",
                    "target_texts": ["Monthly"],
                }
            ],
        }
    )
    assert contract.fields[0].readback_value == ""


def test_associated_control_helper_matches_authoritative_dom_collector():
    root = Path(__file__).resolve().parents[4]
    source = (root / "deploy/browser/page-actions.mjs").read_text()
    expected = (
        "function associatedControlState"
        + source.split("export function associatedControlState", 1)[1].split(
            "\nexport async function readSelectedLabel", 1
        )[0]
    )
    assert ASSOCIATED_CONTROL_HELPER.strip() == expected.strip()


def test_associated_selection_assertion_requires_exact_equals():
    from tap_platform.lca.definition import check_read_arguments

    check_read_arguments({"attribute": "selected_label", "equals": "Female"})
    with pytest.raises(ValueError, match="exact equals"):
        check_read_arguments({"attribute": "selected_label", "nonempty": True})
