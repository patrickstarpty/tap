from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "evaluate-quality-kb.py"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "quality" / "kb"
APPROVED_MAPPING = (
    "approved-provider",
    "approved-model-v1",
    "sha256:" + "6" * 64,
)


def _evaluator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_quality_kb", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _passing_profile() -> dict[str, object]:
    return {
        "schemaVersion": "quality-kb-profile-v1",
        "profileId": "QUALITY-KB-01",
        "dataset": {
            "version": "human-labeled-unit-v1",
            "labelingMethod": "human",
            "provenance": "review-record:unit-v1",
        },
        "bindings": {
            "policyDigest": "sha256:" + "1" * 64,
            "modelAlias": "tapper-chat",
            "actualProvider": "approved-provider",
            "actualModel": "approved-model-v1",
            "actualModelApproval": "approved",
            "actualModelApprovalDigest": "sha256:" + "6" * 64,
            "promptDigest": "sha256:" + "2" * 64,
            "agentRevisionDigest": "sha256:" + "3" * 64,
            "skillRevisionDigests": ["sha256:" + "4" * 64],
            "schemaDigest": "sha256:" + "5" * 64,
        },
        "execution": {
            "providerCallBudget": 4,
            "providerCalls": 2,
            "cacheHits": 0,
            "retryCount": 0,
            "maxRetriesPerCase": 1,
        },
        "cases": [
            {
                "caseId": "answerable-a",
                "caseType": "answerable",
                "question": "Who approves the request?",
                "projectId": "project-a",
                "selectedSourceIds": ["source-a"],
                "expectedRelevantChunkIds": ["chunk-a"],
                "shouldAbstain": False,
                "labelProvenance": "review-record:unit-v1#answerable-a",
                "observed": {
                    "actualProvider": "approved-provider",
                    "actualModel": "approved-model-v1",
                    "retrieved": [
                        {
                            "rank": 1,
                            "chunkId": "chunk-a",
                            "sourceId": "source-a",
                            "projectId": "project-a",
                        }
                    ],
                    "abstained": False,
                    "citations": [
                        {
                            "citationId": "citation-a",
                            "sourceId": "source-a",
                            "projectId": "project-a",
                            "anchorResolvable": True,
                        }
                    ],
                    "claims": [
                        {
                            "claimId": "claim-a",
                            "citationIds": ["citation-a"],
                            "humanSupportedCitationIds": ["citation-a"],
                        }
                    ],
                },
            },
            {
                "caseId": "abstain-a",
                "caseType": "abstain",
                "question": "What is not in the selected source?",
                "projectId": "project-a",
                "selectedSourceIds": ["source-a"],
                "expectedRelevantChunkIds": [],
                "shouldAbstain": True,
                "labelProvenance": "review-record:unit-v1#abstain-a",
                "observed": {
                    "actualProvider": "approved-provider",
                    "actualModel": "approved-model-v1",
                    "retrieved": [],
                    "abstained": True,
                    "citations": [],
                    "claims": [],
                },
            },
        ],
    }


