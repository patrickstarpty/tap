"""Merge explicit workflow intent with a model gateway route suggestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RouteKind = Literal["native", "coding", "execute"]


@dataclass(frozen=True, slots=True)
class RouteDecision:
    kind: RouteKind
    reason: str
    confidence: float
    required_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("route confidence must be between zero and one")


def requested_delegate(
    selected_id: str | None, allowed_ids: tuple[str, ...]
) -> dict[str, str | None]:
    if selected_id is not None and (
        selected_id == "orchestrator" or selected_id not in allowed_ids
    ):
        raise ValueError("delegate unavailable")
    return {"root": "orchestrator", "delegate": selected_id}


def merge_route(
    *,
    explicit_kind: RouteKind | None,
    suggested: RouteDecision | None,
    coding_available: bool,
) -> RouteDecision:
    if explicit_kind is not None:
        decision = RouteDecision(
            explicit_kind,
            "explicit intent",
            1.0,
            ("code.write",) if explicit_kind == "coding" else (),
        )
    else:
        decision = suggested or RouteDecision("native", "default assistant", 1.0, ())
        if decision.confidence < 0.5 and decision.required_capabilities:
            raise ValueError("clarification required before capability expansion")
    if decision.kind == "coding" and not coding_available:
        raise ValueError("coding capability unavailable")
    return decision
