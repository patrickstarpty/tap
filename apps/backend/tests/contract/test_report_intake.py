import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal
from tap_platform.app import create_app
from tap_platform.insights.adapters.junit import JUnitSecurityError, parse_junit
from tap_platform.insights.adapters.mysql import (
    SqlAlchemyReportLedger,
    metadata,
)
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.intake import ReportIntake, UploadTooLarge
from tap_platform.insights.domain.reports import (
    Completeness,
    ReportManifest,
    ReportState,
)
from tap_platform.insights.http import BearerPrincipalAuthorizer
from tap_platform.insights.worker import ReportWorker


TOKEN = "task9-test-bearer-token"


def manifest(**changes: object) -> ReportManifest:
    values: dict[str, object] = {
        "project_id": "project-one",
        "source_id": "ci-one",
        "external_run_id": "run-one",
        "batch_id": "batch-one",
        "shard_id": "1",
        "expected_shards": 1,
        "contains_complete_attempts": True,
        "application_commit": "app-abc",
        "script_commit": "tests-def",
        "environment": "ci",
        "configuration": "browser=chromium",
        "timezone": "UTC",
        "correction_no": 0,
        "attachments": frozenset(),
    }
    values.update(changes)
    return ReportManifest(**values)


def junit(*cases: str) -> bytes:
    return (
        "<?xml version='1.0' encoding='UTF-8'?><testsuite>"
        + "".join(cases)
        + "</testsuite>"
    ).encode()


def case(
    *,
    name: str = "same-name",
    stable_id: str | None = "stable-one",
    data_row: str | None = None,
    attempt: str | None = "1",
    attachment: str | None = None,
    result: str = "pass",
) -> str:
    properties = []
    if stable_id is not None:
        properties.append(f"<property name='tap.test_id' value='{stable_id}'/>")
    if data_row is not None:
        properties.append(f"<property name='tap.data_row' value='{data_row}'/>")
    if attempt is not None:
        properties.append(f"<property name='tap.attempt' value='{attempt}'/>")
    if attachment is not None:
        properties.append(f"<property name='tap.attachment' value='{attachment}'/>")
    outcome = "" if result == "pass" else f"<{result} message='redacted'/>"
    return (
        f"<testcase classname='suite' name='{name}' time='0.25'>"
        f"<properties>{''.join(properties)}</properties>{outcome}</testcase>"
    )


