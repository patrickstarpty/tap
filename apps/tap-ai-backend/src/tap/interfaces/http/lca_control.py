"""Flow-owned human panel adapter; all browser authority remains in shared control."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from tap.interfaces.http.browser_capabilities import BrowserCapabilities, BrowserGrant
from tap.modules.workflows.adapters.browser_client import BrowserApiClient, BrowserApiError

if TYPE_CHECKING:
    from tap.interfaces.http.routes.lca import LcaGateway


async def recording_control(
    gateway: LcaGateway, request: Request, project: str, flow: str, session: str, operation: str
) -> dict[str, Any]:
    scope = request.state.project_scope
    services = request.app.state.http_services
    client = services.browser_client
    if (
        not isinstance(client, BrowserApiClient)
        or client.project_id != project
        or not client.gateway_available
    ):
        raise HTTPException(503, "Recording browser gateway unavailable")
    capabilities = getattr(request.app.state, "lca_browser_capabilities", None)
    if capabilities is None:
        capabilities = BrowserCapabilities()
        request.app.state.lca_browser_capabilities = capabilities

    assert isinstance(capabilities, BrowserCapabilities)

    async def owned() -> dict[str, Any]:
        return await gateway.recording_state(project, scope.actor_id, flow, session)

    state = await owned()
    if operation == "capability":
        if state["control"]["control_state"] in {"closed", "blocked"}:
            raise HTTPException(409, "Recording control unavailable")
        controller = (
            state["control"].get("controller_id")
            if state["control"].get("controller_kind") == "human"
            else None
        )
        token, grant = capabilities.issue(
            project,
            flow,
            session,
            actor_id=scope.actor_id,
            controller_id=controller,
            replace_session=True,
        )
        return {"capability": token, "control": state["control"], "expiresInSeconds": 300}
    token = request.headers.get("x-lca-capability", "")

    async def authorize() -> BrowserGrant:
        grant = capabilities.verify(token, project, flow, session)
        if grant.actor_id != scope.actor_id:
            raise PermissionError("Recording capability unavailable")
        await owned()
        return capabilities.verify(token, project, flow, session)

    try:
        grant = await authorize()
        if operation == "frame":
            return await client.request("GET", f"/sessions/{client._id(session)}/frame")
        body = await request.json()
        if (
            not isinstance(body, dict)
            or type(body.get("expected_epoch")) is not int
            or body["expected_epoch"] < 0
        ):
            raise ValueError("Invalid recording epoch")
        key = request.headers.get("idempotency-key", "")
        if (
            not key
            or len(key) > 128
            or not key.isascii()
            or any(ord(c) < 33 or ord(c) > 126 for c in key)
        ):
            raise ValueError("Valid Idempotency-Key required")

        async def before_dispatch() -> None:
            await authorize()

        if (
            operation == "actions"
            and set(body) == {"action", "expected_epoch"}
            and isinstance(body["action"], dict)
        ):
            return await client.human_action(
                session,
                grant.controller_id,
                body["action"],
                body["expected_epoch"],
                key,
                expires_at=grant.expires_at,
                before_dispatch=before_dispatch,
            )
        if operation == "takeover" and set(body) == {"expected_epoch"}:
            return await client.human_control(
                session,
                grant.controller_id,
                "takeover",
                body["expected_epoch"],
                key,
                expires_at=grant.expires_at,
                before_dispatch=before_dispatch,
            )
        if operation == "stop" and set(body) == {"expected_epoch"}:
            await authorize()
            await asyncio.to_thread(
                client._sync_request,
                "POST",
                f"/sessions/{client._id(session)}/recording/stop",
                body,
                key,
                grant.controller_id,
                grant.expires_at,
            )
            return await owned()
        raise ValueError("Unsupported recording command")
    except PermissionError:
        raise HTTPException(403, "Recording capability unavailable") from None
    except BrowserApiError as exc:
        if operation == "frame" and exc.status == 409 and exc.detail == "live frame pending":
            raise HTTPException(409, {"code": "live_frame_pending"}) from None
        raise HTTPException(
            exc.status if exc.status in {403, 409, 422} else 502,
            "Recording command unavailable; reconcile the original request",
        ) from None
    except ValueError:
        raise HTTPException(422, "Invalid recording command") from None
