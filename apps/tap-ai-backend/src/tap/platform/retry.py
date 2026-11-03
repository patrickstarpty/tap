"""Bounded exponential backoff with full jitter shared by retryable TAP AI jobs."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base: timedelta = field(default=timedelta(seconds=2))
    cap: timedelta = field(default=timedelta(seconds=300))

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base <= timedelta(0):
            raise ValueError("base must be positive")
        if self.cap < self.base:
            raise ValueError("cap must not be smaller than base")

    def ceiling(self, attempt: int) -> timedelta:
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        # Clamp the exponent so huge attempt counts cannot overflow timedelta.
        exponent = min(attempt - 1, 64)
        base_seconds = self.base.total_seconds()
        cap_seconds = self.cap.total_seconds()
        return timedelta(seconds=min(cap_seconds, base_seconds * 2**exponent))

    def next_delay(self, attempt: int, *, rng: random.Random | None = None) -> timedelta:
        ceiling = self.ceiling(attempt).total_seconds()
        source = rng if rng is not None else random
        return timedelta(seconds=source.uniform(0, ceiling))

    def is_exhausted(self, attempt: int) -> bool:
        return attempt >= self.max_attempts
