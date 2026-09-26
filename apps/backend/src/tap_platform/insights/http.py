"""TAP report intake and evidence proxy routes."""

from __future__ import annotations

import json
from typing import Any, Protocol

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from tap_platform.insights.application.intake import ReportIntake, UploadTooLarge
from tap_platform.insights.domain.reports import (
    ReportManifest,
    ReportReceipt,
    ReportState,
)


class ReceiptReader(Protocol):
    def get_receipt(self, receipt_id: str) -> ReportReceipt: ...


class EvidenceReader(Protocol):
    def read(self, ref: str) -> bytes: ...


def create_insights_router(
    *,
    report_intake: ReportIntake | None,
    report_ledger: ReceiptReader | None,
    report_objects: EvidenceReader | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/projects/{project_id}/insights")

    @router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
    async def receive_report(
        project_id: str,
        request: Request,
        x_tap_report_manifest: str = Header(alias="X-TAP-Report-Manifest"),
        x_tap_project_id: str | None = Header(default=None, alias="X-TAP-Project-ID"),
        x_tap_actions: str | None = Header(default=None, alias="X-TAP-Actions"),
    ) -> dict[str, Any]:
        _authorize(
            project_id, x_tap_project_id, x_tap_actions, "insights.reports.create"
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
        x_tap_project_id: str | None = Header(default=None, alias="X-TAP-Project-ID"),
        x_tap_actions: str | None = Header(default=None, alias="X-TAP-Actions"),
    ) -> dict[str, Any]:
        _authorize(project_id, x_tap_project_id, x_tap_actions, "insights.reports.read")
        receipt = await _load_receipt(report_ledger, receipt_id)
        _authorize_receipt_project(project_id, receipt)
        return _receipt_body(receipt)

    @router.get("/evidence/{receipt_id}")
    async def download_evidence(
        project_id: str,
        receipt_id: str,
        x_tap_project_id: str | None = Header(default=None, alias="X-TAP-Project-ID"),
        x_tap_actions: str | None = Header(default=None, alias="X-TAP-Actions"),
    ) -> Response:
        _authorize(
            project_id, x_tap_project_id, x_tap_actions, "insights.evidence.read"
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
    path_project: str,
    principal_project: str | None,
    action_header: str | None,
    action: str,
) -> None:
    actions = {
        item.strip() for item in (action_header or "").split(",") if item.strip()
    }
    if principal_project != path_project or action not in actions:
        raise HTTPException(status_code=403, detail="insights action denied")


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
