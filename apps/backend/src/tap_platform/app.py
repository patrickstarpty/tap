"""Standalone TAP backend entrypoint for non-AI modules."""

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal
from tap_platform.insights.adapters.clickhouse import ClickHouseInsightsStore
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.intake import ReportIntake
from tap_platform.insights.application.queries import InsightsQueryService, QueryLimits
from tap_platform.insights.http import (
    BearerPrincipalAuthorizer,
    DualBearerInsightsAuthorizer,
    EvidenceReader,
    InsightsAuthorizer,
    ReceiptReader,
    ReportRetry,
    create_insights_router,
)
from tap_platform.insights.worker import ReportWorker


def create_app(
    *,
    report_intake: ReportIntake | None = None,
    report_ledger: ReceiptReader | None = None,
    report_objects: EvidenceReader | None = None,
    report_retry: ReportRetry | None = None,
    insights_authorizer: InsightsAuthorizer | None = None,
    query_service: InsightsQueryService | None = None,
) -> FastAPI:
    ledger: SqlAlchemyReportLedger | None = None
    if report_intake is None and report_ledger is None and report_objects is None:
        database_url = os.getenv("TAP_DATABASE_URL")
        object_root = os.getenv("TAP_REPORT_OBJECT_ROOT")
        if bool(database_url) != bool(object_root):
            raise RuntimeError(
                "TAP_DATABASE_URL and TAP_REPORT_OBJECT_ROOT must be configured together"
            )
        if database_url and object_root:
            max_upload_bytes = int(
                os.getenv("TAP_REPORT_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))
            )
            ledger = SqlAlchemyReportLedger(
                create_engine(database_url, pool_pre_ping=True)
            )
            objects = FileReportObjectStore(Path(object_root))
            report_ledger = ledger
            report_objects = objects
            report_intake = ReportIntake(
                ledger=ledger,
                objects=objects,
                max_upload_bytes=max_upload_bytes,
            )
            report_retry = ReportWorker(ledger=ledger, objects=objects)
    if query_service is None and ledger is not None:
        clickhouse_url = os.getenv("TAP_CLICKHOUSE_URL")
        clickhouse_user = os.getenv("TAP_CLICKHOUSE_READER_USER")
        clickhouse_password = os.getenv("TAP_CLICKHOUSE_READER_PASSWORD")
        if clickhouse_url:
            if not all((clickhouse_user, clickhouse_password)):
                raise RuntimeError(
                    "TAP Insights ClickHouse reader settings must be configured together"
                )
            assert clickhouse_url is not None
            assert clickhouse_user is not None
            assert clickhouse_password is not None
            store = ClickHouseInsightsStore(
                clickhouse_url,
                username=clickhouse_user,
                password=clickhouse_password,
                timeout_seconds=float(
                    os.getenv("TAP_INSIGHTS_QUERY_TIMEOUT_SECONDS", "3")
                ),
            )
            query_service = InsightsQueryService(
                facts=store,
                snapshots=ledger.projection_snapshot_at,
                history=ledger,
                clock=lambda: datetime.now(UTC),
                limits=QueryLimits(
                    max_rows_to_read=int(
                        os.getenv("TAP_INSIGHTS_MAX_ROWS_TO_READ", "1000000")
                    ),
                    max_bytes_to_read=int(
                        os.getenv("TAP_INSIGHTS_MAX_BYTES_TO_READ", "268435456")
                    ),
                    max_memory_bytes=int(
                        os.getenv("TAP_INSIGHTS_MAX_MEMORY_BYTES", "268435456")
                    ),
                    max_concurrent_queries=int(
                        os.getenv("TAP_INSIGHTS_MAX_CONCURRENT_QUERIES", "10")
                    ),
                    max_output_rows=int(
                        os.getenv("TAP_INSIGHTS_MAX_OUTPUT_ROWS", "10000")
                    ),
                    max_output_bytes=int(
                        os.getenv("TAP_INSIGHTS_MAX_OUTPUT_BYTES", "4194304")
                    ),
                    timeout_seconds=float(
                        os.getenv("TAP_INSIGHTS_QUERY_TIMEOUT_SECONDS", "3")
                    ),
                ),
            )
    if insights_authorizer is None:
        insights_authorizer = _authorizer_from_environment()
    app = FastAPI(title="TAP API", version="0.1.0")
    app.include_router(
        create_insights_router(
            report_intake=report_intake,
            report_ledger=report_ledger,
            report_objects=report_objects,
            report_retry=report_retry,
            insights_authorizer=insights_authorizer,
            query_service=query_service,
        )
    )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "product": "tap"}

    return app


