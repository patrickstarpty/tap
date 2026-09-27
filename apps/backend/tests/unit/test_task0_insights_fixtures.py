import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "insights"


def test_synthetic_report_set_is_traceable_and_covers_first_source_scenarios():
    fixture_set = json.loads(
        (FIXTURE_ROOT / "task0-inputs-v1.json").read_text(encoding="utf-8")
    )

    assert fixture_set["schemaVersion"] == "report-intake-development-fixtures-v1"
    assert fixture_set["evidenceKind"] == "synthetic-development-fixture"
    assert fixture_set["realGateStatus"] == "pending-real-input"
    assert fixture_set["contractVersion"] == "insights-intake-v1"
    assert {report["scenario"] for report in fixture_set["reports"]} == {
        "success",
        "failure",
        "retry",
        "parameterized",
        "partial",
    }

    source = fixture_set["source"]
    for report in fixture_set["reports"]:
        report_path = FIXTURE_ROOT / report["reportFile"]
        report_bytes = report_path.read_bytes()
        assert hashlib.sha256(report_bytes).hexdigest() == report["sha256"]
        assert ElementTree.fromstring(report_bytes).tag == "testsuite"
        assert report["manifest"]["projectId"] == source["projectId"]
        assert report["manifest"]["sourceId"] == source["sourceId"]

    partial = next(
        report for report in fixture_set["reports"] if report["scenario"] == "partial"
    )
    assert partial["manifest"]["completeness"] == "partial"
    assert partial["manifest"]["expectedShards"] == 2
    assert partial["manifest"]["receivedShards"] == ["1"]


def test_metric_oracle_keeps_first_final_and_recovery_denominators_distinct():
    oracle = json.loads(
        (FIXTURE_ROOT / "metrics-oracle-v1.json").read_text(encoding="utf-8")
    )
    instances = oracle["instances"]
    denominator = [
        item
        for item in instances
        if item["attempts"][0]["result"] in {"pass", "fail", "error"}
    ]
    initially_failed = [
        item
        for item in denominator
        if item["attempts"][0]["result"] in {"fail", "error"}
    ]
    recovered = [
        item for item in initially_failed if item["attempts"][-1]["result"] == "pass"
    ]

    assert oracle["expected"] == {
        "denominator": 3,
        "firstPassNumerator": 1,
        "finalPassNumerator": 2,
        "retryRecoveryNumerator": 1,
        "retryRecoveryDenominator": 2,
        "recoveryContributionNumerator": 1,
        "recoveryContributionDenominator": 3,
        "skippedCount": 1,
    }
    assert len(denominator) == oracle["expected"]["denominator"]
    assert sum(item["attempts"][0]["result"] == "pass" for item in denominator) == 1
    assert sum(item["attempts"][-1]["result"] == "pass" for item in denominator) == 2
    assert len(recovered) == 1
    assert len(initially_failed) == 2
