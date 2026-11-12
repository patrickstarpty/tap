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
