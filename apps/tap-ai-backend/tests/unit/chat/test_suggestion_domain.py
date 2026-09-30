"""Refresh due-time rule for prompt suggestions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tap.modules.chat.domain.suggestions import REFRESH_THROTTLE, RefreshReason, next_due_at

T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_turn_completion_within_throttle_is_deferred():
    assert next_due_at(
        RefreshReason.TURN_COMPLETED,
        now=T0,
        last_refreshed_at=T0 - timedelta(minutes=3),
        current_due_at=None,
    ) == T0 + timedelta(minutes=7)


def test_published_knowledge_is_due_now():
    assert next_due_at(
        RefreshReason.KNOWLEDGE_PUBLISHED,
        now=T0,
        last_refreshed_at=T0 - timedelta(minutes=1),
        current_due_at=None,
    ) == T0


def test_earlier_pending_due_at_wins():
    assert next_due_at(
        RefreshReason.FILTERED,
        now=T0,
        last_refreshed_at=None,
        current_due_at=T0 - timedelta(seconds=5),
    ) == T0 - timedelta(seconds=5)


def test_throttled_reason_never_moves_due_earlier_than_throttle():
    last = T0 - timedelta(minutes=1)
    first = next_due_at(RefreshReason.FILTERED, now=T0, last_refreshed_at=last, current_due_at=None)
    assert next_due_at(
        RefreshReason.FILTERED,
        now=T0 + timedelta(minutes=2),
        last_refreshed_at=last,
        current_due_at=first,
    ) == last + REFRESH_THROTTLE
