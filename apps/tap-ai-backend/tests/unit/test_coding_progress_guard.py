import pytest

from tap.modules.workflows.adapters.coding_progress_guard import CodingProgressGuard


def test_no_progress_stops_before_fifth_request_not_twenty_fifth():
    guard = CodingProgressGuard()
    assert [guard.allow(False, 0) for _ in range(25)] == [True] * 4 + [False] * 21


def test_first_plan_resets_but_repeated_plan_edits_do_not():
    guard = CodingProgressGuard()
    for _ in range(4):
        assert guard.allow(False, 0)
    for _ in range(4):
        assert guard.allow(True, 0)
    assert not guard.allow(True, 0)


def test_normal_successful_actions_reset_window():
    guard = CodingProgressGuard()
    for completed_actions in range(30):
        for _ in range(4):
            assert guard.allow(True, completed_actions)
    assert not guard.allow(True, 29)


def test_failed_actions_cannot_reset_guard_and_stop_is_sticky():
    guard = CodingProgressGuard()
    for _ in range(4):
        assert guard.allow(True, 0)
    assert not guard.allow(True, 0)
    assert not guard.allow(True, 1)


def test_diagnostics_drop_arbitrary_strings_extra_keys_and_bound_entries():
    from tap.modules.workflows.adapters.coding_progress_guard import sanitize_diagnostics

    assert sanitize_diagnostics(None) == []
    source = [{"tool": "SECRET", "status": {}, "category": "token=SECRET", "args": "SECRET"}] * 40
    cleaned = sanitize_diagnostics(source)
    assert len(cleaned) == 32
    assert cleaned[0] == {"sequence": 1, "tool": "other", "status": "pending", "category": "none"}
    assert "SECRET" not in str(cleaned)


def test_completed_transport_rejection_categories_survive_persistence_sanitizer():
    from tap.modules.workflows.adapters.coding_progress_guard import sanitize_diagnostics

    for category in ("server_rejected", "unknown_outcome"):
        result = sanitize_diagnostics(
            [{"tool": "tap_browser", "status": "completed", "category": category}]
        )
        assert result[0]["category"] == category
        assert result[0]["status"] == "completed"


def test_only_first_trusted_discovery_observation_resets_window():
    guard = CodingProgressGuard()
    for _ in range(4):
        assert guard.allow(False, 0)
    for _ in range(4):
        assert guard.allow(False, 0, observed=True)
    assert not guard.allow(False, 0, observed=True)


def test_snapshots_waits_and_repeated_read_do_not_create_progress_units():
    from tap.modules.workflows.adapters.coding_progress_guard import browser_progress_units

    actions = [{"type": "snapshot"}, {"type": "wait"}] * 20
    assert browser_progress_units(actions) == 0
    actions += [{"type": "read", "step": "verify", "name": "amount"}] * 20
    assert browser_progress_units(actions) == 1
    actions += [{"type": "read", "step": "verify2", "name": "age"}, {"type": "click"}]
    assert browser_progress_units(actions) == 3


def test_preflight_rejection_and_fresh_snapshots_do_not_extend_four_turn_window():
    # The client never emits a business receipt for rejected input preflight;
    # BrowserProgress records only the successful read-only snapshots afterward.
    from tap.modules.workflows.adapters.coding_progress_guard import browser_progress_units

    guard = CodingProgressGuard()
    recorded = []
    for _ in range(4):
        assert guard.allow(True, browser_progress_units(recorded), observed=True)
        recorded.append({"type": "snapshot"})
    assert not guard.allow(True, browser_progress_units(recorded), observed=True)
    # A late claimed completion cannot revive the already stopped session.
    assert not guard.allow(True, 1, observed=True)


@pytest.mark.asyncio
async def test_real_progress_preflight_rejections_and_correction_observations_stop():
    from tap.modules.workflows.adapters.coding_progress_guard import browser_progress_units
    from tap.modules.workflows.application.browser_steps import BrowserProgress

    async def emit(*_args):
        pass

    progress = BrowserProgress(emit, discovery_session_id="session-a")
    guard = CodingProgressGuard()
    sequence = 0
    writes = 0

    async def execute(action):
        nonlocal sequence, writes
        sequence += 1
        if action["type"] == "fill":
            writes += 1
            return {
                "status": "preflight_rejected",
                "business_action_admitted": False,
                "operation": "fill",
                "session_id": "session-a",
                "control_epoch": 0,
                "preflight_action_id": f"probe-{sequence}",
                "observation_sequence": sequence,
                "category": "target_missing",
            }
        return {
            "status": "completed",
            "session_id": "session-a",
            "control_epoch": 0,
            "action_id": f"snapshot-{sequence}",
            "observation": {
                "session_id": "session-a",
                "sequence": sequence,
                "url": "https://example.com",
                "bounded_snapshot": "Input amount",
                "error": None,
            },
        }

    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "input", "keyword": "When", "title": "Enter amount"},
            ],
        },
        "plan",
        execute,
    )
    for attempt in range(25):
        if not guard.allow(True, browser_progress_units(progress.recorded_actions), observed=True):
            break
        rejected = await progress.handle(
            {"type": "fill", "step": "input", "selector": "#missing", "value": "3"},
            f"fill-{attempt}",
            execute,
        )
        assert rejected["code"] == "preflight_rejected"
        await progress.handle({"type": "snapshot", "step": "input"}, f"observe-{attempt}", execute)
    assert writes == 4
    assert guard.stopped
    assert not progress.unknown_action
    assert browser_progress_units(progress.recorded_actions) == 0