@pytest.fixture
def runtime(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    metadata.create_all(engine)
    ledger = SqlAlchemyReportLedger(engine)
    objects = FileReportObjectStore(tmp_path / "objects")
    intake = ReportIntake(ledger=ledger, objects=objects, max_upload_bytes=4096)
    return ledger, objects, intake


def drain(worker: ReportWorker, receipt_id: str) -> None:
    for _ in range(3):
        assert worker.process_one(receipt_id)
    assert worker.confirm_projection(receipt_id) is False


def authorizer(project_id: str) -> BearerPrincipalAuthorizer:
    return BearerPrincipalAuthorizer(
        token=TOKEN,
        principal=AccessPrincipal(
            project_id=project_id,
            principal_id="task9-test-client",
            principal_type="service",
            audience="tap",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            actions=frozenset(
                {
                    "insights.evidence.read",
                    "insights.reports.create",
                    "insights.reports.read",
                }
            ),
            enabled=True,
        ),
        expected_audience="tap",
    )


def test_same_identity_and_checksum_returns_the_original_receipt(runtime) -> None:
    """Changing idempotency lookup to always insert must fail this test."""
    ledger, objects, intake = runtime
    raw = junit(case())

    first = intake.receive(manifest(), [raw[:17], raw[17:]])
    retried = intake.receive(manifest(), [raw])

    assert retried == first
    assert first.state is ReportState.RECEIVED
    assert objects.list_raw_objects() == [first.raw_object_ref]
    assert ledger.outbox_events(first.receipt_id) == ["report.received"]


def test_same_identity_with_different_content_is_a_durable_conflict(runtime) -> None:
    """Removing content conflict detection must accept the second payload."""
    ledger, _, intake = runtime
    accepted = intake.receive(manifest(), [junit(case(stable_id="one"))])
    conflicted = intake.receive(manifest(), [junit(case(stable_id="two"))])

    assert conflicted.receipt_id != accepted.receipt_id
    assert conflicted.state is ReportState.CONFLICTED
    assert conflicted.conflict_with_receipt_id == accepted.receipt_id
    assert ledger.get_receipt(accepted.receipt_id).state is ReportState.RECEIVED


def test_same_raw_checksum_with_changed_manifest_is_not_an_exact_retry(runtime) -> None:
    """Reusing a receipt despite manifest drift must fail this test."""
    _, _, intake = runtime
    raw = junit(case())
    accepted = intake.receive(manifest(), [raw])

    changed = intake.receive(
        manifest(
            application_commit="app-changed",
            contains_complete_attempts=False,
            expected_shards=2,
        ),
        [raw],
    )

    assert changed.receipt_id != accepted.receipt_id
    assert changed.state is ReportState.CONFLICTED
    assert changed.conflict_with_receipt_id == accepted.receipt_id


def test_shards_arrive_out_of_order_and_corrections_append_versions(runtime) -> None:
    """Using arrival order as identity or correction authority must fail."""
    ledger, _, intake = runtime
    shard_two = intake.receive(
        manifest(shard_id="2", expected_shards=2), [junit(case(stable_id="two"))]
    )
    assert shard_two.completeness is Completeness.PARTIAL

    shard_one = intake.receive(
        manifest(shard_id="1", expected_shards=2), [junit(case(stable_id="one"))]
    )
    corrected = intake.receive(
        manifest(shard_id="1", expected_shards=2, correction_no=2),
        [junit(case(stable_id="one-corrected"))],
    )
    late_correction = intake.receive(
        manifest(shard_id="1", expected_shards=2, correction_no=1),
        [junit(case(stable_id="one-late"))],
    )

    assert shard_one.completeness is Completeness.COMPLETE
    assert (
        ledger.get_receipt(shard_two.receipt_id).completeness is Completeness.COMPLETE
    )
    assert corrected.correction_no == 2
    assert late_correction.correction_no == 1
    assert (
        len({corrected.receipt_id, late_correction.receipt_id, shard_one.receipt_id})
        == 3
    )


def test_junit_mapping_uses_explicit_identity_and_preserves_unknowns() -> None:
    """Inferring identity from display names, rows, or arrival order must fail."""
    raw = junit(
        case(name="duplicate", stable_id="id-a", data_row="row-a", attempt="1"),
        case(name="duplicate", stable_id="id-b", data_row="row-a", attempt="1"),
        case(name="parameterized", stable_id="id-c", data_row="row-1", attempt="1"),
        case(name="parameterized", stable_id="id-c", data_row="row-2", attempt=None),
        case(
            name="missing-attachment",
            stable_id=None,
            attempt="unknown",
            attachment="trace.zip",
        ),
    )

    attempts = parse_junit(raw, manifest(contains_complete_attempts=False))

    assert [(item.stable_test_id, item.data_row) for item in attempts[:4]] == [
        ("id-a", "row-a"),
        ("id-b", "row-a"),
        ("id-c", "row-1"),
        ("id-c", "row-2"),
    ]
    unknown = attempts[-1]
    assert unknown.source_test_identity == "suite::missing-attachment"
    assert unknown.stable_test_id is None
    assert unknown.attempt is None
    assert set(unknown.missing_reasons) == {
        "attempt-history-incomplete",
        "attempt-number-invalid",
        "attachment-missing:trace.zip",
        "stable-test-id-missing",
    }
    assert all(item.first_attempt_eligible is False for item in attempts)


def test_junit_mapping_bounds_attempt_and_duration_values() -> None:
    """Unbounded integers or nonfinite floats must not strand validation."""
    raw = junit(
        case(attempt="999999999999999999999999999999"),
        "<testcase classname='suite' name='nan' time='NaN'>"
        "<properties><property name='tap.test_id' value='nan-test'/>"
        "<property name='tap.attempt' value='1'/></properties></testcase>",
        "<testcase classname='suite' name='infinity' time='Infinity'>"
        "<properties><property name='tap.test_id' value='inf-test'/>"
        "<property name='tap.attempt' value='1'/></properties></testcase>",
    )

    attempts = parse_junit(raw, manifest())

    assert attempts[0].attempt is None
    assert "attempt-number-invalid" in attempts[0].missing_reasons
    assert attempts[1].duration_seconds is None
    assert attempts[2].duration_seconds is None
    assert all("duration-invalid" in item.missing_reasons for item in attempts[1:])


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b"<testsuite><testcase></testsuite>", "invalid-xml"),
        (
            b"<!DOCTYPE x [<!ENTITY boom 'boom'>]><testsuite>&boom;</testsuite>",
            "xml-dtd-or-entity-forbidden",
        ),
    ],
)
def test_worker_rejects_invalid_or_entity_bearing_xml(runtime, raw, reason) -> None:
    """Relaxing secure XML parsing must let these payloads reach mapped."""
    ledger, objects, intake = runtime
    receipt = intake.receive(manifest(), [raw])
    worker = ReportWorker(ledger=ledger, objects=objects)

    assert worker.process_one(receipt.receipt_id)
    assert worker.process_one(receipt.receipt_id)

    rejected = ledger.get_receipt(receipt.receipt_id)
    assert rejected.state is ReportState.REJECTED
    assert rejected.failure_reason == reason
    assert objects.read(receipt.raw_object_ref) == raw


