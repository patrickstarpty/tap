"""Merge explicit workflow intent with a model gateway route suggestion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RouteKind = Literal["native", "coding", "execute", "browser"]


@dataclass(frozen=True, slots=True)
class RouteDecision:
    kind: RouteKind
    reason: str
    confidence: float
    required_capabilities: tuple[str, ...]

    start_url: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"native", "coding", "execute", "browser"}:
            raise ValueError("invalid route kind")
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
        if decision.confidence < 0.5 and (
            decision.required_capabilities or decision.kind != "native"
        ):
            raise ValueError("clarification required before capability expansion")
    if decision.kind == "coding" and not coding_available:
        raise ValueError("coding capability unavailable")
    return decision


def requested_browser_url(goal: str, proposed: str | None) -> str | None:
    """Select only URLs present in the user goal, never model-invented origins."""
    urls = tuple(
        dict.fromkeys(
            value.rstrip(".,;!?，。；！？)）]")
            for value in re.findall(r"https?://[^\s<>\"']+", goal)
        )
    )
    if proposed is not None:
        return proposed if proposed in urls else None
    return urls[0] if len(urls) == 1 else None
