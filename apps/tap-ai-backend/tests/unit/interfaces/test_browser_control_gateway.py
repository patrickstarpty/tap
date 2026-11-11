from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tap.interfaces.http.browser_control_gateway import BrowserControlGateway
from tap.modules.workflows.adapters.browser_client import BrowserApiClient


def setup():
    scope = SimpleNamespace(project_id="p", actor_id="owner")
    queries = SimpleNamespace(snapshot=AsyncMock(return_value={"status": "running"}))
    client = SimpleNamespace(
        _id=BrowserApiClient._id,
        project_id="p",
        gateway_available=True,
        request=AsyncMock(return_value={"task_id": "t", "status": "active"}),
        get_control=AsyncMock(return_value={"control_state": "agent_active", "control_epoch": 0}),
        human_control=AsyncMock(return_value={"control_state": "takeover_pending"}),
    )
    return scope, queries, client, BrowserControlGateway(queries, client)


@pytest.mark.asyncio
async def test_gateway_rechecks_task_session_and_derives_controller():
    scope, queries, client, gateway = setup()
    issued = await gateway.issue(scope, "t", "s")
    await gateway.command(scope, "t", "s", issued["capability"], "takeover", 0, "k")
    assert client.human_control.call_args.args[1] == issued["controllerId"]
    client.request.return_value["task_id"] = "other"
    with pytest.raises(PermissionError):
        await gateway.state(scope, "t", "s", issued["capability"])


@pytest.mark.asyncio
async def test_termination_revokes_panel_and_actor_cannot_change():
    scope, queries, client, gateway = setup()
    issued = await gateway.issue(scope, "t", "s")
    with pytest.raises(PermissionError):
        await gateway.state(
            SimpleNamespace(project_id="p", actor_id="other"), "t", "s", issued["capability"]
        )
    queries.snapshot.return_value = {"status": "canceled"}
    with pytest.raises(PermissionError):
        await gateway.state(scope, "t", "s", issued["capability"])
    queries.snapshot.return_value = {"status": "running"}
    with pytest.raises(PermissionError):
        await gateway.state(scope, "t", "s", issued["capability"])


@pytest.mark.asyncio
async def test_capability_expiring_during_authorization_never_dispatches():
    from tap.interfaces.http.browser_capabilities import BrowserCapabilities

    scope, queries, client, gateway = setup()
    now = [0.0]
    gateway.capabilities = BrowserCapabilities(clock=lambda: now[0], ttl_seconds=1)
    issued = await gateway.issue(scope, "t", "s")

    async def expired_read(*args):
        now[0] = 2.0
        return {"task_id": "t", "status": "active"}

    client.request.side_effect = expired_read
    with pytest.raises(PermissionError):
        await gateway.command(scope, "t", "s", issued["capability"], "takeover", 0, "k")
    client.human_control.assert_not_awaited()
