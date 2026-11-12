"""Budget-independent bound on coding turns without accepted browser progress."""

from tap.modules.workflows.domain.coding_diagnostics import (
    sanitize_diagnostics as sanitize_diagnostics,
)


class CodingProgressGuard:
    """Only server-accepted first plan and completed actions reset the window."""

    def __init__(self) -> None:
        self._plan_seen = False
        self._observed = False
        self._completed = 0
        self._turns = 0
        self.stopped = False

    def allow(self, has_plan: bool, completed_actions: int, *, observed: bool = False) -> bool:
        if self.stopped:
            return False
        if (
            (has_plan and not self._plan_seen)
            or (observed and not self._observed)
            or completed_actions > self._completed
        ):
            self._turns = 0
        self._plan_seen = self._plan_seen or has_plan
        self._observed = self._observed or observed
        self._completed = max(self._completed, completed_actions)
        if self._turns >= 4:
            self.stopped = True
            return False
        self._turns += 1
        return True


def browser_progress_units(actions: list[dict[str, object]]) -> int:
    """Repeated observation is not progress; each business read condition counts once."""
    reads = {(a.get("step"), a.get("name")) for a in actions if a.get("type") == "read"}
    writes = sum(
        a.get("type") in {"navigate", "click", "fill", "select", "press", "scroll"} for a in actions
    )
    return len(reads) + writes
