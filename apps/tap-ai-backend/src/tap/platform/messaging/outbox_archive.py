"""Scoped transactional archive and immutable event evidence for redrive."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.application.ports import LeaseLost
from tap.platform.db.project_scope import require_project_scope, scope_predicates
from tap.platform.db.schema import outbox, outbox_archive, outbox_dead_letter
from tap.platform.messaging.mysql_outbox import validate_outbox_row


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 500:
        raise ValueError("limit must be between 1 and 500")


def safe_dispatch_reason(error: str) -> str:
    return (
        error
        if error in {"invalid_persisted_event", "stream_capacity", "dispatch_unavailable"}
        else "dispatch_unavailable"
    )


async def record_dead_letter(session: AsyncSession, row: Mapping[str, Any], *, reason: str) -> None:
    """Called with the live row locked in its terminal-state transaction."""
    values = {column.name: row[column.name] for column in outbox.c}
    values.update(
        status="delivery_failed",
        claimed_by=None,
        claim_token=None,
        lease_until=None,
        last_error=safe_dispatch_reason(reason),
    )
    statement = mysql_insert(outbox_dead_letter).values(
        **values,
        failed_at=_now(),
        reason=safe_dispatch_reason(reason),
        redriven_at=None,
    )
    # A subsequent failed attempt reopens recovery using the unchanged original
    # evidence. No envelope, digest, actor or event identity is ever overwritten.
    await session.execute(
        statement.on_duplicate_key_update(
            failed_at=statement.inserted.failed_at,
            reason=statement.inserted.reason,
            redriven_at=None,
            attempt_count=statement.inserted.attempt_count,
            last_error=statement.inserted.last_error,
        )
    )


class OutboxArchive:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._scope = require_project_scope(scope)
        self._sessions = sessions

    async def archive_published(self, older_than: datetime, limit: int) -> int:
        _limit(limit)
        if older_than.tzinfo is not None:
            older_than = older_than.astimezone(timezone.utc).replace(tzinfo=None)
        async with self._sessions() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(outbox)
                        .where(
                            *scope_predicates(outbox, self._scope),
                            outbox.c.status == "published",
                            outbox.c.published_at < older_than,
                        )
                        .order_by(outbox.c.published_at, outbox.c.outbox_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                await session.execute(
                    insert(outbox_archive).values(**dict(row), archived_at=_now())
                )
                result = await session.execute(
                    delete(outbox).where(
                        *scope_predicates(outbox, self._scope),
                        outbox.c.outbox_id == row["outbox_id"],
                        outbox.c.status == "published",
                        outbox.c.published_at == row["published_at"],
                        outbox.c.event_content_digest == row["event_content_digest"],
                    )
                )
                if result.rowcount != 1:
                    raise LeaseLost("archive_claim_lost")
            return len(rows)

    async def redrive_dead_letters(self, limit: int) -> int:
        _limit(limit)
        async with self._sessions() as session, session.begin():
            # Import bounded historical terminal rows without recapturing an
            # already-attempted invalid row on every pass.
            missing = (
                (
                    await session.execute(
                        select(outbox)
                        .where(
                            *scope_predicates(outbox, self._scope),
                            outbox.c.status == "delivery_failed",
                            ~select(outbox_dead_letter.c.outbox_id)
                            .where(outbox_dead_letter.c.outbox_id == outbox.c.outbox_id)
                            .exists(),
                        )
                        .order_by(outbox.c.created_at, outbox.c.outbox_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            for row in missing:
                try:
                    validate_outbox_row(dict(row))
                    reason = "dispatch_unavailable"
                except ValueError:
                    reason = "invalid_persisted_event"
                await record_dead_letter(session, dict(row), reason=reason)
            # Lock live rows first, like claim/settlement, avoiding opposite lock
            # order between a redrive and a concurrently failed publication.
            rows = (
                (
                    await session.execute(
                        select(outbox)
                        .join(
                            outbox_dead_letter, outbox.c.outbox_id == outbox_dead_letter.c.outbox_id
                        )
                        .where(
                            *scope_predicates(outbox, self._scope),
                            *scope_predicates(outbox_dead_letter, self._scope),
                            outbox.c.status == "delivery_failed",
                            outbox_dead_letter.c.redriven_at.is_(None),
                            outbox_dead_letter.c.reason != "invalid_persisted_event",
                        )
                        .order_by(outbox_dead_letter.c.failed_at, outbox.c.outbox_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            recovered = 0
            for row in rows:
                evidence = (
                    (
                        await session.execute(
                            select(outbox_dead_letter)
                            .where(
                                *scope_predicates(outbox_dead_letter, self._scope),
                                outbox_dead_letter.c.outbox_id == row["outbox_id"],
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                try:
                    original = validate_outbox_row(dict(evidence))
                    current = validate_outbox_row(dict(row))
                    if original.to_dict() != current.to_dict():
                        raise ValueError("event evidence changed")
                except ValueError:
                    await session.execute(
                        update(outbox_dead_letter)
                        .where(
                            outbox_dead_letter.c.outbox_id == row["outbox_id"],
                            *scope_predicates(outbox_dead_letter, self._scope),
                        )
                        .values(reason="invalid_persisted_event")
                    )
                    continue
                result = await session.execute(
                    update(outbox)
                    .where(
                        *scope_predicates(outbox, self._scope),
                        outbox.c.outbox_id == row["outbox_id"],
                        outbox.c.status == "delivery_failed",
                        outbox.c.event_content_digest == evidence["event_content_digest"],
                    )
                    .values(
                        status="pending",
                        attempt_count=0,
                        next_attempt_at=_now(),
                        claimed_by=None,
                        claim_token=None,
                        lease_until=None,
                        last_error=None,
                    )
                )
                if result.rowcount != 1:
                    raise LeaseLost("redrive_claim_lost")
                await session.execute(
                    update(outbox_dead_letter)
                    .where(
                        *scope_predicates(outbox_dead_letter, self._scope),
                        outbox_dead_letter.c.outbox_id == row["outbox_id"],
                        outbox_dead_letter.c.redriven_at.is_(None),
                    )
                    .values(redriven_at=_now())
                )
                recovered += 1
            return recovered
