"""Bounded, free-form-free diagnostics shared by runtime and durable boundary."""


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
