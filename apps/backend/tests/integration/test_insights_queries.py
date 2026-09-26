from __future__ import annotations

import base64
import json
import os
import re
import secrets
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tap_platform.access import AccessPrincipal
from tap_platform.app import create_app
from tap_platform.insights.adapters.clickhouse import ClickHouseInsightsStore
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.intake import ReportIntake
from tap_platform.insights.application.projection import ProjectionCoordinator
from tap_platform.insights.application.queries import (
    InsightsQueryService,
    MetricQuery,
    QueryFilters,
    QueryLimitExceeded,
    QueryLimits,
)
from tap_platform.insights.domain.metrics import MetricId
from tap_platform.insights.domain.reports import ReportManifest
from tap_platform.insights.http import BearerPrincipalAuthorizer
from tap_platform.insights.worker import ReportWorker


MYSQL_URL = os.getenv("TAP_TASK11_MYSQL_URL")
CLICKHOUSE_URL = os.getenv("TAP_TASK11_CLICKHOUSE_URL")
CLICKHOUSE_WRITER_USER = os.getenv("TAP_TASK11_CLICKHOUSE_WRITER_USER")
CLICKHOUSE_WRITER_PASSWORD = os.getenv("TAP_TASK11_CLICKHOUSE_WRITER_PASSWORD")
CLICKHOUSE_READER_USER = os.getenv("TAP_TASK11_CLICKHOUSE_READER_USER")
CLICKHOUSE_READER_PASSWORD = os.getenv("TAP_TASK11_CLICKHOUSE_READER_PASSWORD")
CLICKHOUSE_ADMIN_USER = os.getenv("TAP_TASK11_CLICKHOUSE_ADMIN_USER")
CLICKHOUSE_ADMIN_PASSWORD = os.getenv("TAP_TASK11_CLICKHOUSE_ADMIN_PASSWORD")
COMPOSE_PROJECT = os.getenv("TAP_TASK11_COMPOSE_PROJECT")
REPOSITORY_ROOT = Path(__file__).parents[4]