def test_cross_source_fixture_fails_with_explicit_leakage_evidence(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(FIXTURES / "failing-cross-source-v1.json"),
            "--report",
            str(report),
            "--min-cases",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "leakage=2 (required 0)" in completed.stderr
    payload = json.loads(report.read_text())
    assert payload["status"] == "fail"
    assert payload["metrics"]["leakageCount"] == 2
    assert payload["cases"][0]["leakage"] == [
        "citation:citation-foreign:unselected-source:source-b",
        "retrieval:chunk-foreign:unselected-source:source-b",
    ]


def test_report_is_deterministic_and_binds_all_governance_digests() -> None:
    module = _evaluator()
    first = module.evaluate_profile(
        _passing_profile(), min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )
    second = module.evaluate_profile(
        _passing_profile(), min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )

    assert first == second
    assert first["status"] == "pass"
    assert first["datasetDigest"].startswith("sha256:")
    assert first["evaluatorDigest"].startswith("sha256:")
    assert first["bindings"] == {
        "policyDigest": "sha256:" + "1" * 64,
        "modelAlias": "tapper-chat",
        "actualProvider": "approved-provider",
        "actualModel": "approved-model-v1",
        "actualModelApproval": "approved",
        "actualModelApprovalDigest": "sha256:" + "6" * 64,
        "promptDigest": "sha256:" + "2" * 64,
        "agentRevisionDigest": "sha256:" + "3" * 64,
        "skillRevisionDigests": ["sha256:" + "4" * 64],
        "schemaDigest": "sha256:" + "5" * 64,
    }


def test_thresholds_and_each_case_evidence_are_reported_without_rounding() -> None:
    module = _evaluator()
    profile = _passing_profile()
    cases = profile["cases"]
    assert isinstance(cases, list)
    cases[0]["observed"]["retrieved"] = []
    cases[0]["observed"]["citations"][0]["anchorResolvable"] = False
    cases[0]["observed"]["claims"][0]["humanSupportedCitationIds"] = []
    cases[1]["observed"]["abstained"] = False

    report = module.evaluate_profile(
        profile, min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )

    assert report["status"] == "fail"
    assert report["metrics"] == {
        "actualCaseCount": 2,
        "skippedCaseCount": 0,
        "leakageCount": 0,
        "anchorResolved": 0,
        "anchorTotal": 1,
        "groundedSupported": 0,
        "groundedTotal": 1,
        "retrievalRelevantAt10": 0,
        "retrievalRelevantTotal": 1,
        "abstainCorrect": 0,
        "abstainTotal": 1,
    }
    assert report["thresholds"]["anchorResolution"]["actual"] == "0/1"
    assert report["thresholds"]["groundedClaimCitationPrecision"]["actual"] == "0/1"
    assert report["thresholds"]["retrievalRecallAt10"]["actual"] == "0/1"
    assert report["thresholds"]["abstainAccuracy"]["actual"] == "0/1"
    answerable = next(item for item in report["cases"] if item["caseId"] == "answerable-a")
    assert answerable["missedRelevantChunkIds"] == ["chunk-a"]


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        (("dataset", "labelingMethod", "model"), "dataset must be human-labeled"),
        (("bindings", "actualModelApproval", "unapproved"), "actual model is not approved"),
        (("bindings", "actualProvider", ""), "actual provider/model binding is missing"),
        (("bindings", "actualModel", "tapper-chat"), "actual model cannot equal logical alias"),
    ],
)
def test_real_gate_rejects_untruthful_dataset_or_model_binding(
    mutation: tuple[str, str, str], expected_failure: str
) -> None:
    module = _evaluator()
    profile = _passing_profile()
    section, key, value = mutation
    profile[section][key] = value

    report = module.evaluate_profile(
        profile, min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )

    assert report["status"] == "fail"
    assert expected_failure in report["failures"]


def test_real_cli_requires_explicit_opt_in_before_reading_a_profile(tmp_path: Path) -> None:
    absent = tmp_path / "absent.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(absent), "--require-real"],
        check=False,
        capture_output=True,
        text=True,
        env={},
    )

    assert completed.returncode == 2
    assert completed.stderr.strip() == "quality-kb-real requires TAP_RUN_QUALITY_KB_01=1"


def test_real_cli_requires_external_approved_model_mapping_before_profile(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "absent.json"), "--require-real"],
        check=False,
        capture_output=True,
        text=True,
        env={"TAP_RUN_QUALITY_KB_01": "1"},
    )

    assert completed.returncode == 2
    assert completed.stderr.strip() == (
        "quality-kb-real requires an explicit approved actual provider/model mapping"
    )


def test_dataset_digest_changes_when_a_label_changes() -> None:
    module = _evaluator()
    original = _passing_profile()
    changed = _passing_profile()
    changed["cases"][0]["shouldAbstain"] = True

    first = module.evaluate_profile(
        original, min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )
    second = module.evaluate_profile(
        changed, min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )

    assert first["datasetDigest"] != second["datasetDigest"]


def test_real_gate_binds_response_identity_and_bounded_provider_execution() -> None:
    module = _evaluator()
    wrong_identity = _passing_profile()
    wrong_identity["cases"][0]["observed"]["actualModel"] = "different-model"
    over_budget = _passing_profile()
    over_budget["execution"]["providerCalls"] = 5

    identity_report = module.evaluate_profile(
        wrong_identity, min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )
    budget_report = module.evaluate_profile(
        over_budget, min_cases=2, require_real=True, approved_mapping=APPROVED_MAPPING
    )

    assert identity_report["status"] == "fail"
    answerable = next(item for item in identity_report["cases"] if item["caseId"] == "answerable-a")
    assert answerable["modelIdentityMatchesBinding"] is False
    assert (
        "case answerable-a actual response model identity mismatches binding"
        in identity_report["failures"]
    )
    assert budget_report["status"] == "fail"
    assert "provider call count exceeds bounded budget" in budget_report["failures"]
    assert budget_report["execution"] == {
        "providerCallBudget": 4,
        "providerCalls": 5,
        "cacheHits": 0,
        "retryCount": 0,
        "maxRetriesPerCase": 1,
    }