def _authorizer_from_environment() -> InsightsAuthorizer | None:
    report = _report_authorizer_from_environment()
    delegated = _delegated_authorizer_from_environment()
    if report is not None and delegated is not None:
        return _CombinedInsightsAuthorizer(report=report, delegated=delegated)
    return report or delegated


class _CombinedInsightsAuthorizer:
    def __init__(
        self, *, report: InsightsAuthorizer, delegated: InsightsAuthorizer
    ) -> None:
        self._report = report
        self._delegated = delegated

    def authorize(
        self,
        *,
        bearer_token: str,
        project_id: str,
        action: str,
        resource_kind: str,
        resource_id: str | None,
        service_bearer_token: str | None,
        authorization_version: str | None,
    ) -> bool:
        authorizer = (
            self._delegated
            if service_bearer_token is not None or authorization_version is not None
            else self._report
        )
        return authorizer.authorize(
            bearer_token=bearer_token,
            project_id=project_id,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
            service_bearer_token=service_bearer_token,
            authorization_version=authorization_version,
        )


def _report_authorizer_from_environment() -> InsightsAuthorizer | None:
    token = os.getenv("TAP_REPORT_ACCESS_TOKEN")
    project_id = os.getenv("TAP_REPORT_PROJECT_ID")
    expires_at_value = os.getenv("TAP_REPORT_TOKEN_EXPIRES_AT")
    if not any((token, project_id, expires_at_value)):
        return None
    if not all((token, project_id, expires_at_value)):
        raise RuntimeError(
            "TAP_REPORT_ACCESS_TOKEN, TAP_REPORT_PROJECT_ID and "
            "TAP_REPORT_TOKEN_EXPIRES_AT must be configured together"
        )
    assert token is not None
    assert project_id is not None
    assert expires_at_value is not None
    try:
        expires_at = datetime.fromisoformat(expires_at_value)
    except ValueError as exc:
        raise RuntimeError("TAP_REPORT_TOKEN_EXPIRES_AT must be ISO-8601") from exc
    principal = AccessPrincipal(
        project_id=project_id,
        principal_id="tap-report-uploader",
        principal_type="service",
        audience="tap",
        expires_at=expires_at,
        actions=frozenset(
            {
                "insights.evidence.read",
                "insights.failures.read",
                "insights.metrics.read",
                "insights.reports.create",
                "insights.reports.read",
                "insights.runs.read",
            }
        ),
        enabled=True,
    )
    return BearerPrincipalAuthorizer(
        token=token,
        principal=principal,
        expected_audience="tap",
    )


def _delegated_authorizer_from_environment() -> InsightsAuthorizer | None:
    names = (
        "TAP_INSIGHTS_DELEGATED_USER_TOKEN",
        "TAP_INSIGHTS_SERVICE_TOKEN",
        "TAP_INSIGHTS_DELEGATED_PROJECT_ID",
        "TAP_INSIGHTS_DELEGATED_EXPIRES_AT",
        "TAP_INSIGHTS_AUTHORIZATION_VERSION",
    )
    values = {name: os.getenv(name) for name in names}
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise RuntimeError(
            "TAP Insights delegated authorization settings must be configured together"
        )
    user_token = values["TAP_INSIGHTS_DELEGATED_USER_TOKEN"]
    service_token = values["TAP_INSIGHTS_SERVICE_TOKEN"]
    project_id = values["TAP_INSIGHTS_DELEGATED_PROJECT_ID"]
    expiry = values["TAP_INSIGHTS_DELEGATED_EXPIRES_AT"]
    version = values["TAP_INSIGHTS_AUTHORIZATION_VERSION"]
    assert user_token and service_token and project_id and expiry and version
    try:
        expires_at = datetime.fromisoformat(expiry)
    except ValueError as exc:
        raise RuntimeError(
            "TAP_INSIGHTS_DELEGATED_EXPIRES_AT must be ISO-8601"
        ) from exc
    return DualBearerInsightsAuthorizer(
        user_token=user_token,
        user=AccessPrincipal(
            project_id=project_id,
            principal_id="delegated-insights-user",
            principal_type="user",
            audience="tap",
            expires_at=expires_at,
            actions=frozenset({"insights.metrics.read", "insights.evidence.read"}),
            enabled=True,
            authorization_version=version,
        ),
        service_token=service_token,
        service=AccessPrincipal(
            project_id=project_id,
            principal_id="tap-ai-insights-service",
            principal_type="service",
            audience="tap-insights",
            expires_at=expires_at,
            actions=frozenset({"insights.invoke"}),
            enabled=True,
            authorization_version=version,
        ),
        expected_user_audience="tap",
        expected_service_audience="tap-insights",
    )


app = create_app()
