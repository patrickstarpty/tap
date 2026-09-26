"""TAP report intake and evidence proxy routes."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from tap_platform.access import (
    AccessPrincipal,
    InsightsResource,
    authorize_project_action,
)
from tap_platform.insights.application.intake import ReportIntake, UploadTooLarge
from tap_platform.insights.application.queries import (
    InsightsQueryService,
    QueryLimitExceeded,
    QueryTimedOut,
    QueryUnavailable,
)
from tap_platform.insights.contracts import (
    AttemptPageContract,
    FailurePageContract,
    MetricCatalogContract,
    MetricQueryRequest,
    MetricQueryResponse,
    RunPageContract,
    catalog_contract,
    attempt_page_contract,
    failure_page_contract,
    query_contract,
    run_page_contract,
)
from tap_platform.insights.domain.reports import (
    ReportManifest,
    ReportReceipt,
    ReportState,
)


class ReceiptReader(Protocol):
    def get_receipt(self, receipt_id: str) -> ReportReceipt: ...


class EvidenceReader(Protocol):
    def read(self, ref: str) -> bytes: ...


class InsightsAuthorizer(Protocol):
    def authorize(
        self,
        *,
        bearer_token: str,
        project_id: str,
        action: str,
        resource_kind: str,
        resource_id: str | None,
    ) -> bool: ...


class BearerPrincipalAuthorizer:
    """Bind an opaque bearer secret to a server-validated principal."""

    def __init__(
        self,
        *,
        token: str,
        principal: AccessPrincipal,
        expected_audience: str,
    ) -> None:
        if len(token) < 16:
            raise ValueError("bearer token must contain at least 16 characters")
        self._token = token
        self._principal = principal
        self._expected_audience = expected_audience

    def authorize(
        self,
        *,
        bearer_token: str,
        project_id: str,
        action: str,
        resource_kind: str,
        resource_id: str | None,
    ) -> bool:
        if not secrets.compare_digest(bearer_token, self._token):
            return False
        decision = authorize_project_action(
            self._principal,
            action,
            InsightsResource(
                project_id=project_id,
                kind=resource_kind,
                resource_id=resource_id,
            ),
            expected_audience=self._expected_audience,
            now=datetime.now(UTC),
        )
        return decision.allowed


def create_insights_router(
    *,
    report_intake: ReportIntake | None,
    report_ledger: ReceiptReader | None,
    report_objects: EvidenceReader | None,
    insights_authorizer: InsightsAuthorizer | None,
    query_service: InsightsQueryService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/projects/{project_id}/insights")

    @router.get("/metrics", response_model=MetricCatalogContract)
    async def list_metrics(
        project_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MetricCatalogContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.metrics.read",
            resource_kind="metric-query",
            resource_id=None,
        )
        return catalog_contract()

    @router.post(
        "/queries",
        response_model=MetricQueryResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_query(
        project_id: str,
        request: MetricQueryRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MetricQueryResponse:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.metrics.read",
            resource_kind="metric-query",
            resource_id=None,
        )
        if query_service is None:
            raise HTTPException(status_code=503, detail="insights query unavailable")
        try:
            record = await run_in_threadpool(
                query_service.execute,
                project_id=project_id,
                query=request.to_domain(),
            )
        except QueryLimitExceeded as exc:
            raise HTTPException(
                status_code=422, detail="insights query limit exceeded"
            ) from exc
        except QueryTimedOut as exc:
            raise HTTPException(
                status_code=504, detail="insights query timed out"
            ) from exc
        except (QueryUnavailable, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="insights query unavailable"
            ) from exc
        return query_contract(record)

    @router.get("/queries/{query_id}", response_model=MetricQueryResponse)
    async def get_query(
        project_id: str,
        query_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MetricQueryResponse:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.metrics.read",
            resource_kind="metric-query",
            resource_id=query_id,
        )
        return query_contract(_historical(query_service, project_id, query_id))

    @router.get("/runs", response_model=RunPageContract)
    async def list_runs(
        project_id: str,
        query_id: str = Query(alias="queryId"),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> RunPageContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.runs.read",
            resource_kind="run",
            resource_id=query_id,
        )
        return run_page_contract(
            _historical(query_service, project_id, query_id),
            cursor=cursor,
            limit=limit,
        )

    @router.get(
        "/runs/{run_id}/attempts",
        response_model=AttemptPageContract,
    )
    async def list_run_attempts(
        project_id: str,
        run_id: str,
        query_id: str = Query(alias="queryId"),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> AttemptPageContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.runs.read",
            resource_kind="run",
            resource_id=run_id,
        )
        record = _historical(query_service, project_id, query_id)
        if not any(item.run_id == run_id for item in record.runs):
            raise HTTPException(status_code=404, detail="insights run not found")
        return attempt_page_contract(
            record,
            run_id=run_id,
            cursor=cursor,
            limit=limit,
        )

    @router.get("/failures", response_model=FailurePageContract)
    async def list_failures(
        project_id: str,
        query_id: str = Query(alias="queryId"),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> FailurePageContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.failures.read",
            resource_kind="failure",
            resource_id=query_id,
        )
        return failure_page_contract(
            _historical(query_service, project_id, query_id),
            cursor=cursor,
            limit=limit,
        )

    @router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
    async def receive_report(
        project_id: str,
        request: Request,
        x_tap_report_manifest: str = Header(alias="X-TAP-Report-Manifest"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.reports.create",
            resource_kind="report",
            resource_id=None,
        )
        if report_intake is None:
            raise HTTPException(status_code=503, detail="report intake unavailable")
        try:
            manifest_value = json.loads(x_tap_report_manifest)
            manifest = ReportManifest.from_dict(manifest_value)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="invalid report manifest"
            ) from exc
        if manifest.project_id != project_id:
            raise HTTPException(status_code=403, detail="project scope mismatch")
        try:
            chunks = await _bounded_request_chunks(
                request, max_bytes=report_intake.max_upload_bytes
            )
            receipt = await run_in_threadpool(report_intake.receive, manifest, chunks)
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        if receipt.state is ReportState.CONFLICTED:
            raise HTTPException(status_code=409, detail=_receipt_body(receipt))
        return _receipt_body(receipt)

    @router.get("/reports/{receipt_id}")
    async def get_report(
        project_id: str,
        receipt_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.reports.read",
            resource_kind="report",
            resource_id=receipt_id,
        )
        receipt = await _load_receipt(report_ledger, receipt_id)
        _authorize_receipt_project(project_id, receipt)
        return _receipt_body(receipt)

    @router.get("/evidence/{receipt_id}")
    async def download_evidence(
        project_id: str,
        receipt_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Response:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.evidence.read",
            resource_kind="evidence",
            resource_id=receipt_id,
        )
        if report_objects is None:
            raise HTTPException(status_code=503, detail="evidence store unavailable")
        receipt = await _load_receipt(report_ledger, receipt_id)
        _authorize_receipt_project(project_id, receipt)
        raw = await run_in_threadpool(report_objects.read, receipt.raw_object_ref)
        return Response(
            content=raw,
            media_type="application/xml",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'attachment; filename="report-{receipt.receipt_id}.xml"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router


def _historical(service: InsightsQueryService | None, project_id: str, query_id: str):
    if service is None:
        raise HTTPException(status_code=503, detail="insights query unavailable")
    try:
        return service.historical(project_id=project_id, query_id=query_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="insights query not found") from exc


async def _bounded_request_chunks(request: Request, *, max_bytes: int) -> list[bytes]:
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            raise UploadTooLarge(f"report exceeds the {max_bytes}-byte upload limit")
        chunks.append(chunk)
    return chunks


async def _load_receipt(ledger: ReceiptReader | None, receipt_id: str) -> ReportReceipt:
    if ledger is None:
        raise HTTPException(status_code=503, detail="report ledger unavailable")
    try:
        return await run_in_threadpool(ledger.get_receipt, receipt_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="report receipt not found") from exc


def _authorize(
    authorizer: InsightsAuthorizer | None,
    authorization: str | None,
    *,
    project_id: str,
    action: str,
    resource_kind: str,
    resource_id: str | None,
) -> None:
    bearer_token = _bearer_token(authorization)
    if (
        authorizer is None
        or bearer_token is None
        or not authorizer.authorize(
            bearer_token=bearer_token,
            project_id=project_id,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )
    ):
        raise HTTPException(status_code=403, detail="insights action denied")


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    return token


def _authorize_receipt_project(project_id: str, receipt: ReportReceipt) -> None:
    if receipt.project_id != project_id:
        raise HTTPException(status_code=404, detail="report receipt not found")


def _receipt_body(receipt: ReportReceipt) -> dict[str, Any]:
    return {
        "receiptId": receipt.receipt_id,
        "projectId": receipt.project_id,
        "sourceId": receipt.source_id,
        "externalRunId": receipt.external_run_id,
        "batchId": receipt.batch_id,
        "shardId": receipt.shard_id,
        "checksum": receipt.checksum,
        "parserVersion": receipt.parser_version,
        "correctionNo": receipt.correction_no,
        "state": receipt.state.value,
        "completeness": receipt.completeness.value,
        "sizeBytes": receipt.size_bytes,
        "conflictWithReceiptId": receipt.conflict_with_receipt_id,
        "failureReason": receipt.failure_reason,
        "evidenceUrl": (
            f"/api/v1/projects/{receipt.project_id}/insights/evidence/{receipt.receipt_id}"
        ),
    }