def test_upload_is_bounded_before_receipt_acknowledgement(runtime) -> None:
    """Removing streaming byte accounting must acknowledge an oversized report."""
    ledger, objects, intake = runtime

    with pytest.raises(UploadTooLarge):
        intake.receive(manifest(), [b"x" * 4096, b"y"])

    assert ledger.count_receipts() == 0
    assert objects.list_raw_objects() == []


def test_parser_has_independent_depth_and_node_limits() -> None:
    """Checking only total upload size must allow pathological XML shapes."""
    deep = ("<testsuite>" + "<x>" * 40 + "</x>" * 40 + "</testsuite>").encode()
    with pytest.raises(JUnitSecurityError, match="xml-depth-limit"):
        parse_junit(deep, manifest(), max_depth=16)


def test_utf16_cannot_bypass_entity_declaration_rejection() -> None:
    """Scanning only ASCII bytes must permit an encoded entity declaration."""
    encoded = (
        "<?xml version='1.0' encoding='UTF-16'?>"
        "<!DOCTYPE x [<!ENTITY boom 'boom'>]>"
        "<testsuite>&boom;</testsuite>"
    ).encode("utf-16")
    with pytest.raises(JUnitSecurityError, match="xml-encoding-forbidden"):
        parse_junit(encoded, manifest())


def test_retry_after_lost_response_does_not_repeat_the_test_run(runtime) -> None:
    """Treating an HTTP retry as a new run must create a new receipt or attempt."""
    ledger, objects, intake = runtime
    raw = junit(case(stable_id="stable-run"))
    lost = intake.receive(manifest(), [raw])
    recovered = intake.receive(manifest(), [raw])
    drain(ReportWorker(ledger=ledger, objects=objects), recovered.receipt_id)

    assert recovered.receipt_id == lost.receipt_id
    assert len(ledger.attempts_for(recovered.receipt_id)) == 1
    assert ledger.get_receipt(recovered.receipt_id).state is ReportState.PROJECTING


def test_failed_mapping_can_retry_only_the_persisted_report(runtime) -> None:
    """Removing the explicit parse retry must require a new test execution."""
    ledger, objects, intake = runtime
    receipt = intake.receive(manifest(), [junit(case())])
    ledger.transition(
        receipt.receipt_id,
        expected=ReportState.RECEIVED,
        target=ReportState.VALIDATING,
    )
    ledger.transition(
        receipt.receipt_id,
        expected=ReportState.VALIDATING,
        target=ReportState.FAILED,
        reason="temporary-parser-failure",
    )
    worker = ReportWorker(ledger=ledger, objects=objects)

    assert worker.retry_failed(receipt.receipt_id)
    assert ledger.get_receipt(receipt.receipt_id).state is ReportState.VALIDATING
    assert worker.process_one(receipt.receipt_id)
    assert ledger.get_receipt(receipt.receipt_id).state is ReportState.MAPPED
    assert len(ledger.attempts_for(receipt.receipt_id)) == 1