def test_offline_evaluation_still_rejects_missing_real_run_provenance() -> None:
    module = _evaluator()
    profile = _passing_profile()
    profile["bindings"]["actualModelApprovalDigest"] = None
    profile["cases"][0]["observed"]["actualProvider"] = "different-provider"

    report = module.evaluate_profile(profile, min_cases=2, require_real=False)

    assert report["status"] == "fail"
    assert "actual model approval digest is missing" in report["failures"]
    assert (
        "case answerable-a actual response model identity mismatches binding" in report["failures"]
    )


def test_full_gate_requires_all_representative_question_types() -> None:
    module = _evaluator()

    report = module.evaluate_profile(
        _passing_profile(), min_cases=100, require_real=True, approved_mapping=APPROVED_MAPPING
    )

    assert (
        "dataset is missing representative case types: conflict, unauthorized" in report["failures"]
    )


def test_real_evaluator_requires_and_compares_external_approval_authority() -> None:
    module = _evaluator()
    missing = module.evaluate_profile(_passing_profile(), min_cases=2, require_real=True)
    mismatched = module.evaluate_profile(
        _passing_profile(),
        min_cases=2,
        require_real=True,
        approved_mapping=("approved-provider", "other-model", "sha256:" + "6" * 64),
    )

    assert "external approved model mapping is required" in missing["failures"]
    assert missing["externalApprovalMatched"] is None
    assert (
        "profile model identity does not match the external approved mapping"
        in mismatched["failures"]
    )
    assert mismatched["externalApprovalMatched"] is False


def test_claim_citation_precision_is_judged_per_relationship() -> None:
    module = _evaluator()
    profile = _passing_profile()
    observed = profile["cases"][0]["observed"]
    observed["citations"].append(
        {
            "citationId": "citation-b",
            "sourceId": "source-a",
            "projectId": "project-a",
            "anchorResolvable": True,
        }
    )
    observed["claims"][0]["citationIds"].append("citation-b")

    report = module.evaluate_profile(profile, min_cases=2)

    assert report["metrics"]["groundedSupported"] == 1
    assert report["metrics"]["groundedTotal"] == 2
    assert report["status"] == "fail"


def test_answerable_case_cannot_pass_by_abstaining_without_claims() -> None:
    module = _evaluator()
    profile = _passing_profile()
    observed = profile["cases"][0]["observed"]
    observed["abstained"] = True
    observed["citations"] = []
    observed["claims"] = []

    report = module.evaluate_profile(profile, min_cases=2)

    assert report["status"] == "fail"
    assert "case answerable-a did not produce a grounded answer" in report["failures"]
    answerable = next(item for item in report["cases"] if item["caseId"] == "answerable-a")
    assert answerable["responseShapeValid"] is False


def test_real_cli_rejects_a_pytest_run_with_skips(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(_passing_profile()))
    pytest_report = tmp_path / "pytest.xml"
    pytest_report.write_text(
        '<testsuites tests="1" failures="0" errors="0" skipped="1"></testsuites>'
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(profile),
            "--min-cases",
            "2",
            "--require-real",
            "--pytest-report",
            str(pytest_report),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "TAP_RUN_QUALITY_KB_01": "1",
            "TAP_QUALITY_KB_APPROVED_PROVIDER": "approved-provider",
            "TAP_QUALITY_KB_APPROVED_MODEL": "approved-model-v1",
            "TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST": "sha256:" + "6" * 64,
        },
    )

    assert completed.returncode == 2
    assert (
        "quality-kb pytest evidence must have tests and zero failures/errors/skips"
        in completed.stderr
    )


@pytest.mark.skipif(
    os.environ.get("TAP_RUN_QUALITY_KB_01") != "1",
    reason="QUALITY-KB-01 captured real-response gate requires explicit opt-in",
)
def test_captured_real_response_profile_meets_quality_kb_01() -> None:
    module = _evaluator()
    profile_path = Path(
        os.environ.get(
            "TAP_QUALITY_KB_PROFILE",
            FIXTURES / "profile-v1.json",
        )
    )
    report_path = Path(os.environ.get("TAP_QUALITY_KB_REPORT", ".local/quality-kb/report.json"))
    approved_mapping = (
        os.environ["TAP_QUALITY_KB_APPROVED_PROVIDER"],
        os.environ["TAP_QUALITY_KB_APPROVED_MODEL"],
        os.environ["TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST"],
    )
    report = module.evaluate_profile(
        module._load_json(profile_path),
        min_cases=100,
        require_real=True,
        approved_mapping=approved_mapping,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    assert report["metrics"]["actualCaseCount"] >= 100, report["failures"]
    assert report["metrics"]["skippedCaseCount"] == 0, report["failures"]
    assert report["status"] == "pass", report["failures"]
