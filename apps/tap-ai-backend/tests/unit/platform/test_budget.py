from __future__ import annotations

import pytest

from tap.platform.budget import (
    CallBudgetExhausted,
    CallMeter,
    TurnBudget,
    current_meter,
    metered,
)


def test_meter_blocks_call_at_limit() -> None:
    meter = CallMeter(remaining_calls=2, remaining_cost_micros=None)

    meter.before_call()
    meter.after_call(0)
    meter.before_call()
    meter.after_call(0)

    with pytest.raises(CallBudgetExhausted):
        meter.before_call()


def test_meter_blocks_call_once_cost_reached() -> None:
    meter = CallMeter(remaining_calls=100, remaining_cost_micros=1000)
    meter.before_call()
    meter.after_call(1000)

    with pytest.raises(CallBudgetExhausted):
        meter.before_call()

    unlimited = CallMeter(remaining_calls=100, remaining_cost_micros=None)
    unlimited.before_call()
    unlimited.after_call(10_000_000)
    unlimited.before_call()


def test_budget_dict_round_trip() -> None:
    default = TurnBudget()

    assert default.to_dict() == {
        "maxToolRounds": 6,
        "maxSeconds": 180.0,
        "maxModelCalls": 10,
        "maxCostMicros": 0,
    }
    assert TurnBudget.from_dict(default.to_dict()) == default


def test_metered_is_scoped() -> None:
    meter = CallMeter(remaining_calls=1, remaining_cost_micros=None)

    assert current_meter() is None
    with metered(meter):
        assert current_meter() is meter
    assert current_meter() is None
