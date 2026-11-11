"""Independent project-scoped browser API."""

from __future__ import annotations

import os
import time
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal

from .control import ControlActor
from .objects import BrowserObjectStore
from .service import BrowserService, Conflict, Forbidden, Invalid
from .store import BrowserStore

_ACTIONS = frozenset(
    {
        "browser.sessions.create",
        "browser.sessions.read",
        "browser.sessions.cancel",
        "browser.sessions.control",
        "browser.actions.create",
        "browser.actions.read",
        "browser.bundles.create",
        "browser.bundles.read",
        "browser.executions.create",
        "browser.executions.read",
        "browser.executions.cancel",
        "browser.evidence.read",
    }
)


def browser_service_from_environment() -> tuple[
    BrowserService | None, AccessPrincipal | None, str | None
]:
    names = (
        "TAP_BROWSER_DATABASE_URL",
        "TAP_BROWSER_OBJECT_ROOT",
        "TAP_BROWSER_PROJECT_ID",
        "TAP_BROWSER_ACCESS_TOKEN",
        "TAP_BROWSER_TOKEN_EXPIRES_AT",
    )
    values = {name: os.getenv(name) for name in names}
    if not any(values.values()):
        return None, None, None
    if not all(values.values()):
        raise RuntimeError("TAP browser settings must be configured together")
    database = values["TAP_BROWSER_DATABASE_URL"]
    root = values["TAP_BROWSER_OBJECT_ROOT"]
    project = values["TAP_BROWSER_PROJECT_ID"]
    token = values["TAP_BROWSER_ACCESS_TOKEN"]
    expiry = values["TAP_BROWSER_TOKEN_EXPIRES_AT"]
    assert database and root and project and token and expiry
    if not database.startswith("mysql+pymysql://"):
        raise RuntimeError("TAP_BROWSER_DATABASE_URL requires synchronous PyMySQL")
    if len(token) < 16:
        raise RuntimeError("TAP_BROWSER_ACCESS_TOKEN too short")
    principal = AccessPrincipal(
        project_id=project,
        principal_id="tap-ai-browser-service",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime.fromisoformat(expiry),
        actions=_ACTIONS,
        enabled=True,
    )
    return (
        BrowserService(
            BrowserStore(create_engine(database, pool_pre_ping=True)),
            BrowserObjectStore(Path(root)),
        ),
        principal,
        token,
    )