def test_mapping_persistence_failure_becomes_durable_failed(
    runtime, monkeypatch
) -> None:
    """A mapped persistence error must leave a durable failed state."""
    ledger, objects, intake = runtime
    receipt = intake.receive(manifest(), [junit(case())])
    worker = ReportWorker(ledger=ledger, objects=objects)
    assert worker.process_one(receipt.receipt_id)
    original_transition = ledger.transition

    def fail_mapped(receipt_id: str, **kwargs):
        if kwargs["target"] is ReportState.MAPPED:
            raise ValueError("simulated-attempt-persistence-failure")
        return original_transition(receipt_id, **kwargs)

    monkeypatch.setattr(ledger, "transition", fail_mapped)

    assert worker.process_one(receipt.receipt_id)
    failed = ledger.get_receipt(receipt.receipt_id)
    assert failed.state is ReportState.FAILED
    assert failed.failure_reason == "mapping-persistence-failed"


def test_orphaned_staging_file_is_recovered(tmp_path: Path) -> None:
    """Cleaning only finalized raw objects must leak crash-left staging bytes."""
    objects = FileReportObjectStore(tmp_path / "objects")
    interrupted = tmp_path / "objects" / ".staging" / "interrupted-upload"
    interrupted.write_bytes(b"partial")

    recent_cutoff = datetime.now(UTC) - timedelta(hours=1)
    assert (
        objects.recover_orphans(reference_supplier=set, older_than=recent_cutoff) == []
    )
    assert interrupted.exists()
    old_timestamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(interrupted, (old_timestamp, old_timestamp))
    removed = objects.recover_orphans(
        reference_supplier=set,
        older_than=datetime.now(UTC) - timedelta(hours=1),
    )

    assert removed == [".staging/interrupted-upload"]
    assert not interrupted.exists()


def test_orphan_recovery_cannot_race_an_old_content_retry(runtime) -> None:
    """A stale reference snapshot must not delete bytes before receipt commit."""
    ledger, objects, _ = runtime
    raw = junit(case())
    orphan = objects.persist([raw], max_bytes=4096)
    old_timestamp = (datetime.now(UTC) - timedelta(days=1)).timestamp()
    os.utime(objects.root / orphan.ref, (old_timestamp, old_timestamp))
    accepting = Event()
    release_accept = Event()

    class BlockingLedger:
        def accept(self, report_manifest, stored):
            accepting.set()
            assert release_accept.wait(timeout=5)
            return ledger.accept(report_manifest, stored)

    intake = ReportIntake(ledger=BlockingLedger(), objects=objects)
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipt_future = executor.submit(intake.receive, manifest(), [raw])
        assert accepting.wait(timeout=5)
        recovery_future = executor.submit(
            objects.recover_orphans,
            reference_supplier=ledger.referenced_raw_objects,
            older_than=datetime.now(UTC),
        )
        with pytest.raises(FutureTimeoutError):
            recovery_future.result(timeout=0.1)
        release_accept.set()
        receipt = receipt_future.result(timeout=5)
        assert recovery_future.result(timeout=5) == []

    assert objects.read(receipt.raw_object_ref) == raw


def test_orphan_recovery_cannot_delete_an_active_staging_upload(runtime) -> None:
    """Recovery must serialize with an upload even past the supplied cutoff."""
    ledger, objects, _ = runtime
    upload_paused = Event()
    release_upload = Event()
    raw = junit(case())

    def paused_chunks():
        yield raw[:10]
        upload_paused.set()
        assert release_upload.wait(timeout=5)
        yield raw[10:]

    intake = ReportIntake(ledger=ledger, objects=objects)
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipt_future = executor.submit(intake.receive, manifest(), paused_chunks())
        assert upload_paused.wait(timeout=5)
        recovery_future = executor.submit(
            objects.recover_orphans,
            reference_supplier=ledger.referenced_raw_objects,
            older_than=datetime.now(UTC) + timedelta(days=1),
        )
        with pytest.raises(FutureTimeoutError):
            recovery_future.result(timeout=0.1)
        release_upload.set()
        receipt = receipt_future.result(timeout=5)
        assert recovery_future.result(timeout=5) == []

    assert objects.read(receipt.raw_object_ref) == raw


def test_frozen_camel_case_manifest_is_normalized_without_trusting_completeness() -> (
    None
):
    """Requiring a new manifest spelling or trusting declared shards must fail."""
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "insights" / "task0-inputs-v1.json"
        ).read_text(encoding="utf-8")
    )
    value = dict(fixture["reports"][-1]["manifest"])
    value["timezone"] = fixture["source"]["timezone"]
    parsed = ReportManifest.from_dict(value)

    assert parsed.project_id == "synthetic-commerce-project"
    assert parsed.expected_shards == 2
    assert parsed.initial_completeness is Completeness.PARTIAL


