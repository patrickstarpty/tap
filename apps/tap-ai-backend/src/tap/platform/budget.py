"""Per-turn guardrail budget: declared limits and the meter that enforces calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class TurnBudget:
    """Declared guardrail limits for one chat turn."""

    max_tool_rounds: int = 6
    max_seconds: float = 180.0
    max_model_calls: int = 10
    max_cost_micros: int = 0

    def to_dict(self) -> dict[str, int | float]:
        return {
            "maxToolRounds": self.max_tool_rounds,
            "maxSeconds": self.max_seconds,
            "maxModelCalls": self.max_model_calls,
            "maxCostMicros": self.max_cost_micros,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, int | float]) -> TurnBudget:
        return cls(
            max_tool_rounds=int(raw["maxToolRounds"]),
            max_seconds=float(raw["maxSeconds"]),
            max_model_calls=int(raw["maxModelCalls"]),
            max_cost_micros=int(raw["maxCostMicros"]),
        )


class CallBudgetExhausted(Exception):
    """A model call was refused before any provider I/O because its meter is spent."""


class TurnBudgetExhausted(Exception):
    """A turn-level guardrail limit was reached."""

    def __init__(self, reason: Literal["model-calls", "cost", "wall-clock", "tool-rounds"]) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class CallMeter:
    """Tracks model calls and cost spent against one turn's remaining budget."""

    remaining_calls: int
    remaining_cost_micros: int | None
    calls: int = 0
    cost_micros: int = 0

    def before_call(self) -> None:
        if self.calls >= self.remaining_calls or (
            self.remaining_cost_micros is not None
            and self.cost_micros >= self.remaining_cost_micros
        ):
            raise CallBudgetExhausted()

    def after_call(self, cost_micros: int) -> None:
        self.calls += 1
        self.cost_micros += cost_micros


_current_meter: ContextVar[CallMeter | None] = ContextVar("tap_call_meter", default=None)


def current_meter() -> CallMeter | None:
    """Return the `CallMeter` active in the current execution context, if any."""
    return _current_meter.get()


@contextmanager
def metered(meter: CallMeter) -> Iterator[CallMeter]:
    """Make `meter` the active `current_meter()` for the duration of the block."""
    token = _current_meter.set(meter)
    try:
        yield meter
    finally:
        _current_meter.reset(token)
