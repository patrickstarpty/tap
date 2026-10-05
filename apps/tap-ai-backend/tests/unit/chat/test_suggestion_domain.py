"""Refresh due-time rule for prompt suggestions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tap.modules.chat.domain.suggestions import (
    REFRESH_THROTTLE,
    RefreshReason,
    is_displayable_question,
    next_due_at,
)

T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_turn_completion_within_throttle_is_deferred():
    assert next_due_at(
        RefreshReason.TURN_COMPLETED,
        now=T0,
        last_refreshed_at=T0 - timedelta(minutes=3),
        current_due_at=None,
    ) == T0 + timedelta(minutes=7)


def test_published_knowledge_is_due_now():
    assert (
        next_due_at(
            RefreshReason.KNOWLEDGE_PUBLISHED,
            now=T0,
            last_refreshed_at=T0 - timedelta(minutes=1),
            current_due_at=None,
        )
        == T0
    )


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
    assert (
        next_due_at(
            RefreshReason.FILTERED,
            now=T0 + timedelta(minutes=2),
            last_refreshed_at=last,
            current_due_at=first,
        )
        == last + REFRESH_THROTTLE
    )


@pytest.mark.parametrize(
    ("question", "locale", "expected"),
    [
        ("友邦悦享年年年金保险的身故保险金如何计算？", "zh", True),
        ("《友邦悦享年年年金保险》的 IRR 如何计算？", "zh", True),
        ("How is the death benefit calculated?", "zh", False),
        ("How is the death benefit of 友邦悦享年年 calculated?", "en", True),
        ("友邦悦享年年年金保险的身故保险金如何计算？", "en", False),
        ("Which rules apply in src_4e777fe250b9426c8bab815ab8c4ed81?", "en", False),
        ("Which rules apply in rev_d0c82e1614d234e0d78f4fec6b5331?", "en", False),
        ("What does document d0c82e1614d234e0d78f4fec cover?", "en", False),
    ],
)
def test_displayable_question_matches_locale_and_hides_identifiers(question, locale, expected):
    assert is_displayable_question(question, locale) is expected
