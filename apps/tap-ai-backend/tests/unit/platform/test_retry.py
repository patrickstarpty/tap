"""Bounded exponential backoff with full jitter for retryable job failures."""

import random
from datetime import timedelta

import pytest

from tap.platform.retry import RetryPolicy


def test_defaults_match_reliable_answering_contract() -> None:
    policy = RetryPolicy()

    assert policy.max_attempts == 5
    assert policy.base == timedelta(seconds=2)
    assert policy.cap == timedelta(seconds=300)


def test_ceiling_doubles_from_base_and_stops_at_cap() -> None:
    policy = RetryPolicy()

    ceilings = [policy.ceiling(attempt).total_seconds() for attempt in range(1, 11)]

    assert ceilings == [2, 4, 8, 16, 32, 64, 128, 256, 300, 300]


def test_next_delay_is_full_jitter_within_ceiling() -> None:
    policy = RetryPolicy()
    rng = random.Random(7)

    for attempt in range(1, 12):
        delay = policy.next_delay(attempt, rng=rng)
        assert timedelta(0) <= delay <= policy.ceiling(attempt)


def test_next_delay_is_deterministic_for_a_fixed_seed() -> None:
    policy = RetryPolicy()

    first = [policy.next_delay(n, rng=random.Random(42)) for n in range(1, 6)]
    second = [policy.next_delay(n, rng=random.Random(42)) for n in range(1, 6)]

    assert first == second


def test_is_exhausted_at_max_attempts() -> None:
    policy = RetryPolicy()

    assert not policy.is_exhausted(4)
    assert policy.is_exhausted(5)
    assert policy.is_exhausted(6)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base": timedelta(0)},
        {"base": timedelta(seconds=-1)},
        {"base": timedelta(seconds=10), "cap": timedelta(seconds=5)},
    ],
)
def test_invalid_parameters_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


def test_attempt_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RetryPolicy().ceiling(0)