def create_browser_router(
    service: BrowserService | None = None,
    principal: AccessPrincipal | None = None,
    token: str | None = None,
    *,
    gateway_token: str | None = None,
    worker_token: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/browser", tags=["browser"])

    def authorize(request: Request, project_id: str) -> AccessPrincipal:
        if service is None or principal is None or token is None:
            raise HTTPException(503, "browser capability unavailable")
        presented = request.headers.get("authorization", "")
        if not presented.startswith("Bearer ") or not secrets.compare_digest(
            presented[7:], token
        ):
            raise HTTPException(401, "invalid browser credential")
        if project_id != principal.project_id:
            raise HTTPException(403, "browser project forbidden")
        return principal

    def gateway_actor(
        request: Request, project_id: str
    ) -> tuple[AccessPrincipal, ControlActor]:
        if gateway_token is None or service is None or principal is None:
            raise HTTPException(503, "browser human control unavailable")
        if len(gateway_token) < 32 or gateway_token == token:
            raise HTTPException(503, "browser gateway credential invalid")
        presented = request.headers.get("authorization", "")
        if not secrets.compare_digest(presented, f"Bearer {gateway_token}"):
            raise HTTPException(401, "invalid browser gateway credential")
        if project_id != principal.project_id:
            raise HTTPException(403, "browser project forbidden")
        controller = request.headers.get("x-tap-controller", "")
        if not re.fullmatch(r"[a-f0-9]{32}", controller):
            raise HTTPException(422, "invalid delegated controller")
        try:
            expires = float(request.headers.get("x-tap-controller-expires", ""))
            if not time.time() < expires <= time.time() + 301:
                raise ValueError("expired")
            expires_at = datetime.fromtimestamp(expires, UTC).isoformat()
        except (ValueError, OverflowError):
            raise HTTPException(401, "browser capability expired") from None
        return principal, ControlActor("human", controller, expires_at=expires_at)

    def key(request: Request) -> str:
        value = request.headers.get("idempotency-key")
        if value is None:
            raise HTTPException(400, "Idempotency-Key required")
        return value

    def invoke(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except Forbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except (Invalid, ValueError, FileNotFoundError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/sessions", status_code=202)
    def create_session(
        project_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.create_session(actor, body, key(request)))

    @router.get("/sessions/{session_id}")
    def get_session(
        project_id: str, session_id: str, request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.get_session(actor, session_id))

    @router.post("/sessions/{session_id}/cancel", status_code=202)
    def cancel_session(
        project_id: str, session_id: str, request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.cancel_session(actor, session_id))

    @router.post("/sessions/{session_id}/model-reservations")
    def reserve_model(
        project_id: str, session_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        if (
            set(body) != {"expected_epoch", "request_id"}
            or type(body["expected_epoch"]) is not int
            or not isinstance(body["request_id"], str)
        ):
            raise HTTPException(422, "invalid model reservation")
        assert service is not None
        return invoke(
            lambda: service.reserve_model(
                actor, session_id, body["expected_epoch"], body["request_id"]
            )
        )

    @router.post("/sessions/{session_id}/model-reservations/{request_id}/settle")
    def settle_model(
        project_id: str,
        session_id: str,
        request_id: str,
        body: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        if set(body) != {"outcome"} or body["outcome"] not in {"completed", "unknown"}:
            raise HTTPException(422, "invalid model settlement")
        assert service is not None
        return invoke(
            lambda: service.settle_model(
                actor, session_id, request_id, outcome=body["outcome"]
            )
        )

    @router.post("/sessions/{session_id}/actions/{action_id}/input")
    def consume_input(
        project_id: str, session_id: str, action_id: str, request: Request
    ) -> Response:
        from fastapi.responses import JSONResponse

        if (
            service is None
            or principal is None
            or worker_token is None
            or len(worker_token) < 32
            or worker_token in {token, gateway_token}
        ):
            raise HTTPException(503, "browser input channel unavailable")
        if not secrets.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {worker_token}"
        ):
            raise HTTPException(401, "invalid browser worker credential")
        if project_id != principal.project_id:
            raise HTTPException(403, "browser project forbidden")
        value = invoke(
            lambda: service.consume_input_for_dispatch(
                project_id, session_id, action_id
            )
        )
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @router.get("/sessions/{session_id}/frame")
    def live_frame(
        project_id: str, session_id: str, request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        control = invoke(lambda: service.request_observer(actor, session_id))
        try:
            if service.frames is None:
                raise LookupError("frame not ready")
            return service.frames.read(
                session_id, control_epoch=control["control_epoch"]
            )
        except LookupError as exc:
            raise HTTPException(409, "live frame pending") from exc

    @router.post("/sessions/{session_id}/human-actions", status_code=202)
    def human_action(
        project_id: str, session_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        principal_value, actor = gateway_actor(request, project_id)
        if body.get("type") not in {
            "click",
            "fill",
            "select",
            "navigate",
            "scroll",
            "preflight",
        }:
            raise HTTPException(422, "unsupported human action")
        assert service is not None
        return invoke(
            lambda: service.create_action(
                principal_value, session_id, body, key(request), actor=actor
            )
        )

    @router.get("/sessions/{session_id}/control")
    def get_control(
        project_id: str, session_id: str, request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.get_control(actor, session_id))

    @router.post("/sessions/{session_id}/control", status_code=202)
    def request_control(
        project_id: str, session_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        principal_value, actor = gateway_actor(request, project_id)
        if (
            set(body) != {"operation", "expected_epoch"}
            or body["operation"] not in {"takeover", "handback"}
            or type(body["expected_epoch"]) is not int
        ):
            raise HTTPException(422, "invalid browser control request")
        assert service is not None
        return invoke(
            lambda: service.request_control(
                principal_value,
                session_id,
                body["operation"],
                body["expected_epoch"],
                key(request),
                actor=actor,
            )
        )

    @router.post("/sessions/{session_id}/actions", status_code=202)
    def create_action(
        project_id: str, session_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(
            lambda: service.create_action(actor, session_id, body, key(request))
        )

    @router.get("/sessions/{session_id}/actions")
    def list_actions(
        project_id: str,
        session_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=200),
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(
            lambda: service.list_actions(actor, session_id, after=after, limit=limit)
        )

    @router.post("/sessions/{session_id}/recording/stop", status_code=202)
    def stop_recording(
        project_id: str,
        session_id: str,
        body: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        principal_value, actor = gateway_actor(request, project_id)
        if set(body) != {"expected_epoch"} or type(body["expected_epoch"]) is not int:
            raise HTTPException(422, "invalid recording stop request")
        assert service is not None
        return invoke(
            lambda: service.stop_recording(
                principal_value,
                session_id,
                body["expected_epoch"],
                key(request),
                actor=actor,
            )
        )

    @router.get("/sessions/{session_id}/actions/{action_id}")
    def get_action(
        project_id: str, session_id: str, action_id: str, request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.get_action(actor, session_id, action_id))

    @router.post("/bundles", status_code=201)
    def create_bundle(
        project_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.create_bundle(actor, body, key(request)))

    @router.get("/bundles/{bundle_id}")
    def get_bundle(project_id: str, bundle_id: str, request: Request) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.get_bundle(actor, bundle_id))

    @router.post("/executions", status_code=202)
    def create_execution(
        project_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.create_execution(actor, body, key(request)))

    @router.get("/executions/{execution_id}")
    def get_execution(
        project_id: str, execution_id: str, request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.get_execution(actor, execution_id))

    @router.post("/executions/{execution_id}/cancel", status_code=202)
    def cancel_execution(
        project_id: str, execution_id: str, request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(lambda: service.cancel_execution(actor, execution_id))

    @router.get("/evidence/{evidence_id}")
    def read_evidence(project_id: str, evidence_id: str, request: Request) -> Response:
        actor = authorize(request, project_id)
        assert service is not None
        row, data = invoke(lambda: service.read_evidence(actor, evidence_id))
        mime = {
            "screenshot": "image/png",
            "trace": "application/zip",
            "video": "video/webm",
            "result": "application/json",
        }[row["kind"]]
        return Response(
            data, media_type=mime, headers={"X-Content-SHA256": row["sha256"]}
        )

    return router


def create_browser_app() -> FastAPI:
    service, principal, token = browser_service_from_environment()
    app = FastAPI(title="TAP Browser API", version="0.1.0")
    app.include_router(
        create_browser_router(
            service,
            principal,
            token,
            gateway_token=os.getenv("TAP_BROWSER_GATEWAY_TOKEN"),
            worker_token=os.getenv("TAP_BROWSER_WORKER_TOKEN"),
        )
    )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "product": "tap-browser"}

    return app


app = create_browser_app()
