"""Bind browser recovery and embedded LCA to the same authorized API instance."""

import hashlib
import json

from fastapi import HTTPException, Request, Response

from tap.contracts.gateway_context import GatewayContext, GatewayUnavailableContext
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext


def gateway_context(
    request: Request,
    response: Response,
    scope: ProjectScopeContext,
    *,
    available: bool,
) -> GatewayContext | GatewayUnavailableContext:
    response.headers["Cache-Control"] = "no-store"
    if scope.identity_mode is not IdentityMode.VALIDATION:
        raise HTTPException(403, "Local context unavailable", headers={"Cache-Control": "no-store"})
    if not available:
        return GatewayUnavailableContext(project_id=scope.project_id)
    # The local validation identity is not enterprise authentication. Read Host
    # directly; caller-controlled forwarded headers cannot widen this boundary.
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "Local context unavailable", headers={"Cache-Control": "no-store"})
    instance_id = request.app.state.gateway_instance_id
    binding = hashlib.sha256(
        json.dumps(
            [
                "browser-context-v1",
                instance_id,
                scope.enterprise_id,
                scope.project_id,
                scope.actor_id,
                scope.identity_mode.value,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return GatewayContext(
        project_id=scope.project_id,
        actor_id=scope.actor_id,
        gateway_instance_id=instance_id,
        context_binding=binding,
    )
