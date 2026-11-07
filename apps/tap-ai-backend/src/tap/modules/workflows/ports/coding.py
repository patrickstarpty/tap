"""Frozen browser-coding task result boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class CodingResult:
    status: Literal["passed", "failed", "blocked", "canceled", "unknown"]
    provider_session_id: str | None = None
    bundle_ref: str | None = None
    execution_ref: str | None = None
    usage: dict[str, object] = field(default_factory=dict)
    terminal_evidence: dict[str, Any] = field(default_factory=dict)
    input_request: dict[str, str] | None = None
    replay_model_calls: int = 0
