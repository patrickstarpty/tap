"""Archive/recovery evidence uses only fixture-owned disposable MySQL."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql import OutboxStore
from tap.modules.chat.application.ports import LeaseLost
from tap.platform.db.schema import outbox
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging.mysql_outbox import compatibility_outbox_values

NOW = datetime(2026, 9, 6)


def _values(identity, scope=VALIDATION_SCOPE, status="published"):
    return {
        **compatibility_outbox_values(
            scope,
            outbox_id=identity,
            command_id=identity,
            aggregate_type="turn",
            aggregate_id="turn-1",
            message_type="turn.process_requested",
            sequence=None,
            created_at=NOW,
        ),
        "status": status,
        "attempt_count": 2,
        "next_attempt_at": NOW,
        "published_at": NOW if status == "published" else None,
    }


def test_archive_copies_original_evidence_in_bounded_scoped_transaction(owned_project_mysql):
    async def run():
        from tap.platform.db.schema import outbox_archive
        from tap.platform.messaging.outbox_archive import OutboxArchive

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            other = replace(VALIDATION_SCOPE, project_id="other")
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO project (enterprise_id,project_id) VALUES ('local','other')")
                )
                for value in [
                    _values("a"),
                    _values("b"),
                    _values("foreign", other),
                    _values("pending", status="pending"),
                ]:
                    await conn.execute(insert(outbox).values(**value))
                original = (
                    (await conn.execute(select(outbox).where(outbox.c.outbox_id == "a")))
                    .mappings()
                    .one()
                )
            recovery = OutboxArchive(sessions, scope=VALIDATION_SCOPE)
            assert await recovery.archive_published(NOW + timedelta(seconds=1), 1) == 1
            async with engine.connect() as conn:
                archived = (await conn.execute(select(outbox_archive))).mappings().one()
                assert {key: archived[key] for key in original} == dict(original)
                assert set((await conn.execute(select(outbox.c.outbox_id))).scalars()) == {
                    "b",
                    "foreign",
                    "pending",
                }
            assert await recovery.archive_published(NOW + timedelta(seconds=1), 1) == 1
            assert await recovery.archive_published(NOW + timedelta(seconds=1), 1) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_archive_copy_failure_rolls_back_delete(owned_project_mysql):
    async def run():
        from tap.platform.db.schema import outbox_archive
        from tap.platform.messaging.outbox_archive import OutboxArchive

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            values = _values("collision")
            async with engine.begin() as conn:
                await conn.execute(insert(outbox).values(**values))
                await conn.execute(insert(outbox_archive).values(**values, archived_at=NOW))
            with pytest.raises(IntegrityError):
                await OutboxArchive(sessions, scope=VALIDATION_SCOPE).archive_published(
                    NOW + timedelta(seconds=1), 10
                )
            async with engine.connect() as conn:
                assert await conn.scalar(select(outbox.c.outbox_id)) == "collision"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_dead_letter_redrive_preserves_known_identity_and_rejects_unknown_major(
    owned_project_mysql,
):
    async def run():
        from tap.platform.db.schema import outbox_dead_letter
        from tap.platform.messaging.outbox_archive import OutboxArchive

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            known = _values("known", status="pending")
            unknown = _values("unknown", status="pending")
            unknown["envelope"]["schema_version"] = 99
            async with engine.begin() as conn:
                await conn.execute(insert(outbox).values(**known))
                await conn.execute(insert(outbox).values(**unknown))
            store = OutboxStore(sessions, scope=VALIDATION_SCOPE)
            claimed = await store.claim_pending(
                worker_id="w", now=NOW, lease_duration=timedelta(seconds=1), limit=10
            )
            assert [item.message.outbox_id for item in claimed] == ["known"]
            with pytest.raises(LeaseLost):
                await store.mark_terminal(outbox_id="known", claim_token="stale", error="secret")
            await store.mark_terminal(
                outbox_id="known",
                claim_token=claimed[0].claim_token,
                error="private provider credentials",
            )
            recovery = OutboxArchive(sessions, scope=VALIDATION_SCOPE)
            assert await recovery.redrive_dead_letters(10) == 1
            assert await recovery.redrive_dead_letters(10) == 0
            async with engine.connect() as conn:
                rows = {r["outbox_id"]: r for r in (await conn.execute(select(outbox))).mappings()}
                dead = {
                    r["outbox_id"]: r
                    for r in (await conn.execute(select(outbox_dead_letter))).mappings()
                }
                assert rows["known"]["status"] == "pending"
                assert rows["known"]["attempt_count"] == 0
                assert rows["unknown"]["status"] == "delivery_failed"
                for key in (
                    "envelope",
                    "event_content_digest",
                    "command_id",
                    "actor_id",
                    "identity_origin",
                ):
                    assert rows["known"][key] == dead["known"][key] == known[key]
                assert dead["unknown"]["envelope"] == unknown["envelope"]
                assert dead["unknown"]["redriven_at"] is None
                assert dead["known"]["reason"] == "dispatch_unavailable"
                assert "credentials" not in dead["known"]["last_error"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_dead_letter_legacy_capture_and_cross_project_fence(owned_project_mysql):
    async def run():
        from tap.platform.db.schema import outbox_dead_letter
        from tap.platform.messaging.outbox_archive import OutboxArchive

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            other = replace(VALIDATION_SCOPE, project_id="other")
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO project (enterprise_id,project_id) VALUES ('local','other')")
                )
                await conn.execute(
                    insert(outbox).values(
                        **_values("legacy", status="delivery_failed"),
                        last_error="historical secret",
                    )
                )
                await conn.execute(
                    insert(outbox).values(**_values("foreign", other, "delivery_failed"))
                )
            recovery = OutboxArchive(sessions, scope=VALIDATION_SCOPE)
            assert await recovery.redrive_dead_letters(1) == 1
            async with engine.connect() as conn:
                assert list(
                    (await conn.execute(select(outbox_dead_letter.c.outbox_id))).scalars()
                ) == ["legacy"]
                assert (
                    await conn.scalar(
                        select(outbox.c.status).where(outbox.c.outbox_id == "foreign")
                    )
                    == "delivery_failed"
                )
            for invalid in (0, -1, True, 501):
                with pytest.raises(ValueError):
                    await recovery.redrive_dead_letters(invalid)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_dead_letter_write_failure_rolls_back_terminal_settlement(owned_project_mysql):
    from sqlalchemy import event

    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as conn:
                await conn.execute(insert(outbox).values(**_values("rollback", status="pending")))
            store = OutboxStore(sessions, scope=VALIDATION_SCOPE)
            claim = (
                await store.claim_pending(
                    worker_id="w", now=NOW, lease_duration=timedelta(seconds=30), limit=1
                )
            )[0]

            def fail_evidence(conn, cursor, statement, parameters, context, executemany):
                if statement.startswith("INSERT INTO outbox_dead_letter"):
                    raise RuntimeError("injected evidence failure")

            event.listen(engine.sync_engine, "before_cursor_execute", fail_evidence)
            try:
                with pytest.raises(RuntimeError, match="injected evidence failure"):
                    await store.mark_terminal(
                        outbox_id="rollback", claim_token=claim.claim_token, error="failure"
                    )
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", fail_evidence)
            async with engine.connect() as conn:
                row = (await conn.execute(select(outbox))).mappings().one()
                assert row["status"] == "publishing" and row["claim_token"] == claim.claim_token
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_archive_concurrent_batches_never_copy_or_delete_twice(owned_project_mysql):
    async def run():
        from tap.platform.db.schema import outbox_archive
        from tap.platform.messaging.outbox_archive import OutboxArchive

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as conn:
                await conn.execute(insert(outbox).values(**_values("first")))
                await conn.execute(insert(outbox).values(**_values("second")))
            recovery = OutboxArchive(sessions, scope=VALIDATION_SCOPE)
            counts = await asyncio.gather(
                recovery.archive_published(NOW + timedelta(seconds=1), 1),
                recovery.archive_published(NOW + timedelta(seconds=1), 1),
            )
            # InnoDB may lock additional scanned rows while sorting. SKIP LOCKED
            # permits an empty concurrent batch; later passes must drain it once.
            assert all(count in (0, 1) for count in counts) and sum(counts) >= 1
            remaining = await recovery.archive_published(NOW + timedelta(seconds=1), 2)
            assert sum(counts) + remaining == 2
            async with engine.connect() as conn:
                assert set((await conn.execute(select(outbox_archive.c.outbox_id))).scalars()) == {
                    "first",
                    "second",
                }
                assert await conn.scalar(select(outbox.c.outbox_id)) is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_archive_writer_replays_original_fact_and_rejects_changed_content(owned_project_mysql):
    from tap.contracts.events import ProjectEventEnvelope
    from tap.platform.db.schema import outbox_archive
    from tap.platform.messaging.mysql_outbox import OutboxIdempotencyConflict, write_project_event
    from tap.platform.messaging.outbox_archive import OutboxArchive

    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        original = ProjectEventEnvelope.from_dict(_values("original")["envelope"])
        try:
            async with engine.begin() as conn:
                await conn.execute(insert(outbox).values(**_values("original")))
            assert (
                await OutboxArchive(sessions, scope=VALIDATION_SCOPE).archive_published(
                    NOW + timedelta(seconds=1), 1
                )
                == 1
            )
            retry = replace(
                original,
                event_id="new-id",
                correlation_id="new-correlation",
                occurred_at=original.occurred_at + timedelta(seconds=2),
            )
            async with sessions() as session, session.begin():
                replay = await write_project_event(session, scope=VALIDATION_SCOPE, envelope=retry)
                assert replay == original
                with pytest.raises(OutboxIdempotencyConflict):
                    await write_project_event(
                        session,
                        scope=VALIDATION_SCOPE,
                        envelope=replace(
                            retry,
                            event_id="changed-id",
                            aggregate_id="changed",
                            payload={"aggregateId": "changed", "sequence": None},
                        ),
                    )
                with pytest.raises(OutboxIdempotencyConflict):
                    await write_project_event(
                        session,
                        scope=VALIDATION_SCOPE,
                        envelope=replace(original, idempotency_key="different-key"),
                    )
                await session.execute(
                    insert(outbox).values(**_values("legitimate", status="pending"))
                )
                with pytest.raises(OutboxIdempotencyConflict):
                    await write_project_event(
                        session,
                        scope=VALIDATION_SCOPE,
                        envelope=replace(retry, event_id="legitimate"),
                    )
            async with engine.connect() as conn:
                live = (await conn.execute(select(outbox))).mappings().one()
                assert live["outbox_id"] == "legitimate"
                assert live["envelope"] == _values("legitimate")["envelope"]
                rows = (await conn.execute(select(outbox_archive))).mappings().all()
                assert len(rows) == 1 and rows[0]["envelope"] == original.to_dict()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_archive_chat_command_replay_keeps_business_message_and_rejects_key_reuse(
    owned_project_mysql,
):
    from sqlalchemy import update

    from tap.modules.chat.adapters.mysql import MysqlTurnRepository, chat_turn
    from tap.modules.chat.application.ports import CreateTurnCommand
    from tap.modules.chat.domain.models import ChatId, CommandId, TurnId
    from tap.platform.messaging.outbox_archive import OutboxArchive

    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlTurnRepository(sessions, scope=VALIDATION_SCOPE)
        command = CreateTurnCommand(
            command_id=CommandId("original-command"),
            turn_id=TurnId("original-turn"),
            chat_id=ChatId("original-chat"),
            client_request_id="original-request",
            message="Original business request",
            occurred_at=NOW,
        )
        try:
            original = await repository.create_with_outbox(command)
            async with engine.begin() as conn:
                await conn.execute(update(outbox).values(status="published", published_at=NOW))
            assert (
                await OutboxArchive(sessions, scope=VALIDATION_SCOPE).archive_published(
                    NOW + timedelta(seconds=1), 1
                )
                == 1
            )
            assert (
                await repository.create_with_outbox(replace(command, turn_id=TurnId("retry-turn")))
                == original
            )
            with pytest.raises(ValueError, match="idempotency-conflict"):
                await repository.create_with_outbox(
                    replace(command, message="Changed business request")
                )
            with pytest.raises(ValueError, match="idempotency-conflict"):
                await repository.create_with_outbox(
                    replace(
                        command,
                        turn_id=TurnId("rebound-turn"),
                        chat_id=ChatId("new-chat"),
                        client_request_id="new-request",
                    )
                )
            async with engine.connect() as conn:
                assert list((await conn.execute(select(chat_turn.c.turn_id))).scalars()) == [
                    "original-turn"
                ]
                assert await conn.scalar(select(outbox.c.outbox_id)) is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_archive_and_writer_concurrently_preserve_one_original_fact(owned_project_mysql):
    from tap.contracts.events import ProjectEventEnvelope
    from tap.platform.db.schema import outbox_archive
    from tap.platform.messaging.mysql_outbox import write_project_event
    from tap.platform.messaging.outbox_archive import OutboxArchive

    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        original = ProjectEventEnvelope.from_dict(_values("racing-original")["envelope"])
        try:
            async with engine.begin() as conn:
                await conn.execute(insert(outbox).values(**_values("racing-original")))

            async def write_retry():
                async with sessions() as session, session.begin():
                    # Establish an older snapshot before the potential archive move.
                    await session.execute(select(outbox.c.outbox_id))
                    return await write_project_event(
                        session,
                        scope=VALIDATION_SCOPE,
                        envelope=replace(original, event_id="racing-retry", correlation_id="new"),
                    )

            archive = OutboxArchive(sessions, scope=VALIDATION_SCOPE)
            _, replay = await asyncio.gather(
                archive.archive_published(NOW + timedelta(seconds=1), 1), write_retry()
            )
            assert replay == original
            await archive.archive_published(NOW + timedelta(seconds=1), 1)
            async with engine.connect() as conn:
                assert await conn.scalar(select(outbox.c.outbox_id)) is None
                rows = (await conn.execute(select(outbox_archive))).mappings().all()
                assert len(rows) == 1 and rows[0]["envelope"] == original.to_dict()
        finally:
            await engine.dispose()

    asyncio.run(run())
