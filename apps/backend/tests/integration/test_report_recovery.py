import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text

from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.intake import ReportIntake
from tap_platform.insights.domain.reports import (
    Completeness,
    ReportManifest,
    ReportState,
)
from tap_platform.insights.worker import ReportWorker


MYSQL_URL = os.getenv("TAP_TASK9_MYSQL_URL")
pytestmark = pytest.mark.skipif(
    not MYSQL_URL,
    reason="requires the explicitly isolated TAP_TASK9_MYSQL_URL database",
)


def manifest() -> ReportManifest:
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
        correction_no=0,
        attachments=frozenset(),
    )


def fixture_report() -> bytes:
    return (
        Path(__file__).parents[1] / "fixtures" / "insights" / "reports" / "retry.xml"
    ).read_bytes()


@pytest.fixture
def migrated_mysql(tmp_path: Path):
    assert MYSQL_URL is not None
    env = dict(os.environ, TAP_DATABASE_URL=MYSQL_URL)
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
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(MYSQL_URL)
    with engine.begin() as connection:
        for table in (
            "tap_report_attempts",
            "tap_report_transitions",
            "tap_report_outbox",
            "tap_report_receipts",
            "tap_report_identity_claims",
        ):
            connection.execute(text(f"DELETE FROM {table}"))
    yield engine, FileReportObjectStore(tmp_path / "objects")
    engine.dispose()


def test_migration_owns_the_receipt_attempt_and_outbox_schema(migrated_mysql) -> None:
    """Omitting independent TAP migration objects must fail this schema assertion."""
    engine, _ = migrated_mysql
    tables = set(inspect(engine).get_table_names())
    assert {
        "alembic_version",
        "tap_report_attempts",
        "tap_report_identity_claims",
        "tap_report_outbox",
        "tap_report_receipts",
        "tap_report_transitions",
    } <= tables


def test_worker_restart_resumes_each_durable_state_without_rerunning_tests(
    migrated_mysql,
) -> None:
    """Keeping processing state only in worker memory must strand this receipt."""
    engine, objects = migrated_mysql
    ledger = SqlAlchemyReportLedger(engine)
    receipt = ReportIntake(ledger=ledger, objects=objects).receive(
        manifest(), [fixture_report()]
    )
    assert receipt.state is ReportState.RECEIVED
    assert receipt.completeness is Completeness.COMPLETE

    assert ReportWorker(ledger=ledger, objects=objects).process_one(receipt.receipt_id)
    assert ledger.get_receipt(receipt.receipt_id).state is ReportState.VALIDATING

    restarted_ledger = SqlAlchemyReportLedger(create_engine(MYSQL_URL))
    restarted_worker = ReportWorker(ledger=restarted_ledger, objects=objects)
    assert restarted_worker.process_one(receipt.receipt_id)
    assert restarted_ledger.get_receipt(receipt.receipt_id).state is ReportState.MAPPED
    assert len(restarted_ledger.attempts_for(receipt.receipt_id)) == 2

    assert restarted_worker.process_one(receipt.receipt_id)
    assert (
        restarted_ledger.get_receipt(receipt.receipt_id).state is ReportState.PROJECTING
    )
    assert restarted_worker.process_one(receipt.receipt_id)
    ready = restarted_ledger.get_receipt(receipt.receipt_id)
    assert ready.state is ReportState.READY
    assert restarted_ledger.outbox_events(receipt.receipt_id) == [
        "report.received",
        "report.validating",
        "report.mapped",
        "report.projecting",
        "report.ready",
    ]


def test_unreferenced_raw_object_is_recovered_after_ledger_failure(
    migrated_mysql, monkeypatch
) -> None:
    """Acknowledging or retaining an object after ledger failure must fail recovery."""
    engine, objects = migrated_mysql
    ledger = SqlAlchemyReportLedger(engine)
    intake = ReportIntake(ledger=ledger, objects=objects)

    def fail_after_persist(*args, **kwargs):
        raise RuntimeError("simulated-ledger-outage")

    monkeypatch.setattr(ledger, "accept", fail_after_persist)
    with pytest.raises(RuntimeError, match="simulated-ledger-outage"):
        intake.receive(manifest(), [fixture_report()])
    assert len(objects.list_raw_objects()) == 1

    recovered = objects.recover_orphans(
        referenced=SqlAlchemyReportLedger(engine).referenced_raw_objects()
    )
    assert len(recovered) == 1
    assert objects.list_raw_objects() == []


def test_concurrent_identity_claim_has_one_canonical_receipt(migrated_mysql) -> None:
    """A check-then-insert race must not accept two contents as canonical."""
    engine, objects = migrated_mysql

    def receive(stable_id: str):
        raw = (
            "<testsuite><testcase classname='suite' name='same'>"
            "<properties><property name='tap.test_id' "
            f"value='{stable_id}'/></properties></testcase></testsuite>"
        ).encode()
        return ReportIntake(
            ledger=SqlAlchemyReportLedger(create_engine(MYSQL_URL)),
            objects=objects,
        ).receive(manifest(), [raw])

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(receive, ("content-a", "content-b")))

    assert {item.state for item in receipts} == {
        ReportState.RECEIVED,
        ReportState.CONFLICTED,
    }
    canonical = next(item for item in receipts if item.state is ReportState.RECEIVED)
    conflict = next(item for item in receipts if item.state is ReportState.CONFLICTED)
    assert conflict.conflict_with_receipt_id == canonical.receipt_id


def test_concurrent_identical_delivery_returns_one_receipt(migrated_mysql) -> None:
    """Dropping the identity/content uniqueness must duplicate a lost-response retry."""
    engine, objects = migrated_mysql

    def receive(_index: int):
        return ReportIntake(
            ledger=SqlAlchemyReportLedger(create_engine(MYSQL_URL)),
            objects=objects,
        ).receive(manifest(), [fixture_report()])

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(receive, (1, 2)))

    assert receipts[0].receipt_id == receipts[1].receipt_id
    ledger = SqlAlchemyReportLedger(engine)
    assert ledger.count_receipts() == 1
    assert ledger.outbox_events(receipts[0].receipt_id) == ["report.received"]


def test_receipt_and_outbox_rollback_together(migrated_mysql) -> None:
    """Committing the receipt before its outbox event must leave a partial ledger."""
    engine, objects = migrated_mysql

    def reject_outbox(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO tap_report_outbox"):
            raise RuntimeError("simulated outbox failure")

    event.listen(engine, "before_cursor_execute", reject_outbox)
    try:
        with pytest.raises(RuntimeError, match="simulated outbox failure"):
            ReportIntake(
                ledger=SqlAlchemyReportLedger(engine), objects=objects
            ).receive(manifest(), [fixture_report()])
    finally:
        event.remove(engine, "before_cursor_execute", reject_outbox)

    assert SqlAlchemyReportLedger(engine).count_receipts() == 0
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM tap_report_identity_claims")
            ).scalar_one()
            == 0
        )