def test_evidence_download_is_authorized_and_proxied_without_store_credentials(
    runtime,
) -> None:
    """Returning raw storage URLs or skipping project/action checks must fail."""
    ledger, objects, intake = runtime
    raw = junit(case())
    receipt = intake.receive(manifest(), [raw])
    app = create_app(
        report_intake=intake,
        report_ledger=ledger,
        report_objects=objects,
        insights_authorizer=authorizer("project-one"),
    )
    client = TestClient(app)

    denied = client.get(
        f"/api/v1/projects/project-one/insights/evidence/{receipt.receipt_id}"
    )
    cross_project = client.get(
        f"/api/v1/projects/project-one/insights/evidence/{receipt.receipt_id}",
        headers={
            "X-TAP-Project-ID": "project-two",
            "X-TAP-Actions": "insights.evidence.read",
        },
    )
    allowed = client.get(
        f"/api/v1/projects/project-one/insights/evidence/{receipt.receipt_id}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
        },
    )

    assert denied.status_code == 403
    assert cross_project.status_code == 403
    assert allowed.status_code == 200
    assert allowed.content == raw
    assert allowed.headers["content-type"].startswith("application/xml")
    assert "credential" not in json.dumps(dict(allowed.headers)).lower()
    assert receipt.raw_object_ref not in allowed.headers.values()


def test_http_intake_accepts_the_frozen_manifest_and_real_junit_fixture(
    runtime,
) -> None:
    """Breaking the public manifest/raw-byte journey must reject the frozen artifact."""
    ledger, objects, intake = runtime
    fixture_root = Path(__file__).parents[1] / "fixtures" / "insights"
    fixture = json.loads(
        (fixture_root / "task0-inputs-v1.json").read_text(encoding="utf-8")
    )
    report = fixture["reports"][2]
    manifest_value = dict(report["manifest"])
    manifest_value["timezone"] = fixture["source"]["timezone"]
    raw = (fixture_root / report["reportFile"]).read_bytes()
    client = TestClient(
        create_app(
            report_intake=intake,
            report_ledger=ledger,
            report_objects=objects,
            insights_authorizer=authorizer("synthetic-commerce-project"),
        )
    )

    response = client.post(
        "/api/v1/projects/synthetic-commerce-project/insights/reports",
        content=raw,
        headers={
            "Content-Type": "application/xml",
            "Authorization": f"Bearer {TOKEN}",
            "X-TAP-Report-Manifest": json.dumps(manifest_value),
        },
    )

    assert response.status_code == 202
    receipt = ledger.get_receipt(response.json()["receiptId"])
    drain(ReportWorker(ledger=ledger, objects=objects), receipt.receipt_id)
    attempts = ledger.attempts_for(receipt.receipt_id)
    assert [(item.stable_test_id, item.attempt, item.result) for item in attempts] == [
        ("checkout-risk-service", 1, "fail"),
        ("checkout-risk-service", 2, "pass"),
    ]


def test_default_app_wires_the_independent_tap_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving create_app unwired must make the deployed TAP route always unavailable."""
    database_url = f"sqlite:///{tmp_path / 'runtime.sqlite'}"
    engine = create_engine(database_url)
    metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setenv("TAP_DATABASE_URL", database_url)
    monkeypatch.setenv("TAP_REPORT_OBJECT_ROOT", str(tmp_path / "runtime-objects"))
    monkeypatch.setenv("TAP_REPORT_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("TAP_REPORT_PROJECT_ID", "project-one")
    monkeypatch.setenv(
        "TAP_REPORT_TOKEN_EXPIRES_AT",
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    client = TestClient(create_app())
    raw = junit(case())

    response = client.post(
        "/api/v1/projects/project-one/insights/reports",
        content=raw,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-TAP-Report-Manifest": json.dumps(manifest().to_dict()),
        },
    )

    assert response.status_code == 202


def test_manifest_refuses_fabricated_completeness_or_attempt_identity() -> None:
    """Trusting caller-declared received shards must permit fabricated completeness."""
    assert manifest(expected_shards=None).initial_completeness is Completeness.UNKNOWN
    with pytest.raises(ValueError, match="expected_shards"):
        manifest(expected_shards=0)
    with pytest.raises(ValueError, match="correction_no"):
        manifest(correction_no=-1)
