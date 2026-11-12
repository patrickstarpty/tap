"""Budget-independent bound on coding turns without accepted browser progress."""


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


def sanitize_diagnostics(value: object) -> list[dict[str, object]]:
    """Treat coding workspace output as untrusted; persist no free-form strings."""
    tools = {
        "tap_browser",
        "read",
        "write",
        "edit",
        "bash",
        "glob",
        "grep",
        "task",
        "todowrite",
        "webfetch",
    }
    statuses = {"completed", "error", "pending"}
    categories = {
        "none",
        "validation_error",
        "permission_denied",
        "tool_timeout",
        "tool_error",
        "server_rejected",
        "unknown_outcome",
    }
    result: list[dict[str, object]] = []
    if not isinstance(value, list):
        return result
    for item in value[:32]:
        if not isinstance(item, dict):
            continue
        tool, status, category = item.get("tool"), item.get("status"), item.get("category")
        result.append(
            {
                "sequence": len(result) + 1,
                "tool": tool if isinstance(tool, str) and tool in tools else "other",
                "status": status if isinstance(status, str) and status in statuses else "pending",
                "category": category
                if isinstance(category, str) and category in categories
                else "none",
            }
        )
    return result
