"""Owned MySQL evidence for event atomicity, replay, and durable rejection."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from scripts.migration_support import IsolatedMysql
from sqlalchemy import insert, select, text

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql import OutboxStore
from tap.platform.db.schema import outbox
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging import mysql_outbox

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _values(identity: str) -> dict:
    return mysql_outbox.compatibility_outbox_values(
        VALIDATION_SCOPE,
        outbox_id=identity,
        command_id=identity,
        aggregate_type="turn",
        aggregate_id="turn-1",
        message_type="turn.process_requested",
        sequence=None,
        created_at=NOW,
    )


def test_writer_joins_caller_transaction_replays_and_conflicts(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def run() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        envelope = ProjectEventEnvelope.from_dict(_values("event-atomic")["envelope"])
        try:
            writer = mysql_outbox.write_project_event
            async with sessions() as session:
                with pytest.raises(ValueError, match="active transaction"):
                    await writer(session, scope=VALIDATION_SCOPE, envelope=envelope)
            with pytest.raises(RuntimeError, match="rollback"):
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO project (enterprise_id, project_id) "
                            "VALUES ('local', 'atomic')"
                        )
                    )
                    await writer(connection, scope=VALIDATION_SCOPE, envelope=envelope)
                    raise RuntimeError("rollback")
            async with engine.connect() as connection:
                assert await connection.scalar(select(outbox.c.outbox_id)) is None
                assert (
                    await connection.scalar(
                        text("SELECT project_id FROM project WHERE project_id='atomic'")
                    )
                    is None
                )
            async with sessions() as session, session.begin():
                first = await writer(session, scope=VALIDATION_SCOPE, envelope=envelope)
                replay = await writer(
                    session,
                    scope=VALIDATION_SCOPE,
                    envelope=replace(
                        envelope,
                        event_id="discarded-id",
                        occurred_at=NOW + timedelta(seconds=2),
                        correlation_id="different-correlation",
                    ),
                )
                assert first == replay == envelope
                with pytest.raises(ValueError, match="idempotency-conflict"):
                    await writer(
                        session,
                        scope=VALIDATION_SCOPE,
                        envelope=replace(
                            envelope,
                            aggregate_id="other",
                            payload={"aggregateId": "other", "sequence": None},
                        ),
                    )
                for field, value in (
                    ("enterprise_id", "other-enterprise"),
                    ("project_id", "other-project"),
                    ("actor_id", "other-actor"),
                    ("identity_mode", "product"),
                ):
                    with pytest.raises(ValueError, match="scope"):
                        await writer(
                            session,
                            scope=VALIDATION_SCOPE,
                            envelope=replace(envelope, **{field: value}),
                        )
            async with engine.connect() as connection:
                rows = (await connection.execute(select(outbox))).mappings().all()
                assert len(rows) == 1
                assert rows[0]["envelope"] == envelope.to_dict()

            async def concurrent_write(identity: str) -> ProjectEventEnvelope:
                async with sessions() as session, session.begin():
                    return await writer(
                        session,
                        scope=VALIDATION_SCOPE,
                        envelope=replace(envelope, event_id=identity, idempotency_key="concurrent"),
                    )

            left, right = await asyncio.gather(concurrent_write("left"), concurrent_write("right"))
            assert left == right
            other_scope = replace(VALIDATION_SCOPE, project_id="other-project")
            other_event = replace(
                envelope, event_id="other-project-event", project_id="other-project"
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO project (enterprise_id, project_id) "
                        "VALUES ('local', 'other-project')"
                    )
                )
                assert await writer(session, scope=other_scope, envelope=other_event) == other_event
                with pytest.raises(ValueError, match="idempotency-conflict"):
                    await writer(
                        session,
                        scope=other_scope,
                        envelope=replace(
                            other_event, event_id=envelope.event_id, idempotency_key="fresh-key"
                        ),
                    )
            async with engine.connect() as connection:
                rows = (await connection.execute(select(outbox))).mappings().all()
            assert len(rows) == 3
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_invalid_persisted_events_are_durably_rejected_without_poisoning_batch(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def run() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            originals = {}
            async with engine.begin() as connection:
                for identity in ("bad-version", "bad-shape", "bad-scope", "bad-timestamp", "valid"):
                    values = _values(identity)
                    if identity == "bad-version":
                        values["envelope"]["schema_version"] = 99
                    if identity == "bad-shape":
                        values["envelope"]["payload"] = {"secret": "must-never-be-in-last-error"}
                    if identity == "bad-scope":
                        values["envelope"]["project_id"] = "contradictory"
                    if identity == "bad-timestamp":
                        values["envelope"]["occurred_at"] = "9999-12-31T23:59:59-23:59"
                    originals[identity] = values
                    await connection.execute(insert(outbox).values(**values, next_attempt_at=NOW))
            store = OutboxStore(sessions, scope=VALIDATION_SCOPE)
            claimed = await store.claim_pending(
                worker_id="relay", now=NOW, lease_duration=timedelta(seconds=30), limit=10
            )
            assert [item.message.outbox_id for item in claimed] == ["valid"]
            async with engine.connect() as connection:
                rows = (await connection.execute(select(outbox))).mappings().all()
            for row in rows:
                assert all(
                    row[field] == value for field, value in originals[row["outbox_id"]].items()
                )
                if row["outbox_id"] == "valid":
                    assert row["status"] == "publishing"
                else:
                    assert row["status"] == "delivery_failed"
                    assert row["last_error"] == "invalid_persisted_event"
                    assert row["claimed_by"] is row["claim_token"] is row["lease_until"] is None
            assert await store.reconcile_expired(NOW + timedelta(days=1), 10) == 1
            claimed_again = await store.claim_pending(
                worker_id="relay",
                now=NOW + timedelta(days=1),
                lease_duration=timedelta(seconds=30),
                limit=10,
            )
            assert [item.message.outbox_id for item in claimed_again] == ["valid"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_domain_events_keep_business_version_separate_from_delivery_sequence(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def run() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        base = ProjectEventEnvelope.from_dict(_values("graph-event")["envelope"])
        graph = replace(
            base,
            event_type="knowledge.graph-snapshot.requested",
            aggregate_type="GraphSnapshot",
            aggregate_id="snapshot-1",
            aggregate_version=3,
            payload={
                "snapshotId": "snapshot-1",
                "sourceRevisionIds": ["opaque-revision"],
                "extractionProfileDigest": "profile-digest",
            },
        )
        status = replace(
            base,
            event_id="status-event",
            idempotency_key="status-event",
            event_type="execution.run.status-changed",
            aggregate_type="ExecutionRun",
            aggregate_id="run-1",
            aggregate_version=2,
            payload={
                "runId": "run-1",
                "from": "QUEUED",
                "to": "RUNNING",
                "sequence": 2,
                "observationId": "observation-1",
            },
        )
        try:
            async with sessions() as session, session.begin():
                await mysql_outbox.write_project_event(
                    session, scope=VALIDATION_SCOPE, envelope=graph
                )
                await mysql_outbox.write_project_event(
                    session, scope=VALIDATION_SCOPE, envelope=status
                )
            async with engine.connect() as connection:
                rows = (await connection.execute(select(outbox))).mappings().all()
            by_id = {row["outbox_id"]: row for row in rows}
            assert by_id["graph-event"]["sequence"] is None
            assert by_id["graph-event"]["envelope"]["aggregate_version"] == 3
            assert by_id["status-event"]["sequence"] == 2
            assert by_id["status-event"]["envelope"]["aggregate_version"] == 2
            claimed = await OutboxStore(sessions, scope=VALIDATION_SCOPE).claim_pending(
                worker_id="domain-relay", now=NOW, lease_duration=timedelta(seconds=30), limit=10
            )
            assert {item.message.outbox_id for item in claimed} == {"graph-event", "status-event"}
        finally:
            await engine.dispose()

    asyncio.run(run())
