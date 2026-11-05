"""Satisfaction rate with a minimum-sample threshold (Agent team view spec
section 2.3)."""

from __future__ import annotations

from typing import Literal

# Spec 2.3: fewer than 10 rated turns is "sample insufficient".
_MIN_RATED_TURNS_FOR_SATISFACTION = 10


def satisfaction(up: int, down: int) -> tuple[float | None, Literal["ok", "insufficient"]]:
    """Spec 2.3: `up / (up + down)`; fewer than 10 rated turns reports no
    value and `"insufficient"`."""
    total = up + down
    if total < _MIN_RATED_TURNS_FOR_SATISFACTION:
        return None, "insufficient"
    return up / total, "ok"
