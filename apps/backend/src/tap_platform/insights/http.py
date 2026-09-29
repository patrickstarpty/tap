"""TAP report intake and evidence proxy routes."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections import OrderedDict
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from tap_platform.access import (
    AccessPrincipal,
    InsightsResource,
    authorize_insights_request,
    authorize_project_action,
)
from tap_platform.insights.adapters.allure import archive_member
from tap_platform.insights.adapters.report_errors import ReportSecurityError
from tap_platform.insights.adapters.report_evidence import (
    report_evidence,
    attachment_sources,
)
from tap_platform.insights.adapters.report_parser import VERSIONS as PARSER_VERSIONS
from tap_platform.insights.application.intake import ReportIntake, UploadTooLarge
from tap_platform.insights.application.queries import (
    InsightsQueryService,
    QueryLimitExceeded,
    QueryRecord,
    QueryTimedOut,
    QueryUnavailable,
)
from tap_platform.insights.contracts import (
    ReportEvidenceContract,
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
    def get_manifest(self, receipt_id: str) -> ReportManifest: ...
    def get_receipt(self, receipt_id: str) -> ReportReceipt: ...


class EvidenceReader(Protocol):
    def read(self, ref: str) -> bytes: ...


class ReportRetry(Protocol):
    def retry_failed(self, receipt_id: str) -> bool: ...


class InsightsAuthorizer(Protocol):
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
        service_bearer_token: str | None = None,
        authorization_version: str | None = None,
    ) -> bool:
        if (
            service_bearer_token is not None
            or authorization_version is not None
            or not secrets.compare_digest(bearer_token, self._token)
        ):
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


class DualBearerInsightsAuthorizer:
    """Validate an actual user and TAP AI service credential on every request."""

    _READ_ACTIONS = frozenset({"insights.metrics.read", "insights.evidence.read"})

    def __init__(
        self,
        *,
        user_token: str,
        user: AccessPrincipal,
        service_token: str,
        service: AccessPrincipal,
        expected_user_audience: str,
        expected_service_audience: str,
    ) -> None:
        if len(user_token) < 16 or len(service_token) < 16:
            raise ValueError(
                "dual bearer credentials must contain at least 16 characters"
            )
        self._user_token = user_token
        self._user = user
        self._service_token = service_token
        self._service = service
        self._expected_user_audience = expected_user_audience
        self._expected_service_audience = expected_service_audience

    def authorize(
        self,
        *,
        bearer_token: str,
        project_id: str,
        action: str,
        resource_kind: str,
        resource_id: str | None,
        service_bearer_token: str | None = None,
        authorization_version: str | None = None,
    ) -> bool:
        if (
            action not in self._READ_ACTIONS
            or authorization_version != self._user.authorization_version
            or service_bearer_token is None
            or not secrets.compare_digest(bearer_token, self._user_token)
            or not secrets.compare_digest(service_bearer_token, self._service_token)
        ):
            return False
        return authorize_insights_request(
            self._user,
            self._service,
            action,
            InsightsResource(
                project_id=project_id,
                kind=resource_kind,
                resource_id=resource_id,
            ),
            expected_user_audience=self._expected_user_audience,
            expected_service_audience=self._expected_service_audience,
            now=datetime.now(UTC),
        ).allowed


EVIDENCE_CACHE_SIZE = 16


def create_insights_router(
    *,
    report_intake: ReportIntake | None,
    report_ledger: ReceiptReader | None,
    report_objects: EvidenceReader | None,
    insights_authorizer: InsightsAuthorizer | None,
    report_retry: ReportRetry | None = None,
    query_service: InsightsQueryService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/projects/{project_id}/insights")
    # Originals are immutable and checksum-verified, so parsed evidence is reusable.
    evidence_cache: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()

    @router.get("/metrics", response_model=MetricCatalogContract)
    async def list_metrics(
        project_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> MetricCatalogContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.metrics.read",
            resource_kind="metric-query",
            resource_id=None,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
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
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> MetricQueryResponse:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.metrics.read",
            resource_kind="metric-query",
            resource_id=None,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
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
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="invalid insights query"
            ) from exc
        except QueryUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="insights query unavailable"
            ) from exc
        return query_contract(record)

    @router.get("/queries/{query_id}", response_model=MetricQueryResponse)
    async def get_query(
        project_id: str,
        query_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> MetricQueryResponse:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.metrics.read",
            resource_kind="metric-query",
            resource_id=query_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        return query_contract(_historical(query_service, project_id, query_id))

    @router.get("/runs", response_model=RunPageContract)
    async def list_runs(
        project_id: str,
        query_id: str = Query(alias="queryId"),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> RunPageContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.runs.read",
            resource_kind="run",
            resource_id=query_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        if query_service is None:
            raise HTTPException(status_code=503, detail="insights query unavailable")
        try:
            record, items, next_cursor = await run_in_threadpool(
                query_service.run_page,
                project_id=project_id,
                query_id=query_id,
                cursor=cursor,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="insights query not found"
            ) from exc
        except QueryTimedOut as exc:
            raise HTTPException(
                status_code=504, detail="insights query timed out"
            ) from exc
        except QueryLimitExceeded as exc:
            raise HTTPException(
                status_code=422, detail="insights query limit exceeded"
            ) from exc
        except QueryUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="insights query unavailable"
            ) from exc
        return run_page_contract(
            record,
            cursor=cursor,
            limit=limit,
            items=items,
            next_cursor=next_cursor,
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
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> AttemptPageContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.runs.read",
            resource_kind="run",
            resource_id=run_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        if query_service is None:
            raise HTTPException(status_code=503, detail="insights query unavailable")
        try:
            record, items, next_cursor = await run_in_threadpool(
                query_service.attempt_page,
                project_id=project_id,
                query_id=query_id,
                run_id=run_id,
                cursor=cursor,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="insights run not found"
            ) from exc
        except QueryTimedOut as exc:
            raise HTTPException(
                status_code=504, detail="insights query timed out"
            ) from exc
        except QueryLimitExceeded as exc:
            raise HTTPException(
                status_code=422, detail="insights query limit exceeded"
            ) from exc
        except QueryUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="insights query unavailable"
            ) from exc
        return attempt_page_contract(
            record,
            run_id=run_id,
            cursor=cursor,
            limit=limit,
            items=items,
            next_cursor=next_cursor,
        )

    @router.get("/failures", response_model=FailurePageContract)
    async def list_failures(
        project_id: str,
        query_id: str = Query(alias="queryId"),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> FailurePageContract:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.failures.read",
            resource_kind="failure",
            resource_id=query_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        if query_service is None:
            raise HTTPException(status_code=503, detail="insights query unavailable")
        try:
            record, items, next_cursor = await run_in_threadpool(
                query_service.failure_page,
                project_id=project_id,
                query_id=query_id,
                cursor=cursor,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="insights query not found"
            ) from exc
        except QueryTimedOut as exc:
            raise HTTPException(
                status_code=504, detail="insights query timed out"
            ) from exc
        except QueryLimitExceeded as exc:
            raise HTTPException(
                status_code=422, detail="insights query limit exceeded"
            ) from exc
        except QueryUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="insights query unavailable"
            ) from exc
        return failure_page_contract(
            record,
            cursor=cursor,
            limit=limit,
            items=items,
            next_cursor=next_cursor,
        )

    @router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
    async def receive_report(
        project_id: str,
        request: Request,
        x_tap_report_manifest: str = Header(alias="X-TAP-Report-Manifest"),
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> dict[str, Any]:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.reports.create",
            resource_kind="report",
            resource_id=None,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
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
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        if _contradicts_format(
            manifest.report_format, request.headers.get("content-type"), chunks
        ):
            raise HTTPException(
                status_code=415,
                detail=f"uploaded content does not match reportFormat "
                f"{manifest.report_format}",
            )
        try:
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
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> dict[str, Any]:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.reports.read",
            resource_kind="report",
            resource_id=receipt_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        receipt = await _load_receipt(report_ledger, receipt_id)
        _authorize_receipt_project(project_id, receipt)
        return _receipt_body(receipt)

    @router.post("/reports/{receipt_id}/retry", status_code=status.HTTP_202_ACCEPTED)
    async def retry_report(
        project_id: str,
        receipt_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> dict[str, Any]:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.reports.create",
            resource_kind="report",
            resource_id=receipt_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        receipt = await _load_receipt(report_ledger, receipt_id)
        _authorize_receipt_project(project_id, receipt)
        if report_retry is None:
            raise HTTPException(status_code=503, detail="report retry unavailable")
        if not await run_in_threadpool(report_retry.retry_failed, receipt_id):
            raise HTTPException(status_code=409, detail="report is not retryable")
        return _receipt_body(await _load_receipt(report_ledger, receipt_id))

    @router.get("/evidence/{receipt_id}")
    async def download_evidence(
        project_id: str,
        receipt_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> Response:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.evidence.read",
            resource_kind="evidence",
            resource_id=receipt_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        if report_objects is None:
            raise HTTPException(status_code=503, detail="evidence store unavailable")
        receipt = await _load_receipt(report_ledger, receipt_id)
        _authorize_receipt_project(project_id, receipt)
        raw = await run_in_threadpool(report_objects.read, receipt.raw_object_ref)
        is_zip = receipt.parser_version == PARSER_VERSIONS["allure"]
        return Response(
            content=raw,
            media_type="application/zip" if is_zip else "application/xml",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'attachment; filename="report-{receipt.receipt_id}.{"zip" if is_zip else "xml"}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def load_report_details(
        project_id: str,
        receipt_id: str,
        authorization: str | None,
        service_authorization: str | None,
        authorization_version: str | None,
    ) -> tuple[dict[str, Any], bytes]:
        _authorize(
            insights_authorizer,
            authorization,
            project_id=project_id,
            action="insights.evidence.read",
            resource_kind="evidence",
            resource_id=receipt_id,
            service_authorization=service_authorization,
            authorization_version=authorization_version,
        )
        if report_objects is None or report_ledger is None:
            raise HTTPException(status_code=503, detail="evidence store unavailable")
        receipt = await _load_receipt(report_ledger, receipt_id)
        _authorize_receipt_project(project_id, receipt)
        if receipt.state not in {
            ReportState.MAPPED,
            ReportState.PROJECTING,
            ReportState.READY,
        }:
            raise HTTPException(status_code=409, detail="report evidence is not ready")
        raw = await run_in_threadpool(report_objects.read, receipt.raw_object_ref)
        if (
            len(raw) != receipt.size_bytes
            or hashlib.sha256(raw).hexdigest() != receipt.checksum
        ):
            raise HTTPException(
                status_code=503, detail="evidence integrity check failed"
            )
        cache_key = (receipt.receipt_id, receipt.checksum, receipt.parser_version)
        details = evidence_cache.get(cache_key)
        if details is not None:
            evidence_cache.move_to_end(cache_key)
            return details, raw
        manifest = await run_in_threadpool(report_ledger.get_manifest, receipt_id)
        try:
            details = await run_in_threadpool(
                partial(
                    report_evidence,
                    raw,
                    manifest,
                    receipt.parser_version,
                    receipt=receipt,
                )
            )
        except (ReportSecurityError, RuntimeError) as exc:
            raise HTTPException(
                status_code=422, detail="report evidence cannot be read"
            ) from exc
        evidence_cache[cache_key] = details
        while len(evidence_cache) > EVIDENCE_CACHE_SIZE:
            evidence_cache.popitem(last=False)
        return details, raw

    @router.get("/evidence/{receipt_id}/details", response_model=ReportEvidenceContract)
    async def evidence_details(
        project_id: str,
        receipt_id: str,
        response: Response,
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> ReportEvidenceContract:
        details, _ = await load_report_details(
            project_id,
            receipt_id,
            authorization,
            service_authorization,
            authorization_version,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return ReportEvidenceContract.model_validate(details)

    @router.get("/evidence/{receipt_id}/attachment")
    async def evidence_attachment(
        project_id: str,
        receipt_id: str,
        source: str = Query(max_length=512),
        authorization: str | None = Header(default=None, alias="Authorization"),
        service_authorization: str | None = Header(
            default=None, alias="X-TAP-Service-Authorization"
        ),
        authorization_version: str | None = Header(
            default=None, alias="X-TAP-Authorization-Version"
        ),
    ) -> Response:
        details, raw = await load_report_details(
            project_id,
            receipt_id,
            authorization,
            service_authorization,
            authorization_version,
        )
        if details["reportFormat"] != "allure" or source not in attachment_sources(
            details
        ):
            raise HTTPException(status_code=404, detail="attachment not found")
        try:
            content = await run_in_threadpool(archive_member, raw, source)
        except (ReportSecurityError, KeyError) as exc:
            raise HTTPException(status_code=404, detail="attachment not found") from exc
        # Only raster signatures may render inline. HTML/SVG/unknown data download
        # as inert bytes regardless of the report-supplied MIME type or filename.
        media_type = "application/octet-stream"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            media_type = "image/jpeg"
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'attachment; filename="attachment"',
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    return router


def _historical(
    service: InsightsQueryService | None, project_id: str, query_id: str
) -> QueryRecord:
    if service is None:
        raise HTTPException(status_code=503, detail="insights query unavailable")
    try:
        return service.historical(project_id=project_id, query_id=query_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="insights query not found") from exc
    except QueryUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="insights query unavailable"
        ) from exc


_ZIP_MEDIA_TYPES = {"application/zip", "application/x-zip-compressed"}
_XML_MEDIA_TYPES = {"application/xml", "text/xml"}


def _contradicts_format(
    report_format: str, content_type: str | None, chunks: list[bytes]
) -> bool:
    """Reject only clear container mismatches; malformed content stays a durable receipt."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    head = b""
    for chunk in chunks:
        head += chunk[: 4 - len(head)]
        if len(head) >= 4:
            break
    is_zip = head.startswith(b"PK\x03\x04")
    if report_format == "allure":
        return media_type in _XML_MEDIA_TYPES or not is_zip
    return media_type in _ZIP_MEDIA_TYPES or is_zip


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
    service_authorization: str | None,
    authorization_version: str | None = None,
) -> None:
    bearer_token = _bearer_token(authorization)
    service_bearer_token = _bearer_token(service_authorization)
    if (
        authorizer is None
        or bearer_token is None
        or not authorizer.authorize(
            bearer_token=bearer_token,
            project_id=project_id,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
            service_bearer_token=service_bearer_token,
            authorization_version=authorization_version,
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
