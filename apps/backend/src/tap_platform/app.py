"""Standalone TAP backend entrypoint for non-AI modules."""

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger
from tap_platform.insights.adapters.objects import FileReportObjectStore
from tap_platform.insights.application.intake import ReportIntake
from tap_platform.insights.http import (
    BearerPrincipalAuthorizer,
    EvidenceReader,
    InsightsAuthorizer,
    ReceiptReader,
    create_insights_router,
)


def create_app(
    *,
    report_intake: ReportIntake | None = None,
    report_ledger: ReceiptReader | None = None,
    report_objects: EvidenceReader | None = None,
    insights_authorizer: InsightsAuthorizer | None = None,
) -> FastAPI:
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
    if insights_authorizer is None:
        insights_authorizer = _authorizer_from_environment()
    app = FastAPI(title="TAP API", version="0.1.0")
    app.include_router(
        create_insights_router(
            report_intake=report_intake,
            report_ledger=report_ledger,
            report_objects=report_objects,
            insights_authorizer=insights_authorizer,
        )
    )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "product": "tap"}

    return app


def _authorizer_from_environment() -> InsightsAuthorizer | None:
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
                "insights.reports.create",
                "insights.reports.read",
            }
        ),
        enabled=True,
    )
    return BearerPrincipalAuthorizer(
        token=token,
        principal=principal,
        expected_audience="tap",
    )


app = create_app()
