import pytest

from tap.modules.workflows.application.browser_steps import BrowserProgress


@pytest.fixture
def discovery():
    events, calls = [], []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(action):
        calls.append(action)
        return {
            "status": "completed",
            "session_id": "s",
            "action_id": f"receipt-{len(calls)}",
            "observation": {
                "session_id": "s",
                "sequence": len(calls),
                "url": "https://example.com/redirected",
                "bounded_snapshot": "Observed page",
            },
        }

    p = BrowserProgress(
        emit, frozen_start_url="https://example.com/start", discovery_session_id="s"
    )
    return p, calls, events, execute


@pytest.mark.asyncio
async def test_discovery_preserves_receipts_and_adds_explicit_given_once(discovery):
    p, calls, events, execute = discovery
    await p.handle({"type": "navigate", "url": "https://example.com/start"}, "n", execute)
    await p.handle({"type": "snapshot"}, "s", execute)
    assert p.observation_progress
    assert not p.steps and not p.live_complete
    assert events[0][0] == "browser.discovery_started"
    assert p.recorded_actions[0]["url"] == "https://example.com/start"
    assert p.recorded_actions[0]["action_id"] == "receipt-1"
    result = await p.handle(
        {"type": "plan", "steps": [{"id": "result", "keyword": "Then", "title": "Verify"}]},
        "plan",
        execute,
    )
    assert result["status"] == "planned"
    assert p.steps["tap_discovery"]["keyword"] == "Given"
    assert len(calls) == 2 and len(p.recorded_actions) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        {"type": "click", "selector": "#go"},
        {"type": "read", "selector": "#x", "name": "x"},
        {"type": "navigate", "url": "https://example.com/other"},
        {"type": "navigate", "url": "https://evil.example/start"},
    ],
)
async def test_preplan_denials_do_not_dispatch(discovery, action):
    p, calls, _, execute = discovery
    assert "error" in await p.handle(action, "bad", execute)
    assert not calls


@pytest.mark.asyncio
async def test_preplan_navigate_consumed_on_unknown_and_unknown_blocks_plan(discovery):
    p, _, _, _ = discovery

    async def unknown(action):
        return {"status": "unknown"}

    await p.handle({"type": "navigate", "url": "https://example.com/start"}, "n", unknown)
    assert p.unknown_action
    assert (await p.handle({"type": "snapshot"}, "s", unknown))["status"] == "unknown"
    assert (await p.handle({"type": "plan", "steps": []}, "p", unknown))["status"] == "unknown"


@pytest.mark.asyncio
async def test_spoofed_or_stale_observation_does_not_gain_progress(discovery):
    p, _, _, _ = discovery

    async def wrong(action):
        return {
            "status": "completed",
            "session_id": "other",
            "observation": {
                "session_id": "other",
                "sequence": 1,
                "url": "https://example.com/",
                "bounded_snapshot": "page",
            },
        }

    await p.handle({"type": "snapshot"}, "s", wrong)
    assert not p.observation_progress and not p.recorded_actions


@pytest.mark.asyncio
async def test_discovery_slot_and_reserved_step_cannot_be_reused_after_plan(discovery):
    p, calls, _, execute = discovery
    await p.handle({"type": "navigate", "url": "https://example.com/start"}, "n", execute)
    result = await p.handle(
        {"type": "plan", "steps": [{"id": "tap_discovery", "keyword": "Given", "title": "Forged"}]},
        "bad",
        execute,
    )
    assert "error" in result and not p.steps
    await p.handle(
        {"type": "plan", "steps": [{"id": "open", "keyword": "Given", "title": "Observe"}]},
        "p",
        execute,
    )
    assert "error" in await p.handle(
        {"type": "navigate", "url": "https://example.com/start", "step": "open"}, "repeat", execute
    )
    assert "error" in await p.handle(
        {"type": "snapshot", "step": "tap_discovery"}, "system", execute
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_discovery_navigation_slot_consumed_before_exception(discovery):
    p, calls, _, execute = discovery

    async def fail(action):
        raise RuntimeError("transport failed")

    with pytest.raises(RuntimeError):
        await p.handle({"type": "navigate", "url": "https://example.com/start"}, "n", fail)
    assert "error" in await p.handle(
        {"type": "navigate", "url": "https://example.com/start"}, "again", execute
    )
    assert not calls


@pytest.mark.asyncio
async def test_stale_sequence_cannot_create_discovery_evidence(discovery):
    p, _, events, _ = discovery
    p.last_sequence = 4

    async def stale(action):
        return {
            "status": "completed",
            "session_id": "s",
            "observation": {
                "session_id": "s",
                "sequence": 4,
                "bounded_snapshot": "old",
                "url": "https://example.com/",
            },
        }

    result = await p.handle({"type": "snapshot"}, "stale", stale)
    assert "error" in result
    assert not p.observation_progress and not p.recorded_actions
    assert events[-1][1]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_discovery_system_step_counts_toward_plan_limit(discovery):
    p, _, _, execute = discovery
    await p.handle({"type": "snapshot"}, "s", execute)
    steps = [{"id": f"s{i}", "keyword": "Then", "title": "Check"} for i in range(24)]
    assert "error" in await p.handle({"type": "plan", "steps": steps}, "p", execute)
    assert not p.steps
    assert (await p.handle({"type": "plan", "steps": steps[:23]}, "ok", execute))[
        "status"
    ] == "planned"
    assert len(p.steps) == 24


@pytest.mark.asyncio
async def test_discovery_rejects_missing_or_duplicate_receipt_identity(discovery):
    p, _, _, execute = discovery
    await p.handle({"type": "snapshot"}, "first", execute)

    async def duplicate(action):
        return {
            "status": "completed",
            "action_id": "receipt-1",
            "session_id": "s",
            "observation": {"session_id": "s", "sequence": 2, "bounded_snapshot": "page"},
        }

    assert "error" in await p.handle({"type": "snapshot"}, "duplicate", duplicate)

    async def missing(action):
        return {
            "status": "completed",
            "session_id": "s",
            "observation": {"session_id": "s", "sequence": 3, "bounded_snapshot": "page"},
        }

    assert "error" in await p.handle({"type": "snapshot"}, "missing", missing)
    assert len(p.recorded_actions) == 1
