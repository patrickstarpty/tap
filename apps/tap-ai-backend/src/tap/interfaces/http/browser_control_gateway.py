"""Trusted task authorization bridge for ephemeral browser panels."""

from __future__ import annotations

from typing import Any

from tap.interfaces.http.browser_capabilities import BrowserCapabilities, BrowserGrant
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.adapters.browser_client import BrowserApiClient
from tap.modules.workflows.application.queries import WorkflowQueries


class BrowserControlGateway:
    def __init__(
        self,
        queries: WorkflowQueries,
        client: BrowserApiClient,
        capabilities: BrowserCapabilities | None = None,
    ) -> None:
        self.queries = queries
        self.client = client
        self.capabilities = capabilities or BrowserCapabilities()

    async def _authorize(self, scope: ProjectScopeContext, task_id: str, session_id: str) -> None:
        if scope.project_id != self.client.project_id or not self.client.gateway_available:
            raise PermissionError("browser control unavailable")
        snapshot = await self.queries.snapshot(scope, task_id)
        if snapshot.get("status") not in {"running", "pausing", "paused", "awaiting_input"}:
            self.capabilities.revoke_session(scope.project_id, session_id)
            raise PermissionError("browser task is not active")
        session = await self.client.request("GET", f"/sessions/{self.client._id(session_id)}")
        if session.get("task_id") != task_id or session.get("status") != "active":
            self.capabilities.revoke_session(scope.project_id, session_id)
            raise PermissionError("browser session unavailable")

    async def issue(
        self, scope: ProjectScopeContext, task_id: str, session_id: str
    ) -> dict[str, Any]:
        await self._authorize(scope, task_id, session_id)
        control = await self.client.get_control(session_id)
        if control.get("control_state") in {"closed", "blocked"}:
            raise PermissionError("browser control unavailable")
        token, grant = self.capabilities.issue(
            scope.project_id, task_id, session_id, actor_id=scope.actor_id
        )
        return {
            "capability": token,
            "controllerId": grant.controller_id,
            "expiresInSeconds": 300,
            "control": control,
        }

    async def _grant(
        self, scope: ProjectScopeContext, task_id: str, session_id: str, token: str
    ) -> BrowserGrant:
        grant = self.capabilities.verify(token, scope.project_id, task_id, session_id)
        if grant.actor_id != scope.actor_id:
            raise PermissionError("browser capability unavailable")
        await self._authorize(scope, task_id, session_id)
        # Authorization I/O may outlive or revoke the original grant.
        return self.capabilities.verify(token, scope.project_id, task_id, session_id)

    async def state(
        self, scope: ProjectScopeContext, task_id: str, session_id: str, token: str
    ) -> dict[str, Any]:
        await self._grant(scope, task_id, session_id, token)
        result = await self.client.get_control(session_id)
        if result.get("control_state") == "closed":
            self.capabilities.revoke_session(scope.project_id, session_id)
        return result

    async def command(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        session_id: str,
        token: str,
        operation: str,
        expected_epoch: int,
        key: str,
    ) -> dict[str, Any]:
        grant = await self._grant(scope, task_id, session_id, token)

        async def before_dispatch() -> None:
            await self._grant(scope, task_id, session_id, token)

        return await self.client.human_control(
            session_id,
            grant.controller_id,
            operation,
            expected_epoch,
            key,
            expires_at=grant.expires_at,
            before_dispatch=before_dispatch,
        )

    async def frame(
        self, scope: ProjectScopeContext, task_id: str, session_id: str, token: str
    ) -> dict[str, Any]:
        await self._grant(scope, task_id, session_id, token)
        return await self.client.request("GET", f"/sessions/{self.client._id(session_id)}/frame")

    async def action(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        session_id: str,
        token: str,
        action: dict[str, Any],
        expected_epoch: int,
        key: str,
    ) -> dict[str, Any]:
        grant = await self._grant(scope, task_id, session_id, token)

        async def before_dispatch() -> None:
            await self._grant(scope, task_id, session_id, token)

        return await self.client.human_action(
            session_id,
            grant.controller_id,
            action,
            expected_epoch,
            key,
            expires_at=grant.expires_at,
            before_dispatch=before_dispatch,
        )
