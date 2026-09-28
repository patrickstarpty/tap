"""Compatibility and safety checks for actual pytest XML and Allure results."""

import io
import json
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from tap_platform.insights.adapters.report_parser import parse_report, parser_version
from tap_platform.insights.adapters.junit import JUnitSecurityError
from tap_platform.insights.adapters.report_errors import ReportSecurityError
from tap_platform.insights.domain.reports import ReportManifest


def manifest(format="allure"):
    return ReportManifest(
        project_id="project-one",
        source_id="pytest",
        external_run_id="run-one",
        batch_id="batch-one",
        shard_id="1",
        expected_shards=1,
        contains_complete_attempts=True,
        application_commit="app",
        script_commit="tests",
        environment="qa",
        configuration="python",
        timezone="UTC",
        report_format=format,
    )


def archive(items):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        for name, value in items.items():
            output.writestr(
                name, json.dumps(value) if isinstance(value, dict) else value
            )
    return stream.getvalue()


def result(uuid="one", **changes):
    return dict(
        uuid=uuid,
        fullName="test_checkout#test_pay",
        historyId="history-card",
        testCaseId="test-pay",
        name="test_pay[card]",
        status="passed",
        start=1000,
        stop=1500,
        parameters=[{"name": "method", "value": "card"}],
        **changes,
    )


def test_actual_pytest_xml_without_custom_properties(tmp_path: Path):
    test = tmp_path / "test_example.py"
    test.write_text(
        "import pytest\n@pytest.mark.parametrize('value', [1,2])\ndef test_value(value):\n assert value > 0\n"
    )
    xml = tmp_path / "report.xml"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test), "--junitxml=" + str(xml)],
        cwd=tmp_path,
        capture_output=True,
    )
    assert run.returncode == 0
    facts = parse_report(xml.read_bytes(), manifest("pytest"))
    assert len(facts) == 2
    assert len({x.stable_test_id for x in facts}) == 2
    assert all(x.attempt == 1 and x.first_attempt_eligible for x in facts)
    assert all(not x.missing_reasons for x in facts)
    assert parser_version(manifest("pytest")) == "pytest-junit-v1"


def test_pytest_does_not_invent_retry_history():
    raw = b'<testsuite><testcase classname="suite" name="test"><rerunFailure>retry</rerunFailure></testcase></testsuite>'
    facts = parse_report(raw, manifest("pytest"))
    assert facts[0].attempt is None
    assert not facts[0].first_attempt_eligible
    assert not parse_report(
        raw, replace(manifest("pytest"), contains_complete_attempts=False)
    )[0].first_attempt_eligible


def test_allure_retry_and_parameter_identity_are_deterministic():
    first = result()
    first.update(status="failed", stop=1200)
    second = result("two")
    second.update(start=2000, stop=2600)
    third = result("three")
    third.update(
        historyId="history-cash", parameters=[{"name": "method", "value": "cash"}]
    )
    raw = archive(
        {
            "allure-results/two-result.json": second,
            "allure-results/one-result.json": first,
            "allure-results/three-result.json": third,
        }
    )
    facts = parse_report(raw, manifest())
    assert sorted(
        (f.attempt, f.result) for f in facts if f.data_row == "history-card"
    ) == [(1, "fail"), (2, "pass")]
    assert len({f.data_row for f in facts}) == 2
    assert all(f.first_attempt_eligible == (f.attempt == 1) for f in facts)
    assert parser_version(manifest()) == "allure-results-v1"


@pytest.mark.parametrize(
    "name",
    [
        "../evil-result.json",
        "/evil-result.json",
        "a/../../evil-result.json",
        "a\\evil-result.json",
    ],
)
def test_allure_rejects_unsafe_archive_names(name):
    with pytest.raises(ReportSecurityError):
        parse_report(archive({name: result()}), manifest())


def test_allure_requires_results_and_valid_json():
    for raw in [
        archive({"index.html": "<html/>"}),
        archive({"one-result.json": "broken"}),
    ]:
        with pytest.raises(ReportSecurityError):
            parse_report(raw, manifest())


