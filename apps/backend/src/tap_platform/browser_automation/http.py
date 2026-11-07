"""Independent project-scoped browser API."""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal

from .objects import BrowserObjectStore
from .service import BrowserService, Conflict, Forbidden, Invalid
from .store import BrowserStore

_ACTIONS = frozenset(
    {
        "browser.sessions.create",
        "browser.sessions.read",
        "browser.sessions.cancel",
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

    @router.post("/sessions/{session_id}/actions", status_code=202)
    def create_action(
        project_id: str, session_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        actor = authorize(request, project_id)
        assert service is not None
        return invoke(
            lambda: service.create_action(actor, session_id, body, key(request))
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
            "result": "application/json",
        }[row["kind"]]
        return Response(
            data, media_type=mime, headers={"X-Content-SHA256": row["sha256"]}
        )

    return router


def create_browser_app() -> FastAPI:
    service, principal, token = browser_service_from_environment()
    app = FastAPI(title="TAP Browser API", version="0.1.0")
    app.include_router(create_browser_router(service, principal, token))

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "product": "tap-browser"}

    return app


app = create_browser_app()