def require_owned_runtime() -> None:
    if (
        COMPOSE_PROJECT is None
        or re.fullmatch(
            r"tap-insights-task11-[a-z0-9][a-z0-9_-]{2,32}", COMPOSE_PROJECT
        )
        is None
    ):
        raise RuntimeError("Task 11 requires an owned Compose project")
    assert MYSQL_URL is not None
    assert CLICKHOUSE_URL is not None
    mysql = make_url(MYSQL_URL)
    clickhouse = urllib.parse.urlsplit(CLICKHOUSE_URL)
    if (
        mysql.host != "127.0.0.1"
        or mysql.port in {None, 3306}
        or mysql.database is None
        or not mysql.database.startswith("tap_task11_")
        or clickhouse.hostname != "127.0.0.1"
        or clickhouse.port in {None, 8123}
    ):
        raise RuntimeError("Task 11 databases must be dedicated loopback targets")
    container_ids = subprocess.run(
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
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if len(container_ids) != 2:
        raise RuntimeError("owned Task 11 services are not both running")
    inspected = json.loads(
        subprocess.run(
            ["docker", "inspect", *container_ids],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    for container in inspected:
        labels = container["Config"]["Labels"]
        if labels.get("com.docker.compose.project") != COMPOSE_PROJECT or labels.get(
            "com.docker.compose.project.working_dir"
        ) != str(REPOSITORY_ROOT):
            raise RuntimeError("Task 11 service ownership mismatch")


def clickhouse_admin(sql: str) -> None:
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
        response.read()


def manifest(*, correction_no: int = 0) -> ReportManifest:
    return ReportManifest(
        project_id="project-a",
        source_id="ci-a",
        external_run_id="run-a",
        batch_id="batch-a",
        shard_id="1",
        expected_shards=1,
        contains_complete_attempts=True,
        application_commit="app-1",
        script_commit="script-1",
        environment="qa",
        configuration="browser=chromium",
        timezone="Asia/Shanghai",
        correction_no=correction_no,
        started_at="2026-09-24T08:00:00+08:00",
        finished_at="2026-09-24T08:01:00+08:00",
    )


def report(*, corrected: bool = False) -> bytes:
    return f"""<testsuite name="oracle" tests="6">
      <testcase classname="same.title" name="case" time="0.1"><properties>
        <property name="tap.test_id" value="a"/><property name="tap.attempt" value="1"/>
      </properties></testcase>
      <testcase classname="same.title" name="case" time="0.1"><properties>
        <property name="tap.test_id" value="b"/><property name="tap.attempt" value="1"/>
      </properties><failure message="first failure"/></testcase>
      <testcase classname="same.title" name="case" time="0.1"><properties>
        <property name="tap.test_id" value="b"/><property name="tap.attempt" value="2"/>
      </properties>{'<failure message="corrected failure"/>' if corrected else ""}</testcase>
      <testcase classname="same.title" name="case" time="0.1"><properties>
        <property name="tap.test_id" value="c"/><property name="tap.attempt" value="1"/>
      </properties><error message="execution error"/></testcase>
      <testcase classname="same.title" name="case" time="0.1"><properties>
        <property name="tap.test_id" value="d"/><property name="tap.attempt" value="1"/>
      </properties><skipped/></testcase>
    </testsuite>""".encode()


@pytest.fixture
def runtime(tmp_path: Path):
    required = (
        MYSQL_URL,
        CLICKHOUSE_URL,
        CLICKHOUSE_WRITER_USER,
        CLICKHOUSE_WRITER_PASSWORD,
        CLICKHOUSE_READER_USER,
        CLICKHOUSE_READER_PASSWORD,
        CLICKHOUSE_ADMIN_USER,
        CLICKHOUSE_ADMIN_PASSWORD,
    )
    if not all(required):
        pytest.skip("requires explicitly isolated Task 11 MySQL and ClickHouse")
    require_owned_runtime()
    assert MYSQL_URL is not None
    environment = dict(os.environ, TAP_DATABASE_URL=MYSQL_URL)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(REPOSITORY_ROOT / "apps/backend/alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=REPOSITORY_ROOT / "apps/backend",
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
    for table_name in (
        "report_batch_markers",
        "attempt_facts",
        "evidence_refs",
        "run_dimensions",
        "configuration_dimensions",
    ):
        clickhouse_admin(f"TRUNCATE TABLE {table_name}")
    assert CLICKHOUSE_URL is not None
    assert CLICKHOUSE_WRITER_USER is not None
    assert CLICKHOUSE_WRITER_PASSWORD is not None
    assert CLICKHOUSE_READER_USER is not None
    assert CLICKHOUSE_READER_PASSWORD is not None
    ledger = SqlAlchemyReportLedger(engine)
    objects = FileReportObjectStore(tmp_path / "objects")
    writer = ClickHouseInsightsStore(
        CLICKHOUSE_URL,
        username=CLICKHOUSE_WRITER_USER,
        password=CLICKHOUSE_WRITER_PASSWORD,
    )
    reader = ClickHouseInsightsStore(
        CLICKHOUSE_URL,
        username=CLICKHOUSE_READER_USER,
        password=CLICKHOUSE_READER_PASSWORD,
    )
    try:
        yield ledger, objects, writer, reader
    finally:
        engine.dispose()


def project(
    ledger: SqlAlchemyReportLedger,
    objects: FileReportObjectStore,
    writer: ClickHouseInsightsStore,
    *,
    correction_no: int,
    corrected: bool,
) -> str:
    receipt = ReportIntake(ledger=ledger, objects=objects).receive(
        manifest(correction_no=correction_no), [report(corrected=corrected)]
    )
    worker = ReportWorker(ledger=ledger, objects=objects)
    for _ in range(3):
        assert worker.process_one(receipt.receipt_id)
    assert ProjectionCoordinator(ledger=ledger, store=writer).project_receipt(
        receipt.receipt_id
    )
    return receipt.receipt_id


def limits(*, rows: int = 1000, concurrency: int = 2) -> QueryLimits:
    return QueryLimits(
        max_rows_to_read=rows,
        max_bytes_to_read=1_000_000,
        max_memory_bytes=2_000_000,
        max_concurrent_queries=concurrency,
        max_output_rows=100,
        max_output_bytes=1_000_000,
        timeout_seconds=2,
    )


def test_real_query_correction_history_limits_auth_and_evidence(runtime) -> None:
    ledger, objects, writer, reader = runtime
    receipt_id = project(ledger, objects, writer, correction_no=0, corrected=False)
    service = InsightsQueryService(
        facts=reader,
        snapshots=ledger.projection_snapshot_at,
        history=ledger,
        clock=lambda: datetime.now(UTC),
        limits=limits(),
    )
    token = secrets.token_urlsafe(32)
    authorizer = BearerPrincipalAuthorizer(
        token=token,
        principal=AccessPrincipal(
            project_id="project-a",
            principal_id="task11-reader",
            principal_type="user",
            audience="tap",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            actions=frozenset(
                {
                    "insights.evidence.read",
                    "insights.failures.read",
                    "insights.metrics.read",
                    "insights.runs.read",
                }
            ),
            enabled=True,
        ),
        expected_audience="tap",
    )
    client = TestClient(
        create_app(
            report_ledger=ledger,
            report_objects=objects,
            query_service=service,
            insights_authorizer=authorizer,
        )
    )
    request = {
        "metricIds": [item.value for item in MetricId],
        "filters": {"sourceIds": ["ci-a"]},
        "from": "2026-09-24",
        "to": "2026-09-25",
        "timezone": "Asia/Shanghai",
        "asOf": datetime.now(UTC).isoformat(),
    }
    headers = {"Authorization": f"Bearer {token}"}

    created_response = client.post(
        "/api/v1/projects/project-a/insights/queries",
        json=request,
        headers=headers,
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    values = {item["metricId"]: item for item in created["metrics"]}
    assert (
        values["first_pass_rate"]["numerator"],
        values["first_pass_rate"]["denominator"],
    ) == (1, 3)
    assert (
        values["final_pass_rate"]["numerator"],
        values["final_pass_rate"]["denominator"],
    ) == (2, 3)
    assert (
        values["retry_recovery_rate"]["numerator"],
        values["retry_recovery_rate"]["denominator"],
    ) == (1, 2)
    assert values["skipped_count"]["value"] == 1.0
    assert values["first_pass_rate"]["evidenceRefs"] == [receipt_id]

    query_id = created["queryId"]
    runs_page = client.get(
        "/api/v1/projects/project-a/insights/runs",
        params={"queryId": query_id},
        headers=headers,
    ).json()
    assert runs_page["queryId"] == query_id
    run_id = runs_page["items"][0]["runId"]
    assert runs_page["items"][0]["externalRunId"] == "run-a"
    attempts_page = client.get(
        f"/api/v1/projects/project-a/insights/runs/{run_id}/attempts",
        params={"queryId": query_id},
        headers=headers,
    ).json()
    assert attempts_page["queryId"] == query_id
    assert len(attempts_page["items"]) == 5
    assert (
        len(
            client.get(
                "/api/v1/projects/project-a/insights/failures",
                params={"queryId": query_id},
                headers=headers,
            ).json()["items"]
        )
        == 1
    )
    evidence = client.get(
        f"/api/v1/projects/project-a/insights/evidence/{receipt_id}",
        headers=headers,
    )
    assert evidence.status_code == 200
    assert evidence.content == report(corrected=False)
    assert (
        client.get(
            f"/api/v1/projects/project-a/insights/queries/{query_id}",
            headers={"Authorization": "Bearer wrong-token"},
        ).status_code
        == 403
    )

    project(ledger, objects, writer, correction_no=1, corrected=True)
    latest_request = dict(request, asOf=datetime.now(UTC).isoformat())
    latest = client.post(
        "/api/v1/projects/project-a/insights/queries",
        json=latest_request,
        headers=headers,
    ).json()
    latest_values = {item["metricId"]: item for item in latest["metrics"]}
    assert latest_values["final_pass_rate"]["numerator"] == 1
    assert (
        client.get(
            f"/api/v1/projects/project-a/insights/queries/{query_id}", headers=headers
        ).json()
        == created
    )

    limited = InsightsQueryService(
        facts=reader,
        snapshots=ledger.projection_snapshot_at,
        history=ledger,
        clock=lambda: datetime.now(UTC),
        limits=limits(rows=1),
    )
    with pytest.raises(QueryLimitExceeded):
        limited.execute(
            project_id="project-a",
            query=service.historical(project_id="project-a", query_id=query_id).query,
        )


def test_fixed_local_fixture_query_pressure_reports_observed_p95(runtime) -> None:
    """Measure only the committed-size local fixture; this is not a scale claim."""
    ledger, objects, writer, reader = runtime
    project(ledger, objects, writer, correction_no=0, corrected=False)
    service = InsightsQueryService(
        facts=reader,
        snapshots=ledger.projection_snapshot_at,
        history=ledger,
        clock=lambda: datetime.now(UTC),
        limits=limits(concurrency=10),
    )
    query = MetricQuery(
        metric_ids=tuple(MetricId),
        filters=QueryFilters(source_ids=("ci-a",)),
        from_date="2026-09-24",
        to_date="2026-09-25",
        timezone="Asia/Shanghai",
        as_of=datetime.now(UTC),
    )

    def execute_one(_: int) -> float:
        started = time.perf_counter()
        result = service.execute(project_id="project-a", query=query)
        assert result.metrics[0].denominator == 3
        return (time.perf_counter() - started) * 1000

    with ThreadPoolExecutor(max_workers=10) as executor:
        durations_ms = list(executor.map(execute_one, range(20)))
    p95_ms = statistics.quantiles(durations_ms, n=100, method="inclusive")[94]

    print(
        "TASK11_LOCAL_QUERY_P95_MS="
        f"{p95_ms:.3f} fixture_attempts=5 query_count=20 concurrency=10"
    )
    assert p95_ms > 0