def test_allure_rejects_duplicate_uuid_and_ambiguous_retry_order():
    with pytest.raises(ReportSecurityError):
        parse_report(
            archive({"one-result.json": result(), "two-result.json": result()}),
            manifest(),
        )
    facts = parse_report(
        archive({"one-result.json": result(), "two-result.json": result("two")}),
        manifest(),
    )
    assert all(f.attempt is None and not f.first_attempt_eligible for f in facts)


def test_legacy_manifest_fingerprint_is_unchanged():
    old = manifest("junit").to_dict()
    assert "report_format" not in old
    assert ReportManifest.from_dict(old).report_format == "junit"


def test_allure_durable_intake_evidence_and_project_authorization(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from tap_platform.app import create_app
    from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger, metadata
    from tap_platform.insights.adapters.objects import FileReportObjectStore
    from tap_platform.insights.application.intake import ReportIntake
    from tap_platform.insights.worker import ReportWorker
    from test_report_intake import authorizer, TOKEN

    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    metadata.create_all(engine)
    ledger = SqlAlchemyReportLedger(engine)
    objects = FileReportObjectStore(tmp_path / "objects")
    intake = ReportIntake(ledger=ledger, objects=objects)
    report = result(
        steps=[
            {
                "name": "Submit payment",
                "status": "failed",
                "statusDetails": {"message": "Rejected"},
                "attachments": [
                    {"name": "screen", "source": "screen.png", "type": "image/png"}
                ],
            }
        ]
    )
    raw = archive(
        {
            "allure-results/one-result.json": report,
            "allure-results/screen.png": b"\x89PNG\r\n\x1a\nexample",
        }
    )
    client = TestClient(
        create_app(
            report_intake=intake,
            report_ledger=ledger,
            report_objects=objects,
            insights_authorizer=authorizer("project-one"),
        )
    )
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-TAP-Report-Manifest": json.dumps(manifest().to_dict()),
        "Content-Type": "application/zip",
    }
    base = "/api/v1/projects/project-one/insights"
    accepted = client.post(base + "/reports", content=raw, headers=headers)
    assert accepted.status_code == 202
    receipt = accepted.json()["receiptId"]
    assert accepted.json()["parserVersion"] == "allure-results-v1"
    worker = ReportWorker(ledger=ledger, objects=objects)
    for _ in range(3):
        worker.process_one(receipt)
    assert ledger.attempts_for(receipt)[0].attempt == 1
    assert (
        client.post(base + "/reports", content=raw, headers=headers).json()["receiptId"]
        == receipt
    )
    evidence = client.get(base + f"/evidence/{receipt}/details", headers=headers)
    assert evidence.status_code == 200, evidence.text
    node = evidence.json()["attempts"][0]
    assert node["steps"][0]["message"] == "Rejected"
    attachment = node["steps"][0]["attachments"][0]
    response = client.get(
        base + f"/evidence/{receipt}/attachment",
        params={"source": attachment["source"]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\nexample"
    from tap_platform.insights.adapters import report_evidence as evidence_module

    parses = []
    original = evidence_module.parse_allure_members
    monkeypatch.setattr(
        evidence_module,
        "parse_allure_members",
        lambda *args, **kwargs: parses.append(1) or original(*args, **kwargs),
    )
    for _ in range(2):
        assert client.get(
            base + f"/evidence/{receipt}/details", headers=headers
        ).is_success
        assert client.get(
            base + f"/evidence/{receipt}/attachment",
            params={"source": attachment["source"]},
            headers=headers,
        ).is_success
    assert parses == []
    assert client.get(base + f"/evidence/{receipt}/details").status_code == 403
    assert (
        client.get(
            base.replace("project-one", "project-two") + f"/evidence/{receipt}/details",
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.get(
            base + f"/evidence/{receipt}/attachment",
            params={"source": "../secret"},
            headers=headers,
        ).status_code
        == 404
    )
    raw_response = client.get(base + f"/evidence/{receipt}", headers=headers)
    assert raw_response.headers["content-type"] == "application/zip"
    assert raw_response.content == raw


def test_allure_fixture_steps_and_missing_attachment_are_preserved():
    from tap_platform.insights.adapters.report_evidence import report_evidence

    raw = archive(
        {
            "allure-results/one-result.json": result(),
            "allure-results/fixture-container.json": {
                "uuid": "fixture",
                "children": ["one"],
                "befores": [
                    {
                        "name": "Login",
                        "status": "passed",
                        "attachments": [
                            {
                                "name": "log",
                                "source": "missing.txt",
                                "type": "text/plain",
                            }
                        ],
                    }
                ],
            },
        }
    )
    evidence = report_evidence(raw, manifest(), "allure-results-v1")
    assert evidence["attempts"][0]["steps"][0]["name"] == "Login"
    assert evidence["attempts"][0]["steps"][0]["attachments"][0]["available"] is False


def test_allure_rejects_expansion_bomb_and_symbolic_link():
    with pytest.raises(ReportSecurityError, match="expansion-limit"):
        parse_report(
            archive({"one-result.json": result(), "huge.txt": b"0" * 1000000}),
            manifest(),
        )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        output.writestr(info, "/etc/passwd")
    with pytest.raises(ReportSecurityError, match="unsafe-member"):
        parse_report(stream.getvalue(), manifest())


def test_invalid_explicit_pytest_attempt_is_not_overridden():
    raw = b'<testsuite><testcase classname="suite" name="test"><properties><property name="tap.attempt" value="bad"/></properties></testcase></testsuite>'
    fact = parse_report(raw, manifest("pytest"))[0]
    assert fact.attempt is None
    assert not fact.first_attempt_eligible


def test_allure_contiguous_retries_are_ordered_and_storage_bounds_hold():
    first = result()
    first.update(status="failed", stop=1200)
    second = result("two")
    second.update(start=1200, stop=1300)
    assert [
        f.attempt
        for f in parse_report(
            archive({"one-result.json": first, "two-result.json": second}), manifest()
        )
    ] == [1, 2]
    with pytest.raises(ReportSecurityError):
        parse_report(archive({"x" * 245 + "-result.json": result()}), manifest())


def test_allure_fixture_fanout_has_a_byte_budget(monkeypatch):
    from tap_platform.insights.adapters import allure

    monkeypatch.setattr(allure, "MAX_EVIDENCE_BYTES", 2000)
    raw = archive(
        {
            "one-result.json": result(),
            "two-result.json": result("two"),
            "fixture-container.json": {
                "children": ["one", "two"],
                "befores": [{"name": "setup", "statusDetails": {"trace": "x" * 1200}}],
            },
        }
    )
    with pytest.raises(ReportSecurityError, match="evidence-limit"):
        parse_report(raw, manifest())


@pytest.mark.parametrize(
    "format,filename", [("pytest", "pytest.xml"), ("allure", "allure-results.zip")]
)
def test_generated_pytest_allure_outputs_produce_real_metrics(format, filename):
    from datetime import UTC, datetime, timedelta
    from tap_platform.insights.domain.metrics import (
        MetricAttempt,
        MetricId,
        MetricWindow,
        calculate_metrics,
    )
    from tap_platform.insights.adapters.report_evidence import report_evidence

    root = Path(__file__).parents[1] / "fixtures/insights/pytest-allure"
    raw = (root / filename).read_bytes()
    m = manifest(format)
    facts = parse_report(raw, m)
    assert sorted(f.result for f in facts) == ["fail", "pass", "pass", "skipped"]
    now = datetime.now(UTC)
    attempts = [
        MetricAttempt(
            fact_key=str(i),
            receipt_id="receipt",
            project_id=m.project_id,
            source_id=m.source_id,
            external_run_id=m.external_run_id,
            stable_test_id=f.stable_test_id,
            source_test_identity=f.source_test_identity,
            data_row=f.data_row,
            application_commit=m.application_commit,
            script_commit=m.script_commit,
            environment=m.environment,
            configuration=m.configuration,
            attempt=f.attempt,
            result=f.result,
            duration_seconds=f.duration_seconds,
            first_attempt_eligible=f.first_attempt_eligible,
            missing_reasons=f.missing_reasons,
            run_started_at=now,
        )
        for i, f in enumerate(facts)
    ]
    values = {
        v.metric_id: v
        for v in calculate_metrics(
            attempts,
            metric_ids=tuple(MetricId),
            window=MetricWindow(
                start=now - timedelta(hours=1),
                end=now + timedelta(hours=1),
                timezone="UTC",
            ),
        )
    }
    assert values[MetricId.FIRST_PASS_RATE].numerator == 2
    assert values[MetricId.FIRST_PASS_RATE].denominator == 3
    assert values[MetricId.SKIPPED_COUNT].value == 1
    evidence = report_evidence(raw, m, parser_version(m))
    assert any(
        "Synthetic missing identity evidence" in n["message"]
        for n in evidence["attempts"]
    )
    if format == "allure":
        assert any(n["steps"] for n in evidence["attempts"])


def test_allure_ignores_macos_archive_metadata():
    raw = archive(
        {
            "allure-results/one-result.json": result(),
            "__MACOSX/allure-results/._one-result.json": b"\x00\x05\x16\x07binary",
            "allure-results/._two-result.json": b"\x00\x05\x16\x07binary",
            "allure-results/.DS_Store": b"\x00\x00\x00\x01Bud1",
        }
    )
    facts = parse_report(raw, manifest())
    assert [f.source_locator for f in facts] == ["allure-results/one-result.json"]


def test_allure_long_text_is_truncated_not_rejected():
    from tap_platform.insights.adapters.report_evidence import report_evidence

    import hashlib

    def prose(size):
        # Non-repeating text keeps the archive under the expansion ratio guard.
        chunks = (hashlib.sha256(str(i).encode()).hexdigest() for i in range(size))
        return "".join(chunks)[:size]

    first = result(
        statusDetails={"message": "m" + prose(20000), "trace": prose(200000)},
        steps=[{"name": prose(20000), "status": "failed"}],
    )
    prefix = "suite#" + prose(600)
    first.update(
        fullName=prefix + "a",
        historyId="",
        testCaseId="",
        parameters=[{"name": "payload", "value": prose(20000)}],
    )
    second = result("two")
    second.update(fullName=prefix + "b", historyId="", testCaseId="")
    raw = archive({"one-result.json": first, "two-result.json": second})
    facts = parse_report(raw, manifest())
    assert len({f.stable_test_id for f in facts}) == 2
    assert len({f.source_test_identity for f in facts}) == 2
    assert all(len(f.source_test_identity) <= 512 for f in facts)
    node = next(
        n
        for n in report_evidence(raw, manifest(), "allure-results-v1")["attempts"]
        if n["message"]
    )
    assert node["message"].startswith("m")
    assert len(node["message"]) < 10100 and node["message"].endswith("[truncated]")
    assert len(node["trace"]) < 100100
    assert len(node["steps"][0]["name"]) < 10100


def test_allure_honors_external_test_id_mapping():
    raw = archive({"one-result.json": result()})
    mapped = replace(
        manifest(), external_test_id_mapping=(("test_checkout::test_pay", "TC-7"),)
    )
    assert parse_report(raw, mapped)[0].stable_test_id == "TC-7"
    assert parse_report(raw, manifest())[0].stable_test_id != "TC-7"


def test_junit_evidence_status_reflects_the_testcase_outcome():
    from tap_platform.insights.adapters.report_evidence import report_evidence

    raw = (
        b'<testsuite><testcase classname="s" name="pass"/>'
        b'<testcase classname="s" name="fail"><failure message="boom">trace</failure></testcase>'
        b'<testcase classname="s" name="error"><error message="err"/></testcase>'
        b'<testcase classname="s" name="skip"><skipped message="later"/></testcase>'
        b"</testsuite>"
    )
    m = manifest("pytest")
    nodes = report_evidence(raw, m, parser_version(m))["attempts"]
    assert [(n["name"], n["status"]) for n in nodes] == [
        ("pass", "passed"),
        ("fail", "failed"),
        ("error", "broken"),
        ("skip", "skipped"),
    ]
    assert nodes[1]["message"] == "boom" and nodes[1]["trace"] == "trace"


def test_xml_reports_are_parsed_in_one_streaming_pass(monkeypatch):
    from xml.etree import ElementTree

    from tap_platform.insights.adapters.report_evidence import report_evidence

    def forbidden(*args, **kwargs):
        raise AssertionError("XML must not be materialized as a full DOM")

    monkeypatch.setattr(ElementTree, "fromstring", forbidden)
    raw = b'<testsuite><testcase classname="s" name="t"><rerunFailure/></testcase></testsuite>'
    for format in ("pytest", "junit"):
        m = manifest(format)
        assert parse_report(raw, m)
        assert report_evidence(raw, m, parser_version(m))["attempts"]


def test_allure_evidence_reads_and_parses_the_archive_once(monkeypatch):
    from tap_platform.insights.adapters import allure
    from tap_platform.insights.adapters import report_evidence as evidence_module

    calls = {"archive": 0, "node": 0}
    original_archive, original_node = allure.archive_members, allure.evidence_node

    def counted_archive(raw):
        calls["archive"] += 1
        return original_archive(raw)

    def counted_node(node, parent, members, depth=0):
        calls["node"] += depth == 0
        return original_node(node, parent, members, depth)

    monkeypatch.setattr(evidence_module, "archive_members", counted_archive)
    monkeypatch.setattr(allure, "evidence_node", counted_node)
    raw = archive({"one-result.json": result(), "two-result.json": result("two")})
    evidence_module.report_evidence(raw, manifest(), "allure-results-v1")
    assert calls == {"archive": 1, "node": 2}


def test_evidence_carries_the_frozen_legacy_fact_key():
    from tap_platform.insights.adapters.report_evidence import report_evidence
    from tap_platform.insights.application.projection import _semantic_projection_v1
    from tap_platform.insights.domain.reports import (
        Completeness,
        ReportReceipt,
        ReportState,
    )

    m = manifest("junit")
    raw = b'<testsuite><testcase classname="s" name="t"/></testsuite>'
    receipt = ReportReceipt(
        receipt_id="receipt",
        project_id=m.project_id,
        source_id=m.source_id,
        external_run_id=m.external_run_id,
        batch_id=m.batch_id,
        shard_id=m.shard_id,
        checksum="0" * 64,
        parser_version="junit-v1",
        correction_no=0,
        raw_object_ref="raw",
        state=ReportState.READY,
        completeness=Completeness.COMPLETE,
        size_bytes=len(raw),
    )
    legacy = _semantic_projection_v1(receipt, m, parse_report(raw, m))
    node = report_evidence(raw, m, "junit-v1", receipt=receipt)["attempts"][0]
    assert node["legacyFactKey"] == legacy["attempts"][0]["fact_key"]
    assert node["factKey"] != node["legacyFactKey"]


@pytest.mark.parametrize(
    "format,body,content_type",
    [
        ("allure", b"<testsuite/>", "application/zip"),
        ("allure", None, "application/xml"),
        ("pytest", None, "application/xml"),
        ("pytest", b"<testsuite/>", "application/zip"),
        ("junit", None, None),
    ],
)
def test_upload_rejects_a_report_that_contradicts_its_format(
    tmp_path, format, body, content_type
):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from tap_platform.app import create_app
    from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger, metadata
    from tap_platform.insights.adapters.objects import FileReportObjectStore
    from tap_platform.insights.application.intake import ReportIntake
    from test_report_intake import authorizer, TOKEN

    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    metadata.create_all(engine)
    ledger = SqlAlchemyReportLedger(engine)
    objects = FileReportObjectStore(tmp_path / "objects")
    client = TestClient(
        create_app(
            report_intake=ReportIntake(ledger=ledger, objects=objects),
            report_ledger=ledger,
            report_objects=objects,
            insights_authorizer=authorizer("project-one"),
        )
    )
    raw = body if body is not None else archive({"one-result.json": result()})
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-TAP-Report-Manifest": json.dumps(manifest(format).to_dict()),
    }
    if content_type:
        headers["Content-Type"] = content_type
    response = client.post(
        "/api/v1/projects/project-one/insights/reports", content=raw, headers=headers
    )
    assert response.status_code == 415, response.text
    assert "does not match reportFormat" in response.json()["detail"]


def test_report_parsers_share_a_format_neutral_security_error():
    from tap_platform.insights.adapters.report_errors import ReportSecurityError

    with pytest.raises(ReportSecurityError) as allure_error:
        parse_report(archive({"index.html": "<html/>"}), manifest())
    assert type(allure_error.value).__name__ == "AllureSecurityError"
    assert not isinstance(allure_error.value, JUnitSecurityError)
    with pytest.raises(ReportSecurityError):
        parse_report(b"<!DOCTYPE x><testsuite/>", manifest("pytest"))
