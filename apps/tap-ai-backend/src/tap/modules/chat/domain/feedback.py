"""Immutable per-Turn answer feedback: up/down rating with an optional comment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Rating = Literal["up", "down"]


@dataclass(frozen=True, slots=True)
class AnswerFeedback:
    feedback_id: str
    project_id: str
    conversation_id: str
    turn_id: str
    actor_id: str
    trace_id: str | None
    rating: Rating | None
    comment: str | None
    created_at: datetime
    updated_at: datetime
