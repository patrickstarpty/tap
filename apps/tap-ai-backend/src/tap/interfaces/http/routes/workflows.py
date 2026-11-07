"""Project-scoped workflow HTTP reads, controls, and durable SSE replay."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response, StreamingResponse

from tap.contracts.workflows import (
    WorkflowCommandRequest,
    WorkflowCommandView,
    WorkflowCreateRequest,
    WorkflowLogPage,
    WorkflowNodeDetail,
    WorkflowTaskPage,
    WorkflowTaskSnapshot,
)
from tap.interfaces.http.dependencies import source_command_key
from tap.interfaces.http.problems import problem_response, problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.interfaces.http.workflow_service import WorkflowControlUnavailable, WorkflowHttpService
from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable
from tap.modules.workflows.adapters.browser_client import BrowserApiClient, BrowserApiError
from tap.modules.workflows.application.browser_artifacts import BrowserArtifactAdapter
from tap.modules.workflows.application.queries import SnapshotRequired, WorkflowQueries, replay_from

router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[Depends(project_authorization("knowledge.answer"))],
)


def workflow_service(request: Request) -> WorkflowHttpService:
    services = request.app.state.http_services
    store = services.workflow_store
    admission = services.workflow_admission
    if store is None:
        raise KnowledgeRuntimeUnavailable
    return WorkflowHttpService(
        services,
        store,
        admission,
        WorkflowQueries(
            store,
            redactor=PatternEgressRedactor(personal_data=True),
            native_controls_enabled=services.workflow_enabled,
            browser_artifacts=(
                BrowserArtifactAdapter(store, services.browser_client)
                if isinstance(services.browser_client, BrowserApiClient)
                else None
            ),
        ),
    )


def _problem(request: Request, error: Exception):
    if isinstance(error, LookupError):
        return problem_response("workflow-not-found", request)
    if isinstance(error, SnapshotRequired):
        return problem_response("workflow-snapshot-required", request)
    if isinstance(error, WorkflowControlUnavailable):
        return problem_response("workflow-control-unavailable", request)
    if "conflict" in str(error):
        return problem_response("idempotency-conflict", request)
    if "stale" in str(error) or "revision" in str(error):
        return problem_response("revision-conflict", request)
    return problem_response("request-validation", request)


@router.post(
    "",
    operation_id="workflow_create",
    response_model=WorkflowTaskSnapshot,
    status_code=202,
    responses={
        404: problem_response_metadata("Workflow unavailable"),
        409: problem_response_metadata("Idempotency conflict"),
    },
)
async def create(
    body: WorkflowCreateRequest,
    request: Request,
    key: str = Depends(source_command_key),
    service: WorkflowHttpService = Depends(workflow_service),
):
    try:
        return await service.create(request.state.project_scope, body, key)
    except (LookupError, ValueError) as error:
        return _problem(request, error)


@router.get("", operation_id="workflow_list", response_model=WorkflowTaskPage)
async def list_tasks(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = None,
    agent_id: str | None = Query(None, alias="agentId", max_length=64),
    service: WorkflowHttpService = Depends(workflow_service),
):
    try:
        return await service.queries.list(request.state.project_scope, limit, cursor, agent_id)
    except ValueError as error:
        return _problem(request, error)


@router.get("/{task_id}", operation_id="workflow_get", response_model=WorkflowTaskSnapshot)
async def snapshot(
    task_id: str,
    request: Request,
    service: WorkflowHttpService = Depends(workflow_service),
):
    try:
        return await service.queries.snapshot(request.state.project_scope, task_id)
    except LookupError as error:
        return _problem(request, error)


@router.get(
    "/{task_id}/events",
    operation_id="workflow_events",
    responses={
        200: {
            "description": "Durable workflow event stream",
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/WorkflowEventEnvelope"}
                }
            },
        },
        409: problem_response_metadata("Snapshot required"),
    },
)
async def events(
    task_id: str,
    request: Request,
    snapshot_sequence: int | None = Query(None, alias="snapshotSequence", ge=0),
    last_event_id: str | None = Header(None, alias="Last-Event-ID", pattern=r"^(0|[1-9][0-9]*)$"),
    service: WorkflowHttpService = Depends(workflow_service),
):
    scope = request.state.project_scope
    queries = service.queries
    try:
        current = await queries.snapshot(scope, task_id)
        high = current["lastSequence"]
        if snapshot_sequence is not None and snapshot_sequence > high:
            raise SnapshotRequired("future workflow cursor requires a new snapshot")
        client_sequence = None if last_event_id is None else int(last_event_id)
        cursor = (
            replay_from(snapshot_sequence=high, client_sequence=client_sequence)
            if client_sequence is not None
            else (snapshot_sequence if snapshot_sequence is not None else high)
        )
        initial = await queries.events(scope, task_id, cursor)
    except (LookupError, SnapshotRequired, ValueError) as error:
        return _problem(request, error)

    hints = getattr(request.app.state.http_services, "workflow_events", None)

    async def generate():
        nonlocal cursor
        pending = initial
        # Start before any hint that could land between MySQL catch-up and XREAD.
        redis_cursor = "0"
        while True:
            for item in pending:
                cursor = item["sequence"]
                yield (
                    f"id: {cursor}\nevent: {item['kind']}\ndata: "
                    + json.dumps(item, separators=(",", ":"), ensure_ascii=False)
                    + "\n\n"
                )
            if await request.is_disconnected():
                return
            try:
                current = await queries.snapshot(scope, task_id)
            except LookupError:
                return
            if (
                current["status"] in ("succeeded", "failed", "canceled", "skipped")
                and cursor >= current["lastSequence"]
            ):
                return
            if len(pending) < 100:
                if hints is None:
                    await asyncio.sleep(1)
                else:
                    redis_cursor = await hints.read_hint(
                        scope, task_id, redis_cursor, max_wait_seconds=1
                    )
            try:
                pending = await queries.events(scope, task_id, cursor)
            except (LookupError, SnapshotRequired):
                return

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post(
    "/{task_id}/replay",
    operation_id="workflow_replay",
    response_model=WorkflowTaskSnapshot,
    status_code=202,
)
async def replay(
    task_id: str,
    request: Request,
    key: str = Depends(source_command_key),
    service: WorkflowHttpService = Depends(workflow_service),
):
    try:
        return await service.replay(request.state.project_scope, task_id, key)
    except (LookupError, ValueError) as error:
        return _problem(request, error)


@router.get(
    "/{task_id}/browser-evidence/{evidence_id}",
    operation_id="workflow_browser_evidence",
    response_class=Response,
    responses={
        200: {
            "description": "Scoped browser evidence file",
            "content": {
                media_type: {"schema": {"type": "string", "format": "binary"}}
                for media_type in ("image/png", "application/zip", "application/json")
            },
        },
        404: problem_response_metadata("Browser evidence unavailable"),
    },
)
async def browser_evidence(task_id: str, evidence_id: str, request: Request):
    services = request.app.state.http_services
    if not isinstance(services.browser_client, BrowserApiClient) or services.workflow_store is None:
        return problem_response("workflow-control-unavailable", request)
    adapter = BrowserArtifactAdapter(services.workflow_store, services.browser_client)
    try:
        data, media_type = await adapter.read(request.state.project_scope, task_id, evidence_id)
    except (LookupError, BrowserApiError, ValueError, TimeoutError, OSError):
        return problem_response("workflow-not-found", request)
    return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})


@router.post(
    "/{task_id}/commands",
    operation_id="workflow_command",
    response_model=WorkflowCommandView,
    status_code=202,
)
async def command(
    task_id: str,
    body: WorkflowCommandRequest,
    request: Request,
    key: str = Depends(source_command_key),
    service: WorkflowHttpService = Depends(workflow_service),
):
    try:
        return await service.command(request.state.project_scope, task_id, body, key)
    except (LookupError, ValueError) as error:
        return _problem(request, error)


@router.get(
    "/{task_id}/nodes/{node_id}",
    operation_id="workflow_node",
    response_model=WorkflowNodeDetail,
)
async def node(
    task_id: str,
    node_id: str,
    request: Request,
    service: WorkflowHttpService = Depends(workflow_service),
):
    try:
        return await service.queries.node(request.state.project_scope, task_id, node_id)
    except LookupError as error:
        return _problem(request, error)


@router.get(
    "/{task_id}/nodes/{node_id}/logs",
    operation_id="workflow_node_logs",
    response_model=WorkflowLogPage,
)
async def logs(
    task_id: str,
    node_id: str,
    request: Request,
    attempt: int = Query(1, ge=1),
    after: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    service: WorkflowHttpService = Depends(workflow_service),
):
    try:
        return await service.queries.logs(
            request.state.project_scope, task_id, node_id, attempt, after, limit
        )
    except (LookupError, ValueError) as error:
        return _problem(request, error)
