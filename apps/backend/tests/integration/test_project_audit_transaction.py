"""Real, owned MySQL proof for business + Audit + Outbox atomicity."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from scripts.migration_support import IsolatedMysql
from sqlalchemy import insert, select, text

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode
from tap.modules.chat.adapters.mysql import chat_turn
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import outbox
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging.mysql_outbox import write_project_event


def test_audit_three_writes_commit_rollback_and_replay(owned_project_mysql: IsolatedMysql) -> None:
    from tap.entrypoints.tapper_runtime import create_project_audit
    from tap.modules.governance.adapters.schema import project_audit
    from tap.modules.governance.domain.audit import (
        AuditAction,
        AuditOutcome,
        AuditResource,
        SafeAuditMetadata,
    )

    async def append(
        connection, *, key="command-1", scope=VALIDATION_SCOPE, correlation="request-1", **overrides
    ):
        adapter = create_project_audit(connection, scope=scope)
        args = dict(
            action=AuditAction.RECOVER_UPLOADS,
            resource=AuditResource.PROJECT_MAINTENANCE,
            outcome=AuditOutcome.COMPLETED,
            safe_metadata=SafeAuditMetadata({"processed_count": 1}),
        )
        return await adapter.append(
            scope, **{**args, **overrides}, correlation_id=correlation, idempotency_key=key
        )

    async def run():
        engine, _ = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        now = datetime.now(timezone.utc)
        envelope = ProjectEventEnvelope(
            event_id="audit-transaction-event",
            event_type="turn.process_requested",
            schema_version=1,
            occurred_at=now,
            scope_kind="PROJECT",
            enterprise_id="local",
            project_id="tapper-demo",
            actor_id="tapper-local-user",
            identity_mode="validation",
            aggregate_type="turn",
            aggregate_id="audit-transaction-turn",
            aggregate_version=0,
            correlation_id="request-1",
            causation_id=None,
            idempotency_key="command-1",
            payload={"aggregateId": "audit-transaction-turn", "sequence": None},
        )

        async def three_writes(connection):
            await connection.execute(
                insert(chat_turn).values(
                    **scope_values(VALIDATION_SCOPE),
                    turn_id="audit-transaction-turn",
                    chat_id="chat-1",
                    client_request_id="command-1",
                    message="owned fixture",
                    state="QUEUED",
                    created_at=now.replace(tzinfo=None),
                )
            )
            fact = await append(connection)
            await write_project_event(connection, scope=VALIDATION_SCOPE, envelope=envelope)
            return fact

        try:
            async with engine.connect() as connection:
                with pytest.raises(ValueError, match="active transaction"):
                    await append(connection)
            with pytest.raises(RuntimeError, match="rollback"):
                async with engine.begin() as connection:
                    await three_writes(connection)
                    raise RuntimeError("rollback")
            async with engine.connect() as connection:
                for table in (chat_turn, project_audit, outbox):
                    assert (await connection.execute(select(table))).all() == []
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO actor_principal (enterprise_id, actor_id, principal_type) "
                        "VALUES ('local', 'other-actor', 'VALIDATION')"
                    )
                )
                first = await three_writes(connection)
                replay = await append(connection, correlation="retry-request")
                assert replay == first
                assert first.correlation_id == "request-1"
                assert first.occurred_at.tzinfo == timezone.utc
                assert first.scope == VALIDATION_SCOPE
                assert first.identity_origin == "VALIDATION"
                assert dict(first.safe_metadata.values) == {"processed_count": 1}
                for overrides in (
                    {"action": AuditAction.SCAVENGE_STAGING},
                    {"outcome": AuditOutcome.FAILED},
                    {"safe_metadata": SafeAuditMetadata({"processed_count": 2})},
                    {"scope": replace(VALIDATION_SCOPE, actor_id="other-actor")},
                    {"scope": replace(VALIDATION_SCOPE, identity_mode=IdentityMode.PRODUCT)},
                ):
                    with pytest.raises(ValueError, match="idempotency-conflict"):
                        await append(connection, **overrides)
            async with engine.connect() as connection:
                for table in (chat_turn, project_audit, outbox):
                    assert len((await connection.execute(select(table))).all()) == 1
                row = (await connection.execute(select(project_audit))).mappings().one()
                assert row["content_digest"] == first.content_digest
                assert row["correlation_id"] == "request-1"

            async def concurrent(correlation, *, key="concurrent", count=1):
                async with engine.begin() as connection:
                    return await append(
                        connection,
                        key=key,
                        correlation=correlation,
                        safe_metadata=SafeAuditMetadata({"processed_count": count}),
                    )

            left, right = await asyncio.gather(concurrent("left"), concurrent("right"))
            assert left == right
            from tap.modules.governance.domain.audit import (
                AuditIdempotencyConflict,
                ProjectAuditFact,
            )

            competing = await asyncio.gather(
                concurrent("left", key="competing", count=1),
                concurrent("right", key="competing", count=2),
                return_exceptions=True,
            )
            assert sum(isinstance(value, ProjectAuditFact) for value in competing) == 1
            assert sum(isinstance(value, AuditIdempotencyConflict) for value in competing) == 1
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO project (enterprise_id, project_id) "
                        "VALUES ('local', 'other-project')"
                    )
                )
                other = await append(
                    connection, scope=replace(VALIDATION_SCOPE, project_id="other-project")
                )
                assert other.audit_id != first.audit_id
                assert other.scope.project_id == "other-project"
                await connection.execute(
                    text("INSERT INTO enterprise (enterprise_id) VALUES ('other-enterprise')")
                )
                await connection.execute(
                    text(
                        "INSERT INTO project (enterprise_id, project_id) "
                        "VALUES ('other-enterprise', 'enterprise-project')"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO actor_principal (enterprise_id, actor_id, principal_type) "
                        "VALUES ('other-enterprise', 'enterprise-actor', 'VALIDATION')"
                    )
                )
                enterprise_fact = await append(
                    connection,
                    scope=replace(
                        VALIDATION_SCOPE,
                        enterprise_id="other-enterprise",
                        project_id="enterprise-project",
                        actor_id="enterprise-actor",
                    ),
                )
                assert enterprise_fact.audit_id != first.audit_id
                assert enterprise_fact.scope.enterprise_id == "other-enterprise"
                # Case-distinct command keys identify distinct facts.
                upper = await append(connection, key="Command-1")
                assert upper.audit_id != first.audit_id
            async with engine.connect() as connection:
                assert len((await connection.execute(select(project_audit))).all()) == 6
        finally:
            await engine.dispose()

    asyncio.run(run())
