import pytest

from tap.modules.workflows.domain.transitions import transition


@pytest.mark.parametrize(
    ("current", "signal", "expected"),
    [
        ("queued", "claim", "running"),
        ("waiting_dependency", "dependency_ready", "queued"),
        ("running", "dependency_wait", "waiting_dependency"),
        ("running", "input_needed", "awaiting_input"),
        ("awaiting_input", "input_applied", "queued"),
        ("running", "pause_requested", "pausing"),
        ("pausing", "runtime_paused", "paused"),
        ("paused", "resume_requested", "queued"),
        ("running", "lease_lost", "recovering"),
        ("recovering", "reconciled", "queued"),
        ("running", "cancel_requested", "canceling"),
        ("canceling", "runtime_canceled", "canceled"),
        ("running", "runtime_succeeded", "succeeded"),
        ("running", "runtime_failed", "failed"),
        ("waiting_dependency", "plan_skipped", "skipped"),
    ],
)
def test_allowed_transitions(current: str, signal: str, expected: str) -> None:
    assert transition(current, signal) == expected


@pytest.mark.parametrize(
    ("current", "signal"),
    [
        ("running", "runtime_paused"),
        ("running", "runtime_canceled"),
        ("pausing", "runtime_succeeded"),
        ("succeeded", "runtime_failed"),
        ("failed", "claim"),
        ("canceled", "resume_requested"),
        ("skipped", "claim"),
        ("unknown", "claim"),
        ("queued", "unknown"),
    ],
)
def test_unconfirmed_or_terminal_transitions_are_rejected(current: str, signal: str) -> None:
    with pytest.raises(ValueError):
        transition(current, signal)
