"""Project events written in the caller transaction and validated before delivery."""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tap.contracts.events import ProjectEventEnvelope, event_content_digest
from tap.modules.access.domain.context import ProjectScopeContext
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import outbox


def scoped_outbox_id(scope: ProjectScopeContext, *, kind: str, identity: str) -> str:
    scope = require_project_scope(scope)
    material = json.dumps(
        [scope.enterprise_id, scope.project_id, kind, identity], separators=(",", ":")
    )
    return "project-event:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def compatibility_outbox_values(
    scope: ProjectScopeContext,
    *,
    outbox_id: str,
    command_id: str,
    aggregate_type: str,
    aggregate_id: str,
    message_type: str,
    sequence: int | None,
    created_at: datetime,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    identity = scope_values(scope)
    occurred_at = (
        created_at.replace(tzinfo=timezone.utc)
        if created_at.tzinfo is None
        else created_at.astimezone(timezone.utc)
    )
    envelope = ProjectEventEnvelope(
        event_id=outbox_id,
        event_type=message_type,
        schema_version=1,
        occurred_at=occurred_at,
        scope_kind="PROJECT",
        enterprise_id=scope.enterprise_id,
        project_id=scope.project_id,
        actor_id=scope.actor_id,
        identity_mode=scope.identity_mode.value,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=sequence if sequence is not None else 0,
        correlation_id=outbox_id if correlation_id is None else correlation_id,
        causation_id=None,
        idempotency_key=command_id,
        payload={"aggregateId": aggregate_id, "sequence": sequence},
    )
    return {
        **identity,
        "outbox_id": outbox_id,
        "command_id": command_id,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "sequence": sequence,
        "message_type": message_type,
        "created_at": occurred_at.replace(tzinfo=None),
        "envelope": envelope.to_dict(),
        "event_content_digest": event_content_digest(envelope),
    }


def validate_outbox_row(row: Mapping[str, Any]) -> ProjectEventEnvelope:
    try:
        envelope = ProjectEventEnvelope.from_dict(row["envelope"])
        expected = {
            "enterprise_id": envelope.enterprise_id,
            "project_id": envelope.project_id,
            "actor_id": envelope.actor_id,
            "identity_mode": envelope.identity_mode,
            "identity_origin": envelope.identity_mode.upper(),
            "outbox_id": envelope.event_id,
            "command_id": envelope.idempotency_key,
            "aggregate_type": envelope.aggregate_type,
            "aggregate_id": envelope.aggregate_id,
            "sequence": envelope.payload.get("sequence"),
            "message_type": envelope.event_type,
            "event_content_digest": event_content_digest(envelope),
        }
        if any(row[name] != value for name, value in expected.items()):
            raise ValueError("outbox envelope contradicts relational facts")
        created = row["created_at"]
        if not isinstance(created, datetime):
            raise ValueError("invalid outbox creation time")
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created != envelope.occurred_at:
            raise ValueError("outbox event timestamp contradicts creation time")
    except (KeyError, TypeError) as error:
        raise ValueError("invalid persisted outbox envelope") from error
    return envelope


class OutboxIdempotencyConflict(ValueError):
    """The scoped event key or event ID already identifies different event facts."""


async def write_project_event(
    transaction: AsyncSession | AsyncConnection,
    *,
    scope: ProjectScopeContext,
    envelope: ProjectEventEnvelope,
) -> ProjectEventEnvelope:
    """Persist or replay an event without beginning or committing a transaction.

    Resource ownership and business request equality belong to the caller's
    scoped application transaction. This writer checks trusted envelope scope
    and event-content equality, retaining the original ID and timestamp on replay.
    """
    identity = scope_values(scope)
    if not transaction.in_transaction():
        raise ValueError("event writer requires an active transaction")
    if any(
        getattr(envelope, field) != identity[field]
        for field in ("enterprise_id", "project_id", "actor_id", "identity_mode")
    ):
        raise ValueError("event envelope scope differs from trusted scope")
    created_at = envelope.occurred_at.astimezone(timezone.utc).replace(tzinfo=None)
    values = {
        **identity,
        "outbox_id": envelope.event_id,
        "command_id": envelope.idempotency_key,
        "aggregate_type": envelope.aggregate_type,
        "aggregate_id": envelope.aggregate_id,
        "sequence": envelope.payload.get("sequence"),
        "message_type": envelope.event_type,
        "created_at": created_at,
        "envelope": envelope.to_dict(),
        "event_content_digest": event_content_digest(envelope),
        "status": "pending",
        "attempt_count": 0,
        "next_attempt_at": created_at,
    }
    # Let the unique key serialize concurrent writers without an absent-row
    # SELECT/gap lock. The duplicate branch preserves every original event fact.
    await transaction.execute(
        insert(outbox).values(**values).on_duplicate_key_update(outbox_id=outbox.c.outbox_id)
    )
    row = (
        (
            await transaction.execute(
                select(outbox)
                .where(
                    *scope_predicates(outbox, scope),
                    outbox.c.command_id == envelope.idempotency_key,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise OutboxIdempotencyConflict("idempotency-conflict")
    persisted = validate_outbox_row(cast(Mapping[str, Any], row))
    if row["event_content_digest"] != values["event_content_digest"]:
        raise OutboxIdempotencyConflict("idempotency-conflict")
    return persisted
