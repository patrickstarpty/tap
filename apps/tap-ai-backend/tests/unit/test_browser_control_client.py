from unittest.mock import AsyncMock

import pytest

from tap.modules.workflows.adapters.browser_client import BrowserApiClient


@pytest.mark.asyncio
async def test_control_read_and_session_opt_in_are_explicit():
    client = BrowserApiClient("http://127.0.0.1:8123", "p", "a" * 32)
    client.request = AsyncMock(return_value={"control_epoch": 2})
    assert await client.get_control("session") == {"control_epoch": 2}
    client.request.assert_awaited_once_with("GET", "/sessions/session/control")
    client._create = AsyncMock(return_value=({"session_id": "s", "status": "active"}, False))
    client.poll = AsyncMock(return_value={"session_id": "s", "status": "active"})
    await client.session(
        "t", ["https://example.com"], 60, "k", control_version="browser-control-v1"
    )
    assert client._create.call_args.args[1]["control_version"] == "browser-control-v1"


@pytest.mark.asyncio
async def test_model_forward_reservation_is_explicit_and_settled():
    client = BrowserApiClient("http://127.0.0.1:8123", "p", "a" * 32)
    client.request = AsyncMock(return_value={"status": "reserved"})
    await client.reserve_model("s", 2, "request1")
    client.request.assert_awaited_with(
        "POST", "/sessions/s/model-reservations", {"expected_epoch": 2, "request_id": "request1"}
    )
    await client.settle_model("s", "request1", outcome="unknown")
    client.request.assert_awaited_with(
        "POST", "/sessions/s/model-reservations/request1/settle", {"outcome": "unknown"}
    )


@pytest.mark.asyncio
async def test_human_gateway_fails_closed_without_separate_credential():
    client = BrowserApiClient("http://127.0.0.1:8123", "p", "a" * 32)
    with pytest.raises(ValueError, match="gateway"):
        await client.human_control("s", "c" * 32, "takeover", 0, "key")


@pytest.mark.asyncio
async def test_controlled_value_action_preflights_without_exposing_value():
    client = BrowserApiClient("http://127.0.0.1:8123", "p", "a" * 32)
    client.request = AsyncMock(side_effect=[{"action_id": "pre"}, {"action_id": "fill"}])
    client.poll = AsyncMock(
        side_effect=[
            {
                "action_id": "pre",
                "status": "completed",
                "observation": {"extracted": {"preflight": {"safe": True}}},
            },
            {"status": "completed"},
        ]
    )
    action = {"type": "fill", "selector": "#name", "value": "synthetic", "expected_epoch": 2}
    await client.action("s", action, "key")
    first = client.request.call_args_list[0].args[2]
    assert first == {
        "type": "preflight",
        "operation": "fill",
        "selector": "#name",
        "expected_epoch": 2,
    }
    assert "synthetic" not in str(first)
    assert client.request.call_args_list[1].args[2]["preflight_action_id"] == "pre"
    assert "preflight_action_id" not in action


@pytest.mark.asyncio
async def test_human_input_preflight_omits_value_and_preserves_frame_binding():
    client = BrowserApiClient("http://127.0.0.1:8123", "p", "a" * 32, gateway_token="g" * 32)
    client._human_post = AsyncMock(side_effect=[{"action_id": "pre"}, {"action_id": "fill"}])
    client.poll = AsyncMock(
        side_effect=[
            {
                "action_id": "pre",
                "status": "completed",
                "observation": {"extracted": {"preflight": {"safe": True}}},
            },
            {"status": "completed"},
        ]
    )
    action = {
        "type": "fill",
        "x": 10,
        "y": 20,
        "value": "synthetic",
        "frame_sequence": 3,
        "viewport": {"width": 1280, "height": 800},
    }
    await client.human_action("s", "c" * 32, action, 2, "key")
    probe = client._human_post.call_args_list[0].args[2]
    assert "value" not in probe
    assert probe["frame_sequence"] == 3 and probe["x"] == 10
    assert client._human_post.call_args_list[1].args[2]["preflight_action_id"] == "pre"


@pytest.mark.asyncio
async def test_expired_capability_after_preflight_does_not_post_value():
    client = BrowserApiClient("http://127.0.0.1:8123", "p", "a" * 32, gateway_token="g" * 32)
    client._human_post = AsyncMock(return_value={"action_id": "pre"})
    client.poll = AsyncMock(
        return_value={
            "action_id": "pre",
            "status": "completed",
            "observation": {"extracted": {"preflight": {"safe": True}}},
        }
    )
    guard = AsyncMock(side_effect=[None, PermissionError("expired")])
    with pytest.raises(PermissionError):
        await client.human_action(
            "s",
            "c" * 32,
            {"type": "fill", "selector": "#name", "value": "secret"},
            0,
            "k",
            before_dispatch=guard,
        )
    assert client._human_post.await_count == 1
    assert "value" not in client._human_post.call_args.args[2]


@pytest.mark.asyncio
async def test_human_select_preflight_records_option_value_without_exposing_value():
    client = BrowserApiClient("http://127.0.0.1:8123", "p", "a" * 32, gateway_token="g" * 32)
    client._human_post = AsyncMock(side_effect=[{"action_id": "pre"}, {"action_id": "select"}])
    client.poll = AsyncMock(
        side_effect=[
            {
                "action_id": "pre",
                "status": "completed",
                "observation": {"extracted": {"preflight": {"safe": True}}},
            },
            {"status": "completed", "args": {"value": "us"}},
        ]
    )
    result = await client.human_action(
        "s",
        "c" * 32,
        {
            "type": "select",
            "selector": "#country",
            "value": "us",
            "frame_sequence": 2,
            "viewport": {"width": 1280, "height": 800},
        },
        1,
        "select-key",
    )
    probe = client._human_post.call_args_list[0].args[2]
    assert probe["operation"] == "select" and "value" not in probe
    assert result["args"] == {"value": "us"}


def test_trusted_owner_renewal_revokes_old_token_without_changing_controller():
    from tap.interfaces.http.browser_capabilities import BrowserCapabilities

    caps = BrowserCapabilities()
    old, first = caps.issue("p", "flow", "s", actor_id="alice")
    new, renewed = caps.issue(
        "p", "flow", "s", actor_id="alice", controller_id=first.controller_id, replace_session=True
    )
    assert renewed.controller_id == first.controller_id
    with pytest.raises(PermissionError):
        caps.verify(old, "p", "flow", "s")
    assert caps.verify(new, "p", "flow", "s").actor_id == "alice"
    with pytest.raises(PermissionError):
        caps.verify(new, "p", "other", "s")
    with pytest.raises(ValueError):
        caps.issue("p", "flow", "s", actor_id="alice", controller_id="forged", replace_session=True)
