"""In-memory SuggestionStore double covers the same claim/complete/fail semantics
as the MySQL store, at unit-test speed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tap.modules.chat.adapters.memory_suggestions import InMemorySuggestionStore
from tap.modules.chat.application.suggestion_ports import ClaimedRefresh
from tap.modules.chat.domain.suggestions import (
    PromptSuggestion,
    RefreshReason,
    SuggestionKey,
    SuggestionSource,
)

T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
KEY = SuggestionKey(actor_id="actor-1", locale="en")


def _suggestion(suggestion_id: str, question: str) -> PromptSuggestion:
    return PromptSuggestion(
        suggestion_id=suggestion_id,
        question=question,
        sources=(SuggestionSource(source_id="source-1", version="rev-1:1"),),
    )


@pytest.fixture
def store() -> InMemorySuggestionStore:
    return InMemorySuggestionStore()


@pytest.mark.asyncio
async def test_load_is_none_before_first_refresh(store):
    assert await store.load(KEY) is None


@pytest.mark.asyncio
async def test_complete_replaces_all_suggestions_for_key(store):
    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    (claim,) = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )
    await store.complete_refresh(claim, (_suggestion("s1", "first?"),), now=T0)

    await store.request_refresh(
        KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0 + timedelta(minutes=1)
    )
    (claim2,) = await store.claim_due(
        worker_id="w1", now=T0 + timedelta(minutes=1), limit=10, lease_duration=timedelta(minutes=5)
    )
    await store.complete_refresh(
        claim2,
        (_suggestion("s3", "third?"), _suggestion("s2", "second?")),
        now=T0 + timedelta(minutes=1),
    )

    loaded = await store.load(KEY)
    assert [item.suggestion_id for item in loaded] == ["s3", "s2"]


@pytest.mark.asyncio
async def test_suggestions_are_isolated_by_actor_and_locale(store):
    other = SuggestionKey(actor_id="actor-2", locale="zh")
    for key in (KEY, other):
        await store.request_refresh(key, RefreshReason.MISSING, now=T0)
    claims = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )
    by_key = {claim.key: claim for claim in claims}
    await store.complete_refresh(by_key[KEY], (_suggestion("s1", "en question?"),), now=T0)
    await store.complete_refresh(by_key[other], (_suggestion("s2", "zh question?"),), now=T0)

    assert [item.suggestion_id for item in await store.load(KEY)] == ["s1"]
    assert [item.suggestion_id for item in await store.load(other)] == ["s2"]


@pytest.mark.asyncio
async def test_claim_only_returns_due_rows(store):
    await store.request_refresh(KEY, RefreshReason.FILTERED, now=T0)
    (claim,) = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )
    await store.complete_refresh(claim, (), now=T0)

    await store.request_refresh(KEY, RefreshReason.FILTERED, now=T0 + timedelta(minutes=1))
    assert (
        await store.claim_due(
            worker_id="w1",
            now=T0 + timedelta(minutes=1),
            limit=10,
            lease_duration=timedelta(minutes=5),
        )
    ) == ()
    claimed = await store.claim_due(
        worker_id="w1",
        now=T0 + timedelta(minutes=11),
        limit=10,
        lease_duration=timedelta(minutes=5),
    )
    assert len(claimed) == 1


@pytest.mark.asyncio
async def test_request_during_running_refresh_keeps_new_due_at(store):
    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    (claim,) = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )

    await store.request_refresh(
        KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0 + timedelta(seconds=30)
    )
    await store.complete_refresh(claim, (_suggestion("s1", "q?"),), now=T0 + timedelta(minutes=1))

    reclaimed = await store.claim_due(
        worker_id="w1", now=T0 + timedelta(minutes=2), limit=10, lease_duration=timedelta(minutes=5)
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].key == KEY


@pytest.mark.asyncio
async def test_stale_lease_cannot_complete(store):
    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    (claim,) = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(seconds=1)
    )

    stolen = await store.claim_due(
        worker_id="w2", now=T0 + timedelta(seconds=2), limit=10, lease_duration=timedelta(minutes=5)
    )
    assert len(stolen) == 1

    ok = await store.complete_refresh(
        claim, (_suggestion("s1", "q?"),), now=T0 + timedelta(seconds=3)
    )
    assert ok is False
    assert await store.load(KEY) is None


@pytest.mark.asyncio
async def test_failed_refresh_keeps_rows_and_backs_off(store):
    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    (claim,) = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )
    await store.complete_refresh(claim, (_suggestion("s1", "q?"),), now=T0)

    await store.request_refresh(
        KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0 + timedelta(minutes=1)
    )
    (claim2,) = await store.claim_due(
        worker_id="w1", now=T0 + timedelta(minutes=1), limit=10, lease_duration=timedelta(minutes=5)
    )
    ok = await store.fail_refresh(claim2, "model-timeout", now=T0 + timedelta(minutes=1))
    assert ok is True

    loaded = await store.load(KEY)
    assert [item.suggestion_id for item in loaded] == ["s1"]
    assert (
        await store.claim_due(
            worker_id="w1",
            now=T0 + timedelta(minutes=1, seconds=1),
            limit=10,
            lease_duration=timedelta(minutes=5),
        )
    ) == ()
    backed_off = await store.claim_due(
        worker_id="w1",
        now=T0 + timedelta(minutes=11),
        limit=10,
        lease_duration=timedelta(minutes=5),
    )
    assert len(backed_off) == 1
    assert backed_off[0].attempt_count == 1


@pytest.mark.asyncio
async def test_daily_refresh_only_marks_old_rows(store):
    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    (claim,) = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )
    await store.complete_refresh(claim, (_suggestion("s1", "q?"),), now=T0)

    later = T0 + timedelta(hours=23)
    assert await store.request_daily_refresh(now=later) == 0

    much_later = T0 + timedelta(hours=25)
    assert await store.request_daily_refresh(now=much_later) == 1
    assert (
        await store.claim_due(
            worker_id="w1", now=much_later, limit=10, lease_duration=timedelta(minutes=5)
        )
    ) != ()


@pytest.mark.asyncio
async def test_project_request_only_touches_existing_rows(store):
    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    touched = await store.request_refresh_for_project_in_transaction(
        session=None, reason=RefreshReason.KNOWLEDGE_PUBLISHED, now=T0
    )
    assert touched == 1
    never_requested = SuggestionKey(actor_id="actor-9", locale="en")
    assert await store.load(never_requested) is None


@pytest.mark.asyncio
async def test_request_refresh_for_actor_only_updates_existing_rows(store):
    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    touched = await store.request_refresh_for_actor(
        "actor-1", RefreshReason.TURN_COMPLETED, now=T0 + timedelta(minutes=1)
    )
    assert touched == 1
    untouched = await store.request_refresh_for_actor(
        "actor-does-not-exist", RefreshReason.TURN_COMPLETED, now=T0
    )
    assert untouched == 0


def test_claimed_refresh_is_frozen():
    claim = ClaimedRefresh(key=KEY, lease_token="token", attempt_count=0)
    with pytest.raises(Exception):
        claim.attempt_count = 1  # type: ignore[misc]
