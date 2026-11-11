"""Same-origin LCA proxy: trusted project identity stays server-side."""

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from tap.interfaces.http.scope import project_authorization

_ID = r"[A-Za-z0-9_-]{1,80}"
_ROUTES = {
    "GET": (
        rf"(?:flows/{_ID}/recordings(?:/{_ID})?|flows|flows/{_ID}|"
        rf"flows/{_ID}/runs|flows/{_ID}/releases|releases/{_ID}|runs/{_ID}|runs/{_ID}/evidence/{_ID}|assets)"
    ),
    "POST": (
        rf"(?:flows/{_ID}/recordings/{_ID}/cancel|flows/{_ID}/recordings|"
        rf"flows|flows/{_ID}/runs|flows/{_ID}/releases|releases/{_ID}/runs|runs/{_ID}/(?:cancel|reconcile)|assets)"
    ),
    "PUT": rf"flows/{_ID}/draft",
}


@dataclass(frozen=True)
class LcaGateway:
    base_url: str
    token: str
    transport: httpx.AsyncBaseTransport | None = None

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Invalid LCA backend origin")
        if not parsed.hostname or len(self.token) < 16:
            raise ValueError("LCA gateway origin and server credential required")

    @classmethod
    def from_environment(cls) -> "LcaGateway | None":
        origin = os.getenv("TAP_LCA_BACKEND_URL")
        token = os.getenv("TAP_LCA_GATEWAY_TOKEN")
        if not origin and not token:
            return None
        if not origin or not token:
            raise ValueError("LCA gateway settings must be configured together")
        return cls(origin, token)

    async def recording_state(
        self, project: str, actor: str, flow: str, session: str
    ) -> dict[str, Any]:
        url = (
            f"{self.base_url.rstrip('/')}/api/projects/{quote(project, safe='')}"
            f"/lca/flows/{flow}/recordings/{session}"
        )
        try:
            async with httpx.AsyncClient(
                transport=self.transport, follow_redirects=False, timeout=10
            ) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {self.token}", "X-LCA-Actor": actor}
                )
            if response.status_code != 200:
                raise HTTPException(
                    response.status_code if response.status_code in {403, 404, 409} else 502,
                    "Recording unavailable",
                )
            result = response.json()
            if not isinstance(result, dict):
                raise HTTPException(502, "Invalid recording state")
            return result
        except httpx.HTTPError:
            raise HTTPException(502, "Recording state unavailable") from None

    async def forward(self, request: Request, project_id: str, path: str) -> Response:
        scope = request.state.project_scope
        if scope.project_id != project_id:
            raise HTTPException(403, "LCA project forbidden")
        agent_preview = re.fullmatch(rf"agent-tasks/({_ID})/preview", path)
        if agent_preview:
            if request.method != "GET":
                raise HTTPException(405, "AA preview is read-only")
            from fastapi.responses import JSONResponse

            from tap.modules.workflows.adapters.browser_client import (
                BrowserApiClient,
                BrowserApiError,
            )
            from tap.modules.workflows.application.browser_artifacts import BrowserArtifactAdapter

            services = request.app.state.http_services
            if services.workflow_store is None or not isinstance(
                services.browser_client, BrowserApiClient
            ):
                raise HTTPException(503, "AA source unavailable")
            try:
                result = await BrowserArtifactAdapter(
                    services.workflow_store, services.browser_client
                ).lca_draft(scope, agent_preview[1])
            except (LookupError, BrowserApiError, ValueError, TimeoutError, OSError):
                raise HTTPException(404, "AA source or compatible draft unavailable") from None
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        control = re.fullmatch(
            rf"flows/({_ID})/recordings/({_ID})/(capability|frame|takeover|actions|stop)", path
        )
        if control:
            expected = "GET" if control[3] == "frame" else "POST"
            if request.method != expected:
                raise HTTPException(405, "Recording method unavailable")
            if len(await request.body()) > 16384:
                raise HTTPException(413, "Recording request too large")
            from fastapi.responses import JSONResponse

            from tap.interfaces.http.lca_control import recording_control

            result = await recording_control(
                self, request, project_id, control[1], control[2], control[3]
            )
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        if not re.fullmatch(_ROUTES.get(request.method, r"(?!)"), path):
            raise HTTPException(404, "LCA route unavailable")
        if path == "assets" and request.method == "POST":
            parts = []
            size = 0
            async for part in request.stream():
                size += len(part)
                if size > 350_000:
                    raise HTTPException(413, "LCA upload request too large")
                parts.append(part)
            body = b"".join(parts)
        else:
            body = await request.body()
        if len(body) > 1_000_000:
            raise HTTPException(413, "LCA request too large")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-LCA-Actor": scope.actor_id,
            "Content-Type": "application/json",
        }
        key = request.headers.get("idempotency-key")
        if key:
            headers["Idempotency-Key"] = key
        url = f"{self.base_url.rstrip('/')}/api/projects/{quote(project_id, safe='')}/lca/{path}"
        try:
            async with httpx.AsyncClient(
                transport=self.transport, follow_redirects=False, timeout=30
            ) as client:
                async with client.stream(
                    request.method, url, headers=headers, content=body
                ) as response:
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 12_000_000:
                            raise HTTPException(502, "LCA response exceeds limit")
                        chunks.append(chunk)
                    safe_headers = {"Cache-Control": "no-store"}
                    for name in (
                        "content-type",
                        "x-content-sha256",
                        "content-disposition",
                        "x-content-type-options",
                    ):
                        if name in response.headers:
                            safe_headers[name] = response.headers[name]
                    return Response(
                        b"".join(chunks), status_code=response.status_code, headers=safe_headers
                    )
        except httpx.HTTPError:
            raise HTTPException(
                502, "LCA request outcome unavailable; reconcile the original request"
            ) from None


async def authorize_lca(request: Request) -> None:
    # No inherited knowledge permission grants browser execution. Every request
    # is resolved against the current trusted project/actor and policy decision.
    path = request.path_params.get("path", "")
    if request.method == "GET":
        action = "lca.read"
    elif path.endswith("/releases"):
        action = "lca.publish"
    elif "/runs" in path or "/recordings" in path:
        action = "lca.execute"
    else:
        action = "lca.write"
    await project_authorization(action)(request)


router = APIRouter(dependencies=[Depends(authorize_lca)])


@router.get("/api/lca/context")
async def context(request: Request) -> dict[str, object]:
    return {
        "project_id": request.state.project_scope.project_id,
        "available": getattr(request.app.state, "lca_gateway", None) is not None,
    }


@router.get("/api/projects/{project_id}/lca/{path:path}", operation_id="lca_proxy_get")
@router.post("/api/projects/{project_id}/lca/{path:path}", operation_id="lca_proxy_post")
@router.put("/api/projects/{project_id}/lca/{path:path}", operation_id="lca_proxy_put")
async def proxy(project_id: str, path: str, request: Request) -> Response:
    gateway = getattr(request.app.state, "lca_gateway", None)
    if not isinstance(gateway, LcaGateway):
        raise HTTPException(503, "LCA capability unavailable")
    return await gateway.forward(request, project_id, path)
