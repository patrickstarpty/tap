from __future__ import annotations

import os
import json
import base64
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import re
from dataclasses import replace
from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

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
from tap_platform.insights.application.queries import (
    InsightsQueryService,
    MetricQuery,
    QueryFilters,
    QueryLimits,
)
from tap_platform.insights.domain.metrics import MetricId


MYSQL_URL = os.getenv("TAP_TASK10_MYSQL_URL")
CLICKHOUSE_URL = os.getenv("TAP_TASK10_CLICKHOUSE_URL")
CLICKHOUSE_USER = os.getenv("TAP_TASK10_CLICKHOUSE_USER", "tap_insights_writer")
CLICKHOUSE_PASSWORD = os.getenv("TAP_TASK10_CLICKHOUSE_PASSWORD")
CLICKHOUSE_ADMIN_USER = os.getenv("TAP_TASK10_CLICKHOUSE_ADMIN_USER")
CLICKHOUSE_ADMIN_PASSWORD = os.getenv("TAP_TASK10_CLICKHOUSE_ADMIN_PASSWORD")
COMPOSE_PROJECT = os.getenv("TAP_TASK10_COMPOSE_PROJECT")
REPOSITORY_ROOT = Path(__file__).parents[4]


def assert_owned_isolated_runtime() -> None:
    """Fail closed before tests execute any destructive database cleanup."""
    if (
        COMPOSE_PROJECT is None
        or re.fullmatch(r"tap-insights-[a-z0-9][a-z0-9_-]{2,48}", COMPOSE_PROJECT)
        is None
    ):
        raise RuntimeError("Task 10 tests require an owned isolated Compose project")
    assert MYSQL_URL is not None
    assert CLICKHOUSE_URL is not None
    mysql = make_url(MYSQL_URL)
    clickhouse = urllib.parse.urlsplit(CLICKHOUSE_URL)
    clickhouse_database = urllib.parse.parse_qs(clickhouse.query).get("database")
    if (
        mysql.host != "127.0.0.1"
        or mysql.port in {None, 3306}
        or mysql.database is None
        or not mysql.database.startswith("tap_task10_")
        or clickhouse.hostname != "127.0.0.1"
        or clickhouse.port in {None, 8123}
        or clickhouse_database != ["tap_insights"]
    ):
        raise RuntimeError("Task 10 tests require loopback-only dedicated databases")

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            COMPOSE_PROJECT,
            "-f",
            str(REPOSITORY_ROOT / "compose.yaml"),
            "ps",
            "-q",
            "mysql",
            "clickhouse",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    container_ids = [item for item in result.stdout.splitlines() if item]
    if len(container_ids) != 2:
        raise RuntimeError("owned Task 10 Compose services are not both running")
    inspected = json.loads(
        subprocess.run(
            ["docker", "inspect", *container_ids],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    expected_ports = {
        "mysql": ("3306/tcp", mysql.port),
        "clickhouse": ("8123/tcp", clickhouse.port),
    }
    for container in inspected:
        labels = container["Config"]["Labels"]
        service = labels.get("com.docker.compose.service")
        if (
            labels.get("com.docker.compose.project") != COMPOSE_PROJECT
            or labels.get("com.docker.compose.project.working_dir")
            != str(REPOSITORY_ROOT)
            or service not in expected_ports
        ):
            raise RuntimeError("database container ownership does not match the test")
        container_port, host_port = expected_ports[service]
        bindings = container["NetworkSettings"]["Ports"].get(container_port) or []
        if not any(
            binding.get("HostIp") == "127.0.0.1"
            and int(binding.get("HostPort", 0)) == host_port
            for binding in bindings
        ):
            raise RuntimeError("database port does not match the owned Compose service")


def clickhouse_admin(sql: str, *, database: str | None = None) -> bytes:
    assert CLICKHOUSE_URL is not None
    assert CLICKHOUSE_ADMIN_USER is not None
    assert CLICKHOUSE_ADMIN_PASSWORD is not None
    parsed = urllib.parse.urlsplit(CLICKHOUSE_URL)
    query = urllib.parse.parse_qs(parsed.query)
    query["query"] = [sql]
    if database is not None:
        query["database"] = [database]
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


def manifest(
    *,
    correction_no: int = 0,
    attachments: frozenset[str] = frozenset(),
    configuration: str = "browser=chromium",
):
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
        configuration=configuration,
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
    assert_owned_isolated_runtime()
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
            "tap_insights_queries",
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
        "mysql+pymysql://tap:tap@127.0.0.1:33319/tap_task10_rebuild",
        "--object-root",
        str(tmp_path / "objects"),
        "--clickhouse-url",
        "http://127.0.0.1:38123/?database=tap_insights",
        "--dry-run",
    ]
    dry_run = subprocess.run(base, capture_output=True, text=True, check=False)
    assert dry_run.returncode == 0
    assert json.loads(dry_run.stdout) == {
        "action": "dry-run",
        "composeProject": "tap-insights-rebuild-task10",
        "targetVersion": "rebuild-task10-v2",
    }

    local_stack = base.copy()
    local_stack[3] = "tap-insights-local"
    local_stack[7] = "mysql+pymysql://tap:tap@127.0.0.1:23306/tap"
    assert (
        subprocess.run(
            local_stack, capture_output=True, text=True, check=False
        ).returncode
        == 2
    )

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
    configuration: str = "browser=chromium",
) -> str:
    receipt = ReportIntake(ledger=ledger, objects=objects).receive(
        manifest(
            correction_no=correction_no,
            attachments=attachments,
            configuration=configuration,
        ),
        [raw],
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


def test_mysql_data_bearing_downgrade_retains_query_projection_and_report(
    projection_runtime,
):
    ledger, objects, store = projection_runtime
    base = replace(manifest(), started_at="2026-09-24T00:00:00+00:00")
    receipt = project_manifest(ledger, objects, store, base)
    record = query_service(ledger, store).execute(
        project_id=base.project_id, query=metric_query()
    )
    command = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(REPOSITORY_ROOT / "apps/backend/alembic.ini"),
    ]
    environment = dict(os.environ, TAP_DATABASE_URL=MYSQL_URL)

    def migrate(action, target):
        return subprocess.run(
            [*command, action, target],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )

    result = migrate("downgrade", "base")
    assert (
        result.returncode != 0 and "query history requires retention" in result.stderr
    )
    assert ledger.get_query(record.query_id).metrics == record.metrics
    engine = create_engine(MYSQL_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM tap_insights_queries"))
        assert migrate("downgrade", "0003_insights_projection").returncode == 0
        result = migrate("downgrade", "0002_manifest_fingerprint")
        assert (
            result.returncode != 0
            and "projection authority requires retention" in result.stderr
        )
        assert ledger.projection_snapshot().visible_data_version == 1
        with engine.begin() as connection:
            for table in (
                "tap_insights_projection_batches",
                "tap_insights_projection_state",
                "tap_insights_projection_versions",
            ):
                connection.execute(text(f"DELETE FROM {table}"))
        assert migrate("downgrade", "0001_report_intake").returncode == 0
        result = migrate("downgrade", "base")
        assert (
            result.returncode != 0
            and "report authority requires retention" in result.stderr
        )
        assert migrate("upgrade", "head").returncode == 0
        assert ledger.get_receipt(receipt.receipt_id).checksum == receipt.checksum
        assert objects.read(receipt.raw_object_ref) == report("retry.xml")
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


def test_correction_replaces_an_incorrect_configuration_dimension(
    projection_runtime,
) -> None:
    """Scoping correction selection by configuration must leave stale facts visible."""
    ledger, objects, store = projection_runtime
    coordinator = ProjectionCoordinator(ledger=ledger, store=store)
    original = map_to_projecting(
        ledger,
        objects,
        correction_no=0,
        raw=report("retry.xml"),
        configuration="browser=chromium",
    )
    assert coordinator.project_receipt(original)
    corrected = map_to_projecting(
        ledger,
        objects,
        correction_no=1,
        raw=report("retry.xml"),
        configuration="browser=firefox",
    )
    assert coordinator.project_receipt(corrected)

    effective = store.effective_attempts(ledger.projection_snapshot())
    assert len(effective) == 2
    assert {item.configuration for item in effective} == {"browser=firefox"}


def query_service(ledger, store):
    return InsightsQueryService(
        facts=store,
        snapshots=lambda _as_of: ledger.projection_snapshot(),
        history=ledger,
        clock=lambda: datetime.now(UTC),
        limits=QueryLimits(
            max_rows_to_read=10000,
            max_bytes_to_read=5000000,
            max_memory_bytes=5000000,
            max_concurrent_queries=1,
            max_output_rows=100,
            timeout_seconds=10,
        ),
    )


def metric_query(**filters):
    return MetricQuery(
        metric_ids=tuple(MetricId),
        filters=QueryFilters(**filters),
        from_date="2026-09-23",
        to_date="2026-09-27",
        timezone="UTC",
        as_of=datetime.now(UTC),
    )


def project_manifest(ledger, objects, store, report_manifest, raw=None):
    receipt = ReportIntake(ledger=ledger, objects=objects).receive(
        report_manifest, [raw if raw is not None else report("retry.xml")]
    )
    worker = ReportWorker(
        ledger=ledger,
        objects=objects,
        projector=ProjectionCoordinator(ledger=ledger, store=store),
    )
    for _ in range(4):
        worker.process_one(receipt.receipt_id)
    return ledger.get_receipt(receipt.receipt_id)


def test_correction_winner_is_selected_before_mutable_filters_and_deletion(
    projection_runtime,
):
    ledger, objects, store = projection_runtime
    base = replace(manifest(), started_at="2026-09-24T00:00:00+00:00")
    project_manifest(ledger, objects, store, base)
    old = ledger.projection_snapshot()
    project_manifest(
        ledger,
        objects,
        store,
        replace(
            base, correction_no=1, configuration="browser=firefox", environment="new"
        ),
    )
    query = metric_query(configurations=("browser=chromium",))
    assert (
        store.effective_attempts(
            ledger.projection_snapshot(),
            project_id=base.project_id,
            query=query,
            window=query.window(),
        )
        == []
    )
    assert (
        len(
            store.effective_attempts(
                old, project_id=base.project_id, query=query, window=query.window()
            )
        )
        == 2
    )
    project_manifest(
        ledger,
        objects,
        store,
        replace(base, correction_no=2, environment="deleted"),
        b"<testsuite/>",
    )
    assert (
        store.effective_attempts(
            ledger.projection_snapshot(),
            project_id=base.project_id,
            query=metric_query(environments=("new",)),
        )
        == []
    )


def test_cross_batch_same_logical_attempt_is_idempotent_and_conflicts_are_durable(
    projection_runtime,
):
    ledger, objects, store = projection_runtime
    base = replace(manifest(), started_at="2026-09-24T00:00:00+00:00")
    original = project_manifest(ledger, objects, store, base)
    project_manifest(
        ledger,
        objects,
        store,
        replace(base, batch_id="other-batch", shard_id="other-shard"),
    )
    assert len(store.effective_attempts(ledger.projection_snapshot())) == 2
    metrics = (
        query_service(ledger, store)
        .execute(project_id=base.project_id, query=metric_query())
        .metrics
    )
    assert (
        next(
            item for item in metrics if item.metric_id is MetricId.P95_DURATION_SECONDS
        ).value
        == 1.7
    )
    raw = report("retry.xml").replace(b'time="0.7"', b'time="9.7"')
    assert raw != report("retry.xml")
    conflict = project_manifest(
        ledger, objects, store, replace(base, batch_id="conflict-batch"), raw
    )
    assert conflict.state is ReportState.CONFLICTED
    assert conflict.conflict_with_receipt_id == original.receipt_id
    assert len(store.effective_attempts(ledger.projection_snapshot())) == 2
    project_manifest(
        ledger, objects, store, replace(base, correction_no=1), b"<testsuite/>"
    )
    assert store.effective_attempts(ledger.projection_snapshot()) == []
    corrected = project_manifest(
        ledger,
        objects,
        store,
        replace(base, batch_id="correction-batch", correction_no=2),
        raw,
    )
    assert corrected.state is ReportState.READY
    assert len(store.effective_attempts(ledger.projection_snapshot())) == 2
    result = ProjectionRebuilder(ledger=ledger, objects=objects, store=store).rebuild(
        target_version="rebuild-cross-batch"
    )
    assert result.row_count == 2


def test_concurrent_cross_batch_mapping_serializes_logical_content_authority(
    projection_runtime,
):
    ledger, objects, store = projection_runtime
    base = replace(manifest(), started_at="2026-09-24T00:00:00+00:00")
    intake = ReportIntake(ledger=ledger, objects=objects)
    first = intake.receive(base, [report("retry.xml")])
    second = intake.receive(
        replace(base, batch_id="concurrent-batch"),
        [report("retry.xml").replace(b'time="0.7"', b'time="9.7"')],
    )
    worker = ReportWorker(
        ledger=ledger,
        objects=objects,
        projector=ProjectionCoordinator(ledger=ledger, store=store),
    )
    for item in (first, second):
        worker.process_one(item.receipt_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert all(
            executor.map(worker.process_one, (first.receipt_id, second.receipt_id))
        )
    receipts = [ledger.get_receipt(item.receipt_id) for item in (first, second)]
    assert {item.state for item in receipts} == {
        ReportState.MAPPED,
        ReportState.CONFLICTED,
    }
    winner = next(item for item in receipts if item.state is ReportState.MAPPED)
    loser = next(item for item in receipts if item.state is ReportState.CONFLICTED)
    assert loser.conflict_with_receipt_id == winner.receipt_id
    worker.process_one(winner.receipt_id)
    worker.process_one(winner.receipt_id)
    assert len(store.effective_attempts(ledger.projection_snapshot())) == 2


@pytest.mark.parametrize("expected", [2, None])
def test_query_completeness_is_frozen_at_visible_projection_watermark(
    projection_runtime, expected
):
    ledger, objects, store = projection_runtime
    base = replace(
        manifest(), expected_shards=expected, started_at="2026-09-24T00:00:00+00:00"
    )
    project_manifest(ledger, objects, store, base)
    service = query_service(ledger, store)
    historical = service.execute(project_id=base.project_id, query=metric_query())
    assert all(
        item.completeness == "unavailable" and item.value is None
        for item in historical.metrics
    )
    coverage = historical.report_coverage[0]
    assert (
        coverage.expected_shards,
        coverage.received_shards,
        coverage.completeness,
    ) == (expected, 1, "partial" if expected else "unknown")
    if expected:
        # Merely receiving the second shard cannot complete the projected snapshot.
        receipt = ReportIntake(ledger=ledger, objects=objects).receive(
            replace(base, shard_id="2"), [report("success.xml")]
        )
        assert all(
            item.value is None
            for item in service.execute(
                project_id=base.project_id, query=metric_query()
            ).metrics
        )
        worker = ReportWorker(
            ledger=ledger,
            objects=objects,
            projector=ProjectionCoordinator(ledger=ledger, store=store),
        )
        for _ in range(4):
            worker.process_one(receipt.receipt_id)
        current = service.execute(project_id=base.project_id, query=metric_query())
        assert current.report_coverage[0].completeness == "complete"
        assert current.metrics[0].completeness == "complete"
    reopened = ledger.get_query(historical.query_id)
    assert reopened.metrics == historical.metrics
    assert reopened.report_coverage == historical.report_coverage


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
    corrected = map_to_projecting(
        ledger,
        objects,
        correction_no=1,
        raw=raw.replace(b"failure-log", b"updated-log"),
        attachments=frozenset({"updated-log"}),
    )
    assert ProjectionCoordinator(ledger=ledger, store=store).project_receipt(corrected)
    assert store.evidence_for(ledger.projection_snapshot(), fact.fact_key) == [
        "updated-log"
    ]
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


def test_rebuild_excludes_complete_batches_beyond_the_visible_watermark(
    projection_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete batch behind an incomplete gap must not enter or permit cutover."""
    ledger, objects, store = projection_runtime
    coordinator = ProjectionCoordinator(ledger=ledger, store=store)
    blocked_receipt = map_to_projecting(
        ledger,
        objects,
        correction_no=0,
        raw=report("retry.xml"),
    )
    original_append_marker = store.append_marker

    def interrupt_before_marker(_batch) -> None:
        raise RuntimeError("injected projection interruption")

    monkeypatch.setattr(store, "append_marker", interrupt_before_marker)
    with pytest.raises(RuntimeError, match="injected projection interruption"):
        coordinator.project_receipt(blocked_receipt)
    monkeypatch.setattr(store, "append_marker", original_append_marker)

    hidden_receipt = map_to_projecting(
        ledger,
        objects,
        correction_no=1,
        raw=report("retry.xml"),
    )
    assert coordinator.project_receipt(hidden_receipt)
    source = ledger.projection_snapshot()
    assert source.visible_data_version == 0
    assert store.effective_attempts(source) == []

    with pytest.raises(ValueError, match="incomplete source projection batches"):
        ProjectionRebuilder(
            ledger=ledger,
            objects=objects,
            store=store,
        ).rebuild(target_version="task10-gap-recovery-v2")
    target = ledger.projection_snapshot("task10-gap-recovery-v2")
    assert target.visible_data_version == 0
    assert store.effective_attempts(target) == []
    assert ledger.projection_snapshot() == source


def test_cutover_cannot_overtake_in_flight_source_completion(
    projection_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Activation queued first must still reject a reserved source batch."""
    ledger, objects, store = projection_runtime
    coordinator = ProjectionCoordinator(ledger=ledger, store=store)
    visible_receipt = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    assert coordinator.project_receipt(visible_receipt)
    source = ledger.projection_snapshot()

    target_version = "task10-concurrent-v2"
    ledger.create_projection_version(target_version)
    ready_receipt = ledger.get_receipt(visible_receipt)
    coordinator._project(
        receipt=ready_receipt,
        manifest=ledger.get_manifest(visible_receipt),
        attempts=ledger.attempts_for(visible_receipt),
        projection_version=target_version,
        mark_receipt_ready=False,
    )
    target = ledger.projection_snapshot(target_version)
    target_attempts = store.effective_attempts(target)
    target_checksum = store.effective_checksum(target)

    blocked_receipt = map_to_projecting(
        ledger, objects, correction_no=1, raw=report("retry.xml")
    )
    original_append_marker = store.append_marker

    def interrupt_before_marker(_batch) -> None:
        raise RuntimeError("injected projection interruption")

    monkeypatch.setattr(store, "append_marker", interrupt_before_marker)
    with pytest.raises(RuntimeError, match="injected projection interruption"):
        coordinator.project_receipt(blocked_receipt)
    monkeypatch.setattr(store, "append_marker", original_append_marker)

    assert MYSQL_URL is not None
    lock_engine = create_engine(MYSQL_URL)
    lock_connection = lock_engine.connect()
    lock_transaction = lock_connection.begin()
    lock_connection.execute(
        text(
            "SELECT projection_version FROM tap_insights_projection_versions "
            "WHERE projection_version = 'insights-v1' FOR UPDATE"
        )
    )
    activation_result: dict[str, object] = {}
    completion_result: dict[str, object] = {}

    def activate() -> None:
        try:
            ledger.activate_projection_version(
                target_version,
                row_count=len(target_attempts),
                checksum=target_checksum,
                expected_active_projection_version=source.projection_version,
                expected_visible_data_version=source.visible_data_version,
            )
        except Exception as exc:  # noqa: BLE001 - assertion captures thread result
            activation_result["error"] = exc

    def complete() -> None:
        try:
            completion_result["changed"] = coordinator.project_receipt(blocked_receipt)
        except Exception as exc:  # noqa: BLE001 - assertion captures thread result
            completion_result["error"] = exc

    activation_thread = threading.Thread(target=activate)
    completion_thread = threading.Thread(target=complete)
    try:
        activation_thread.start()
        time.sleep(0.1)
        completion_thread.start()
        time.sleep(0.1)
        assert activation_thread.is_alive()
        assert completion_thread.is_alive()
    finally:
        lock_transaction.rollback()
        lock_connection.close()
        lock_engine.dispose()
    activation_thread.join(timeout=10)
    completion_thread.join(timeout=10)

    assert isinstance(activation_result.get("error"), ValueError)
    assert "incomplete source projection batches" in str(activation_result["error"])
    assert completion_result == {"changed": True}
    assert ledger.projection_snapshot().projection_version == "insights-v1"
    assert ledger.get_receipt(blocked_receipt).state is ReportState.READY


def test_rebuild_rejects_tampered_raw_object(projection_runtime) -> None:
    """A parseable altered object must never become its own rebuild oracle."""
    ledger, objects, store = projection_runtime
    receipt_id = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    assert ProjectionCoordinator(ledger=ledger, store=store).project_receipt(receipt_id)
    receipt = ledger.get_receipt(receipt_id)
    (objects.root / receipt.raw_object_ref).write_bytes(empty_report())

    with pytest.raises(RuntimeError, match="raw object integrity mismatch"):
        ProjectionRebuilder(
            ledger=ledger,
            objects=objects,
            store=store,
        ).rebuild(target_version="task10-tampered-v2")
    assert ledger.projection_snapshot().projection_version == "insights-v1"


def test_rebuild_rejects_unknown_recorded_parser_version(projection_runtime) -> None:
    """Rebuild must not silently reinterpret a receipt with another parser."""
    ledger, objects, store = projection_runtime
    receipt_id = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    assert ProjectionCoordinator(ledger=ledger, store=store).project_receipt(receipt_id)
    assert MYSQL_URL is not None
    engine = create_engine(MYSQL_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tap_report_receipts "
                    "SET parser_version = 'unknown-v9' "
                    "WHERE receipt_id = :receipt_id"
                ),
                {"receipt_id": receipt_id},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="unsupported recorded parser version"):
        ProjectionRebuilder(
            ledger=ledger,
            objects=objects,
            store=store,
        ).rebuild(target_version="task10-parser-v2")
    assert ledger.projection_snapshot().projection_version == "insights-v1"


def test_clickhouse_backup_restores_after_owned_data_volume_loss(
    projection_runtime,
) -> None:
    """Recovery must use the separate backup volume after clean data-volume loss."""
    ledger, objects, store = projection_runtime
    receipt_id = map_to_projecting(
        ledger, objects, correction_no=0, raw=report("retry.xml")
    )
    assert ProjectionCoordinator(ledger=ledger, store=store).project_receipt(receipt_id)
    suffix = uuid.uuid4().hex
    backup_name = f"task10-{suffix}"
    clickhouse_admin(
        f"BACKUP DATABASE tap_insights TO Disk('insights_backups', '{backup_name}')"
    )
    assert COMPOSE_PROJECT is not None
    compose = [
        "docker",
        "compose",
        "-p",
        COMPOSE_PROJECT,
        "-f",
        str(REPOSITORY_ROOT / "compose.yaml"),
        "--profile",
        "insights",
    ]
    subprocess.run([*compose, "stop", "clickhouse"], check=True)
    subprocess.run([*compose, "rm", "-f", "clickhouse"], check=True)
    data_volume = f"{COMPOSE_PROJECT}_clickhouse-insights-data"
    volume = json.loads(
        subprocess.run(
            ["docker", "volume", "inspect", data_volume],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]
    if volume["Labels"].get("com.docker.compose.project") != COMPOSE_PROJECT:
        raise RuntimeError("refusing to remove an unowned ClickHouse data volume")
    subprocess.run(["docker", "volume", "rm", data_volume], check=True)
    subprocess.run(
        [*compose, "up", "-d", "--wait", "--wait-timeout", "180", "clickhouse"],
        check=True,
    )
    clickhouse_admin("DROP DATABASE tap_insights")
    clickhouse_admin(
        "RESTORE DATABASE tap_insights AS tap_insights "
        f"FROM Disk('insights_backups', '{backup_name}')",
        database="default",
    )
    assert int(clickhouse_admin("SELECT count() FROM tap_insights.attempt_facts")) == 2
