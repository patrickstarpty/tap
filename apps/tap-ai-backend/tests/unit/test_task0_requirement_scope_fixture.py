import json
from pathlib import Path

FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "quality" / "requirements" / "trusted-checkout-v1.json"
)


def test_synthetic_requirement_scope_is_explicit_and_covers_trust_boundaries():
    scope = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert scope["schemaVersion"] == "requirement-scope-snapshot-v1"
    assert scope["evidenceKind"] == "synthetic-development-fixture"
    assert scope["realGateStatus"] == "pending-real-input"
    assert scope["contractVersion"] == "trusted-knowledge-inputs-v1"

    principals = scope["approvalRoleMatrix"]
    assert principals["editorId"] != principals["independentReviewerId"]
    assert principals["independentReviewerId"] != principals["publisherId"]
    assert all(value.startswith("synthetic-") for value in principals.values())

    requirements = scope["requirements"]
    assert {item["caseKind"] for item in requirements} == {
        "normal",
        "boundary",
        "exception",
        "conflict",
        "no-answer",
        "forbidden",
    }
    assert len({item["requirementId"] for item in requirements}) == len(requirements)
    assert all(item["sourceRevisionId"] for item in requirements)
    assert all(item["locator"] for item in requirements)
