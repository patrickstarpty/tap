"""MySQL prompt suggestion refresh queue and suggestion cache, with real lease
fencing and transactional semantics. Each test gets its own isolated database
via the function-scoped owned_project_mysql fixture."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_suggestions import MysqlSuggestionStore
from tap.modules.chat.application.suggestion_ports import ClaimedRefresh
from tap.modules.chat.domain.suggestions import (
    PromptSuggestion,
    RefreshReason,
    SuggestionKey,
    SuggestionSource,
)
from tests.owned_mysql import owned_project_database_url

T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
KEY = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="en")


def _suggestion(suggestion_id: str, question: str) -> PromptSuggestion:
    return PromptSuggestion(
        suggestion_id=suggestion_id,
        question=question,
        sources=(SuggestionSource(source_id="source-1", version="rev-1:1"),),
    )


async def _insert_actor(engine, actor_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO actor_principal (enterprise_id, actor_id, principal_type) "
                "VALUES (:enterprise_id, :actor_id, 'VALIDATION')"
            ),
            {"enterprise_id": VALIDATION_SCOPE.enterprise_id, "actor_id": actor_id},
        )


def _run(owned_project_mysql, scenario):
    url = owned_project_database_url(owned_project_mysql)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = MysqlSuggestionStore(sessions, scope=VALIDATION_SCOPE)

    async def wrapped():
        try:
            await scenario(store, engine, sessions)
        finally:
            await engine.dispose()

    asyncio.run(wrapped())


def test_load_is_none_before_first_refresh(owned_project_mysql):
    async def scenario(store, engine, sessions):
        assert await store.load(KEY) is None

    _run(owned_project_mysql, scenario)


def test_complete_replaces_all_suggestions_for_key(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        (claim,) = await store.claim_due(
            worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
        )
        assert await store.complete_refresh(claim, (_suggestion("s1", "first?"),), now=T0)

        await store.request_refresh(
            KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0 + timedelta(minutes=1)
        )
        (claim2,) = await store.claim_due(
            worker_id="w1",
            now=T0 + timedelta(minutes=1),
            limit=10,
            lease_duration=timedelta(minutes=5),
        )
        await store.complete_refresh(
            claim2,
            (_suggestion("s3", "third?"), _suggestion("s2", "second?")),
            now=T0 + timedelta(minutes=1),
        )
        loaded = await store.load(KEY)
        assert [item.suggestion_id for item in loaded] == ["s3", "s2"]
        assert loaded[0].sources == (SuggestionSource("source-1", "rev-1:1"),)

    _run(owned_project_mysql, scenario)


def test_suggestions_are_isolated_by_actor_and_locale(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await _insert_actor(engine, "actor-2")
        other = SuggestionKey(actor_id="actor-2", locale="zh")

        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        await store.request_refresh(other, RefreshReason.MISSING, now=T0)
        claims = await store.claim_due(
            worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
        )
        by_key = {claim.key: claim for claim in claims}
        await store.complete_refresh(by_key[KEY], (_suggestion("s1", "en question?"),), now=T0)
        await store.complete_refresh(by_key[other], (_suggestion("s2", "zh question?"),), now=T0)

        assert [item.suggestion_id for item in await store.load(KEY)] == ["s1"]
        assert [item.suggestion_id for item in await store.load(other)] == ["s2"]

    _run(owned_project_mysql, scenario)


def test_claim_only_returns_due_rows(owned_project_mysql):
    async def scenario(store, engine, sessions):
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

    _run(owned_project_mysql, scenario)


def test_request_during_running_refresh_keeps_new_due_at(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        (claim,) = await store.claim_due(
            worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
        )

        await store.request_refresh(
            KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0 + timedelta(seconds=30)
        )
        await store.complete_refresh(
            claim, (_suggestion("s1", "q?"),), now=T0 + timedelta(minutes=1)
        )

        reclaimed = await store.claim_due(
            worker_id="w1",
            now=T0 + timedelta(minutes=2),
            limit=10,
            lease_duration=timedelta(minutes=5),
        )
        assert len(reclaimed) == 1
        assert reclaimed[0].key == KEY

    _run(owned_project_mysql, scenario)


def test_stale_lease_cannot_complete(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        (claim,) = await store.claim_due(
            worker_id="w1", now=T0, limit=10, lease_duration=timedelta(seconds=1)
        )

        stolen = await store.claim_due(
            worker_id="w2",
            now=T0 + timedelta(seconds=2),
            limit=10,
            lease_duration=timedelta(minutes=5),
        )
        assert len(stolen) == 1

        ok = await store.complete_refresh(
            claim, (_suggestion("s1", "q?"),), now=T0 + timedelta(seconds=3)
        )
        assert ok is False
        assert await store.load(KEY) is None

    _run(owned_project_mysql, scenario)


def test_failed_refresh_keeps_rows_and_backs_off(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        (claim,) = await store.claim_due(
            worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
        )
        await store.complete_refresh(claim, (_suggestion("s1", "q?"),), now=T0)

        await store.request_refresh(
            KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0 + timedelta(minutes=1)
        )
        (claim2,) = await store.claim_due(
            worker_id="w1",
            now=T0 + timedelta(minutes=1),
            limit=10,
            lease_duration=timedelta(minutes=5),
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

    _run(owned_project_mysql, scenario)


def test_daily_refresh_only_marks_old_rows(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        (claim,) = await store.claim_due(
            worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
        )
        await store.complete_refresh(claim, (_suggestion("s1", "q?"),), now=T0)

        not_yet = await store.request_daily_refresh(now=T0 + timedelta(hours=23))
        assert not_yet == 0

        marked = await store.request_daily_refresh(now=T0 + timedelta(hours=25))
        assert marked == 1
        due_now = await store.claim_due(
            worker_id="w1",
            now=T0 + timedelta(hours=25),
            limit=10,
            lease_duration=timedelta(minutes=5),
        )
        assert len(due_now) == 1

    _run(owned_project_mysql, scenario)


def test_project_request_only_touches_existing_rows(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        async with sessions() as write_session, write_session.begin():
            touched = await store.request_refresh_for_project_in_transaction(
                write_session, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0 + timedelta(hours=1)
            )
        assert touched == 1
        never_requested = SuggestionKey(actor_id="never-seen-actor", locale="en")
        assert await store.load(never_requested) is None

    _run(owned_project_mysql, scenario)


def test_request_refresh_for_actor_only_updates_existing_rows(owned_project_mysql):
    async def scenario(store, engine, sessions):
        await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
        touched = await store.request_refresh_for_actor(
            VALIDATION_SCOPE.actor_id,
            RefreshReason.TURN_COMPLETED,
            now=T0 + timedelta(minutes=1),
        )
        assert touched == 1
        untouched = await store.request_refresh_for_actor(
            "actor-never-requested", RefreshReason.TURN_COMPLETED, now=T0
        )
        assert untouched == 0

    _run(owned_project_mysql, scenario)


def test_request_refresh_is_race_safe_for_a_new_key(owned_project_mysql):
    """Two concurrent first-ever request_refresh calls for the same brand-new
    key (e.g. a read's MISSING trigger racing a turn-completion trigger) must
    not raise, and must not leave the row missing or duplicated: exactly one
    unique-key insert wins, the loser retries as an update."""

    async def scenario(store, engine, sessions):
        await asyncio.gather(
            store.request_refresh(KEY, RefreshReason.MISSING, now=T0),
            store.request_refresh(KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=T0),
        )
        (claim,) = await store.claim_due(
            worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
        )
        assert claim.key == KEY

    _run(owned_project_mysql, scenario)


def test_claimed_refresh_is_frozen():
    claim = ClaimedRefresh(key=KEY, lease_token="token", attempt_count=0)
    with pytest.raises(Exception):
        claim.attempt_count = 1  # type: ignore[misc]
