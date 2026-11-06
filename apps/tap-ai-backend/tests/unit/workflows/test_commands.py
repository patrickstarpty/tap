"""Control commands preserve version and receipt semantics."""

import pytest

from tap.modules.workflows.domain.commands import ControlCommand, command_disposition


def test_stale_plan_cannot_apply_new_instruction() -> None:
    assert command_disposition(2, 3, False) == "stale"
    assert command_disposition(2, 3, True) == "applied"


def test_future_plan_revision_is_rejected() -> None:
    assert command_disposition(4, 3, False) == "rejected"
    assert command_disposition(3, 3, False) == "pending"


def test_command_requires_actionable_target_and_text() -> None:
    with pytest.raises(ValueError):
        ControlCommand("one", "task", None, "steer", 1, None)
    with pytest.raises(ValueError):
        ControlCommand("two", "task", None, "retry", 1, None)
    with pytest.raises(ValueError):
        ControlCommand("three", "task", "node", "cancel", 1, None)
