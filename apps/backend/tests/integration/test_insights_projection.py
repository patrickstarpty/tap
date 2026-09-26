from __future__ import annotations

import os
import json
import base64
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tap_platform.insights.adapters.clickhouse import ClickHouseInsightsStore
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.intake import ReportIntake
from tap_platform.insights.application.projection import (
    ProjectionCoordinator,
    ProjectionRebuilder,
)
from tap_platform.insights.domain.reports import ReportManifest, ReportState
from tap_platform.insights.worker import ReportWorker


MYSQL_URL = os.getenv("TAP_TASK10_MYSQL_URL")
CLICKHOUSE_URL = os.getenv("TAP_TASK10_CLICKHOUSE_URL")
CLICKHOUSE_USER = os.getenv("TAP_TASK10_CLICKHOUSE_USER", "tap_insights_writer")
CLICKHOUSE_PASSWORD = os.getenv("TAP_TASK10_CLICKHOUSE_PASSWORD")
CLICKHOUSE_ADMIN_USER = os.getenv("TAP_TASK10_CLICKHOUSE_ADMIN_USER")
CLICKHOUSE_ADMIN_PASSWORD = os.getenv("TAP_TASK10_CLICKHOUSE_ADMIN_PASSWORD")


def clickhouse_admin(sql: str) -> bytes:
    assert CLICKHOUSE_URL is not None
    assert CLICKHOUSE_ADMIN_USER is not None
    assert CLICKHOUSE_ADMIN_PASSWORD is not None
    parsed = urllib.parse.urlsplit(CLICKHOUSE_URL)
    query = urllib.parse.parse_qs(parsed.query)
    query["query"] = [sql]
    request = urllib.request.Request(
        urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(query, doseq=True),
                "",
            )
        ),
        data=b"",
        headers={
            "Authorization": "Basic "
            + base64.b64encode(
                f"{CLICKHOUSE_ADMIN_USER}:{CLICKHOUSE_ADMIN_PASSWORD}".encode()
            ).decode()
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def manifest(*, correction_no: int = 0, attachments: frozenset[str] = frozenset()):
    return ReportManifest(
        project_id="isolated-project",
        source_id="fixture-ci",
        external_run_id="fixture-run",
        batch_id="fixture-batch",
        shard_id="1",
        expected_shards=1,
        contains_complete_attempts=True,
        application_commit="app-1",
        script_commit="tests-1",
        environment="isolated",
        configuration="browser=chromium",
        timezone="UTC",
        correction_no=correction_no,
        attachments=attachments,
        job_id="job-1",
        build_id="build-1",
        branch="main",
        business_cycle_id="cycle-1",
    )


def report(name: str = "retry.xml") -> bytes:
    return (
        Path(__file__).parents[1] / "fixtures" / "insights" / "reports" / name
    ).read_bytes()


def empty_report() -> bytes:
    return b'<testsuite name="corrected-empty" tests="0"/>'


@pytest.fixture
def projection_runtime(tmp_path: Path):
    if not all(
        (
            MYSQL_URL,
            CLICKHOUSE_URL,
            CLICKHOUSE_PASSWORD,
            CLICKHOUSE_ADMIN_USER,
            CLICKHOUSE_ADMIN_PASSWORD,
        )
    ):
        pytest.skip(
            "requires explicitly isolated Task 10 MySQL and ClickHouse services"
        )
    assert MYSQL_URL is not None
    assert CLICKHOUSE_URL is not None
    assert CLICKHOUSE_PASSWORD is not None
    environment = dict(os.environ, TAP_DATABASE_URL=MYSQL_URL)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(Path(__file__).parents[2] / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(MYSQL_URL)
    with engine.begin() as connection:
        for table_name in (
            "tap_insights_projection_batches",
            "tap_insights_projection_state",
            "tap_insights_projection_versions",
            "tap_report_attempts",
            "tap_report_transitions",
            "tap_report_outbox",
            "tap_report_receipts",
            "tap_report_identity_claims",
        ):
            connection.execute(text(f"DELETE FROM {table_name}"))
    store = ClickHouseInsightsStore(
        CLICKHOUSE_URL,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
    )
    for table_name in (
        "report_batch_markers",
        "attempt_facts",
        "evidence_refs",
        "run_dimensions",
        "configuration_dimensions",
    ):
        clickhouse_admin(f"TRUNCATE TABLE {table_name}")
        clickhouse_admin(f"SYSTEM STOP MERGES {table_name}")
    ledger = SqlAlchemyReportLedger(engine)
    objects = FileReportObjectStore(tmp_path / "objects")
    try:
        yield ledger, objects, store
    finally:
        for table_name in (
            "report_batch_markers",
            "attempt_facts",
            "evidence_refs",
            "run_dimensions",
            "configuration_dimensions",
        ):
            clickhouse_admin(f"SYSTEM START MERGES {table_name}")
        engine.dispose()


def test_rebuild_script_is_dry_run_by_default_and_rejects_unsafe_targets(
    tmp_path: Path,
) -> None:
    """A typo or shared/default target must never start a rebuild."""
    script = Path(__file__).parents[4] / "scripts" / "rebuild-insights.py"
    base = [
        sys.executable,
        str(script),
        "--compose-project",
        "tap-insights-rebuild-task10",
        "--target-version",
        "rebuild-task10-v2",
        "--database-url",
        "mysql+pymysql://tap:tap@127.0.0.1:33319/tap_task10",
        "--object-root",
        str(tmp_path / "objects"),
        "--clickhouse-url",
        "http://127.0.0.1:38123/?database=tap_task10_insights",
        "--dry-run",
    ]
    dry_run = subprocess.run(base, capture_output=True, text=True, check=False)
    assert dry_run.returncode == 0
    assert json.loads(dry_run.stdout) == {
        "action": "dry-run",
        "composeProject": "tap-insights-rebuild-task10",
        "targetVersion": "rebuild-task10-v2",
    }

    unsafe = subprocess.run(
        [
            *base[:3],
            "default",
            *base[4:],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert unsafe.returncode == 2
    assert "owned isolated Compose project" in unsafe.stderr


def map_to_projecting(
    ledger: SqlAlchemyReportLedger,
    objects: FileReportObjectStore,
    *,
    correction_no: int,
    raw: bytes,
    attachments: frozenset[str] = frozenset(),
) -> str:
    receipt = ReportIntake(ledger=ledger, objects=objects).receive(
        manifest(correction_no=correction_no, attachments=attachments), [raw]
    )
    worker = ReportWorker(ledger=ledger, objects=objects)
    assert worker.process_one(receipt.receipt_id)
    assert worker.process_one(receipt.receipt_id)
    assert worker.process_one(receipt.receipt_id)
    assert ledger.get_receipt(receipt.receipt_id).state is ReportState.PROJECTING
    return receipt.receipt_id


def test_projection_migration_owns_version_batch_and_state_tables(
    projection_runtime,
) -> None:
    """Omitting MySQL projection authority must fail the schema contract."""
    assert MYSQL_URL is not None
    engine = create_engine(MYSQL_URL)
    try:
        assert {
            "tap_insights_projection_batches",
            "tap_insights_projection_state",
            "tap_insights_projection_versions",
        } <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_full_junit_path_replays_lost_receipt_without_duplicate_effective_facts(
    projection_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting physical duplicate rows instead of one logical fact must fail."""
    ledger, objects, store = projection_runtime
    receipt_id = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    coordinator = ProjectionCoordinator(ledger=ledger, store=store)

    def lose_receipt(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("simulated receipt loss after ClickHouse write")

    monkeypatch.setattr(ledger, "complete_projection_batch", lose_receipt)
    with pytest.raises(ConnectionError, match="receipt loss"):
        coordinator.project_receipt(receipt_id)
    assert ledger.projection_snapshot().visible_data_version == 0
    assert store.effective_attempts(ledger.projection_snapshot()) == []
    assert store.raw_attempt_count() == 2

    monkeypatch.undo()
    restarted = ProjectionCoordinator(ledger=ledger, store=store)
    assert ReportWorker(
        ledger=ledger,
        objects=objects,
        projector=restarted,
    ).process_one(receipt_id)
    snapshot = ledger.projection_snapshot()
    attempts = store.effective_attempts(snapshot)
    assert snapshot.visible_data_version == 1
    assert store.raw_attempt_count() == 4
    assert [item.result for item in attempts] == ["fail", "pass"]
    assert [item.attempt for item in attempts] == [1, 2]
    assert {item.raw_object_ref for item in attempts} == {
        ledger.get_receipt(receipt_id).raw_object_ref
    }
    assert restarted.project_receipt(receipt_id) is False
    assert ledger.get_receipt(receipt_id).state is ReportState.READY
    with pytest.raises(ValueError, match="conflicting content"):
        ledger.reserve_projection_batch(
            receipt_id=receipt_id,
            payload_checksum="0" * 64,
            row_count=2,
        )


def test_out_of_order_delete_undo_and_same_version_conflict_use_ledger_authority(
    projection_runtime,
) -> None:
    """Arrival order or a conflicting correction must never choose the visible fact."""
    ledger, objects, store = projection_runtime
    coordinator = ProjectionCoordinator(ledger=ledger, store=store)

    initial = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    assert coordinator.project_receipt(initial)
    deleted = map_to_projecting(ledger, objects, correction_no=2, raw=empty_report())
    assert coordinator.project_receipt(deleted)
    assert (
        clickhouse_admin(
            "SELECT DISTINCT is_deleted FROM report_batch_markers "
            "WHERE correction_no = 2"
        ).strip()
        == b"1"
    )
    late_lower = map_to_projecting(
        ledger, objects, correction_no=1, raw=report("success.xml")
    )
    assert coordinator.project_receipt(late_lower)
    assert store.effective_attempts(ledger.projection_snapshot()) == []

    restored = map_to_projecting(
        ledger, objects, correction_no=3, raw=report("retry.xml")
    )
    assert coordinator.project_receipt(restored)
    effective = store.effective_attempts(ledger.projection_snapshot())
    assert [(item.attempt, item.result) for item in effective] == [
        (1, "fail"),
        (2, "pass"),
    ]
    assert {item.correction_no for item in effective} == {3}

    conflict = ReportIntake(ledger=ledger, objects=objects).receive(
        manifest(correction_no=3), [report("failure.xml")]
    )
    assert conflict.state is ReportState.CONFLICTED
    with pytest.raises(ValueError, match="authoritative projecting receipt"):
        coordinator.project_receipt(conflict.receipt_id)
    assert {
        item.correction_no
        for item in store.effective_attempts(ledger.projection_snapshot())
    } == {3}


def test_incomplete_batch_never_advances_visible_watermark(
    projection_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advancing the watermark before every append succeeds must expose partial data."""
    ledger, objects, store = projection_runtime
    receipt_id = map_to_projecting(
        ledger,
        objects,
        correction_no=0,
        raw=report("retry.xml"),
    )
    coordinator = ProjectionCoordinator(ledger=ledger, store=store)
    original_append_marker = store.append_marker

    def interrupt_before_marker(_batch) -> None:
        raise RuntimeError("injected projection interruption")

    monkeypatch.setattr(store, "append_marker", interrupt_before_marker)
    with pytest.raises(RuntimeError, match="injected projection interruption"):
        coordinator.project_receipt(receipt_id)
    assert ledger.projection_snapshot().visible_data_version == 0
    assert store.effective_attempts(ledger.projection_snapshot()) == []

    monkeypatch.setattr(store, "append_marker", original_append_marker)
    later_receipt = map_to_projecting(
        ledger,
        objects,
        correction_no=1,
        raw=report("retry.xml"),
    )
    assert coordinator.project_receipt(later_receipt)
    assert ledger.projection_snapshot().visible_data_version == 0
    assert store.effective_attempts(ledger.projection_snapshot()) == []

    assert coordinator.project_receipt(receipt_id)
    snapshot = ledger.projection_snapshot()
    assert snapshot.visible_data_version == 2
    assert len(store.effective_attempts(snapshot)) == 2
    assert {item.correction_no for item in store.effective_attempts(snapshot)} == {1}
    assert store.run_dimensions(snapshot)[0].configuration == "browser=chromium"


def test_evidence_dimension_retains_authorized_reference(projection_runtime) -> None:
    """Dropping evidence refs while writing the attempt must break drilldown lineage."""
    ledger, objects, store = projection_runtime
    raw = b"""<testsuite name="evidence" tests="1">
      <testcase classname="checkout.Evidence" name="capture" time="0.2">
        <properties>
          <property name="tap.test_id" value="checkout-evidence"/>
          <property name="tap.data_row" value="evidence-row"/>
          <property name="tap.attempt" value="1"/>
          <property name="tap.attachment" value="failure-log"/>
        </properties>
      </testcase>
    </testsuite>"""
    receipt_id = map_to_projecting(
        ledger,
        objects,
        correction_no=0,
        raw=raw,
        attachments=frozenset({"failure-log"}),
    )
    assert ProjectionCoordinator(ledger=ledger, store=store).project_receipt(receipt_id)
    snapshot = ledger.projection_snapshot()
    fact = store.effective_attempts(snapshot)[0]
    assert store.evidence_for(snapshot, fact.fact_key) == ["failure-log"]


def test_rebuild_reparses_ledger_objects_and_atomically_cuts_over(
    projection_runtime,
) -> None:
    """Reusing ClickHouse rows instead of ledger objects must fail clean recovery."""
    ledger, objects, store = projection_runtime
    receipt_id = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    assert ProjectionCoordinator(ledger=ledger, store=store).project_receipt(receipt_id)
    original = ledger.projection_snapshot()
    for table_name in (
        "report_batch_markers",
        "attempt_facts",
        "evidence_refs",
        "run_dimensions",
        "configuration_dimensions",
    ):
        clickhouse_admin(f"TRUNCATE TABLE {table_name}")
    assert store.effective_attempts(original) == []

    result = ProjectionRebuilder(
        ledger=ledger,
        objects=objects,
        store=store,
    ).rebuild(target_version="task10-recovery-v2")
    active = ledger.projection_snapshot()
    assert result.row_count == 2
    assert result.oracle_checksum == result.clickhouse_checksum
    assert active.projection_version == "task10-recovery-v2"
    assert active.visible_data_version == 1
    assert len(store.effective_attempts(active)) == 2
    assert ledger.get_receipt(receipt_id).state is ReportState.READY


def test_clickhouse_backup_restores_to_clean_owned_database(projection_runtime) -> None:
    """An unreadable backup must not count as recovery evidence."""
    ledger, objects, store = projection_runtime
    receipt_id = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    assert ProjectionCoordinator(ledger=ledger, store=store).project_receipt(receipt_id)
    suffix = uuid.uuid4().hex
    backup_name = f"task10-{suffix}"
    restored_database = f"tap_task10_restored_{suffix}"
    try:
        clickhouse_admin(
            f"BACKUP DATABASE tap_insights TO Disk('insights_backups', '{backup_name}')"
        )
        clickhouse_admin(
            "RESTORE DATABASE tap_insights "
            f"AS {restored_database} "
            f"FROM Disk('insights_backups', '{backup_name}')"
        )
        restored_count = int(
            clickhouse_admin(
                f"SELECT count() FROM {restored_database}.attempt_facts"
            ).strip()
        )
        assert restored_count == 2
    finally:
        clickhouse_admin(f"DROP DATABASE IF EXISTS {restored_database}")
