"""MySQL prompt suggestion refresh queue and suggestion cache with lease fencing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import uuid4

from sqlalchemy import (
    CHAR,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.application.suggestion_ports import ClaimedRefresh
from tap.modules.chat.domain.suggestions import (
    DAILY_REFRESH,
    REFRESH_THROTTLE,
    PromptSuggestion,
    RefreshReason,
    SuggestionKey,
    SuggestionSource,
    next_due_at,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata


def _scope_constraints(name: str):
    return (
        ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    )


prompt_suggestion_refresh = Table(
    "prompt_suggestion_refresh",
    metadata,
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), primary_key=True),
    Column("actor_id", String(128), primary_key=True),
    Column("identity_mode", String(16), nullable=False),
    Column("identity_origin", String(16), nullable=False),
    Column("locale", CHAR(2), primary_key=True),
    Column("due_at", DATETIME(fsp=6), nullable=True),
    Column("last_reason", String(32), nullable=True),
    Column("last_refreshed_at", DATETIME(fsp=6), nullable=True),
    Column("lease_owner", String(128), nullable=True),
    Column("lease_token", CHAR(32), nullable=True),
    Column("lease_expires_at", DATETIME(fsp=6), nullable=True),
    Column("claimed_due_at", DATETIME(fsp=6), nullable=True),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("failure_code", String(64), nullable=True),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint(
        "enterprise_id",
        "project_id",
        "actor_id",
        "locale",
        name="uq_prompt_suggestion_refresh_identity",
    ),
    Index("ix_prompt_suggestion_refresh_due", "project_id", "due_at"),
    *_scope_constraints("prompt_suggestion_refresh"),
)

prompt_suggestion = Table(
    "prompt_suggestion",
    metadata,
    Column("suggestion_id", CHAR(32), primary_key=True),
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), primary_key=True),
    Column("actor_id", String(128), nullable=False),
    Column("identity_mode", String(16), nullable=False),
    Column("identity_origin", String(16), nullable=False),
    Column("locale", CHAR(2), nullable=False),
    Column("position", SmallInteger, nullable=False),
    Column("question", String(500), nullable=False),
    Column("sources_json", JSON, nullable=False),
    Column("generated_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("project_id", "suggestion_id", name="uq_prompt_suggestion_project_pk"),
    Index(
        "ix_prompt_suggestion_actor_locale_position",
        "project_id",
        "actor_id",
        "locale",
        "position",
    ),
    *_scope_constraints("prompt_suggestion"),
)


@dataclass
class _RefreshRow:
    due_at: datetime | None
    last_refreshed_at: datetime | None
    lease_expires_at: datetime | None
    attempt_count: int


def _naive(value: datetime) -> datetime:
    """MySQL DATETIME columns are naive; normalize aware inputs to naive UTC so
    they compare directly against values read back from the driver."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _effective_current_due_at(
    due_at: datetime | None, lease_expires_at: datetime | None, *, now: datetime
) -> datetime | None:
    """While a claim is actively leased, due_at is frozen at the claimed value
    until completion; a fresh request must not be clamped to that stale value,
    or it would collapse back to the value complete_refresh clears."""
    if lease_expires_at is not None and lease_expires_at > now:
        return None
    return due_at


def _sources_json(sources: tuple[SuggestionSource, ...]) -> list[dict[str, str]]:
    return [{"sourceId": source.source_id, "version": source.version} for source in sources]


def _sources_from_json(raw: list[dict[str, str]]) -> tuple[SuggestionSource, ...]:
    return tuple(SuggestionSource(item["sourceId"], item["version"]) for item in raw)


class MysqlSuggestionStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def load(self, key: SuggestionKey) -> tuple[PromptSuggestion, ...] | None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(prompt_suggestion_refresh.c.last_refreshed_at).where(
                            *scope_predicates(prompt_suggestion_refresh, self._scope),
                            prompt_suggestion_refresh.c.actor_id == key.actor_id,
                            prompt_suggestion_refresh.c.locale == key.locale,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["last_refreshed_at"] is None:
                return None
            rows = (
                (
                    await session.execute(
                        select(prompt_suggestion)
                        .where(
                            *scope_predicates(prompt_suggestion, self._scope),
                            prompt_suggestion.c.actor_id == key.actor_id,
                            prompt_suggestion.c.locale == key.locale,
                        )
                        .order_by(prompt_suggestion.c.position)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            PromptSuggestion(
                suggestion_id=r["suggestion_id"],
                question=r["question"],
                sources=_sources_from_json(r["sources_json"]),
            )
            for r in rows
        )

    async def request_refresh(
        self, key: SuggestionKey, reason: RefreshReason, *, now: datetime
    ) -> None:
        now = _naive(now)
        try:
            async with self._sessions() as session, session.begin():
                await self._request_refresh_locked(session, key, reason, now=now)
        except (IntegrityError, OperationalError) as error:
            # A concurrent first-ever request for this key (e.g. a read's
            # MISSING trigger racing a turn-completion trigger) can lose the
            # unique-key insert race. Two SELECT ... FOR UPDATE probes that
            # both find no row take an InnoDB gap lock on the same range, so
            # MySQL reports this either as a plain duplicate-entry
            # IntegrityError or, if both inserts land at once, as a 1213
            # deadlock OperationalError with one transaction picked as the
            # victim and rolled back. Either way the loser's transaction is
            # already rolled back; retry once in a fresh transaction, where
            # the winner's row now exists and this takes the update branch
            # instead.
            if isinstance(error, OperationalError) and "1213" not in str(error):
                raise
            async with self._sessions() as session, session.begin():
                await self._request_refresh_locked(session, key, reason, now=now)

    async def _locked_row(self, session: AsyncSession, key: SuggestionKey) -> _RefreshRow | None:
        row = (
            (
                await session.execute(
                    select(prompt_suggestion_refresh)
                    .where(
                        *scope_predicates(prompt_suggestion_refresh, self._scope),
                        prompt_suggestion_refresh.c.actor_id == key.actor_id,
                        prompt_suggestion_refresh.c.locale == key.locale,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return _RefreshRow(
            due_at=cast("datetime | None", row["due_at"]),
            last_refreshed_at=cast("datetime | None", row["last_refreshed_at"]),
            lease_expires_at=cast("datetime | None", row["lease_expires_at"]),
            attempt_count=cast(int, row["attempt_count"]),
        )

    async def _apply_due_update(
        self,
        session: AsyncSession,
        *,
        actor_id: str,
        locale: str,
        reason: RefreshReason,
        due_at: datetime | None,
        last_refreshed_at: datetime | None,
        lease_expires_at: datetime | None,
        now: datetime,
    ) -> None:
        """Recompute due_at for one existing row (via next_due_at, guarded by
        _effective_current_due_at) and persist it. Shared by every call site
        that updates an already-existing prompt_suggestion_refresh row."""
        computed = next_due_at(
            reason,
            now=now,
            last_refreshed_at=last_refreshed_at,
            current_due_at=_effective_current_due_at(due_at, lease_expires_at, now=now),
        )
        await session.execute(
            update(prompt_suggestion_refresh)
            .where(
                *scope_predicates(prompt_suggestion_refresh, self._scope),
                prompt_suggestion_refresh.c.actor_id == actor_id,
                prompt_suggestion_refresh.c.locale == locale,
            )
            .values(due_at=computed, last_reason=reason.value, updated_at=now)
        )

    async def _request_refresh_locked(
        self, session: AsyncSession, key: SuggestionKey, reason: RefreshReason, *, now: datetime
    ) -> None:
        existing = await self._locked_row(session, key)
        if existing is None:
            due_at = next_due_at(reason, now=now, last_refreshed_at=None, current_due_at=None)
            await session.execute(
                insert(prompt_suggestion_refresh).values(
                    **{**scope_values(self._scope), "actor_id": key.actor_id},
                    locale=key.locale,
                    due_at=due_at,
                    last_reason=reason.value,
                    last_refreshed_at=None,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    claimed_due_at=None,
                    attempt_count=0,
                    failure_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            await self._apply_due_update(
                session,
                actor_id=key.actor_id,
                locale=key.locale,
                reason=reason,
                due_at=existing.due_at,
                last_refreshed_at=existing.last_refreshed_at,
                lease_expires_at=existing.lease_expires_at,
                now=now,
            )

    async def request_refresh_for_actor(
        self, actor_id: str, reason: RefreshReason, *, now: datetime
    ) -> int:
        now = _naive(now)
        async with self._sessions() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(
                            prompt_suggestion_refresh.c.locale,
                            prompt_suggestion_refresh.c.due_at,
                            prompt_suggestion_refresh.c.last_refreshed_at,
                            prompt_suggestion_refresh.c.lease_expires_at,
                        )
                        .where(
                            *scope_predicates(prompt_suggestion_refresh, self._scope),
                            prompt_suggestion_refresh.c.actor_id == actor_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            touched = 0
            for row in rows:
                await self._apply_due_update(
                    session,
                    actor_id=actor_id,
                    locale=row["locale"],
                    reason=reason,
                    due_at=row["due_at"],
                    last_refreshed_at=row["last_refreshed_at"],
                    lease_expires_at=row["lease_expires_at"],
                    now=now,
                )
                touched += 1
        return touched

    async def request_refresh_for_project_in_transaction(
        self, session: AsyncSession, reason: RefreshReason, *, now: datetime
    ) -> int:
        if not session.in_transaction():
            raise ValueError("prompt suggestion project refresh requires an active transaction")
        now = _naive(now)
        rows = (
            (
                await session.execute(
                    select(
                        prompt_suggestion_refresh.c.actor_id,
                        prompt_suggestion_refresh.c.locale,
                        prompt_suggestion_refresh.c.due_at,
                        prompt_suggestion_refresh.c.last_refreshed_at,
                        prompt_suggestion_refresh.c.lease_expires_at,
                    )
                    .where(*scope_predicates(prompt_suggestion_refresh, self._scope))
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        touched = 0
        for row in rows:
            await self._apply_due_update(
                session,
                actor_id=row["actor_id"],
                locale=row["locale"],
                reason=reason,
                due_at=row["due_at"],
                last_refreshed_at=row["last_refreshed_at"],
                lease_expires_at=row["lease_expires_at"],
                now=now,
            )
            touched += 1
        return touched

    async def request_daily_refresh(self, *, now: datetime) -> int:
        now = _naive(now)
        async with self._sessions() as session, session.begin():
            threshold = now - DAILY_REFRESH
            rows = (
                (
                    await session.execute(
                        select(
                            prompt_suggestion_refresh.c.actor_id,
                            prompt_suggestion_refresh.c.locale,
                            prompt_suggestion_refresh.c.last_refreshed_at,
                        )
                        .where(
                            *scope_predicates(prompt_suggestion_refresh, self._scope),
                            prompt_suggestion_refresh.c.due_at.is_(None),
                            prompt_suggestion_refresh.c.last_refreshed_at.is_not(None),
                            prompt_suggestion_refresh.c.last_refreshed_at < threshold,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            touched = 0
            for row in rows:
                due_at = next_due_at(
                    RefreshReason.DAILY,
                    now=now,
                    last_refreshed_at=row["last_refreshed_at"],
                    current_due_at=None,
                )
                await session.execute(
                    update(prompt_suggestion_refresh)
                    .where(
                        *scope_predicates(prompt_suggestion_refresh, self._scope),
                        prompt_suggestion_refresh.c.actor_id == row["actor_id"],
                        prompt_suggestion_refresh.c.locale == row["locale"],
                    )
                    .values(due_at=due_at, last_reason=RefreshReason.DAILY.value, updated_at=now)
                )
                touched += 1
        return touched

    async def claim_due(
        self, *, worker_id: str, now: datetime, limit: int, lease_duration: timedelta
    ) -> tuple[ClaimedRefresh, ...]:
        if not worker_id:
            raise ValueError("prompt suggestion worker id must be nonblank")
        if type(limit) is not int or limit < 1:
            raise ValueError("prompt suggestion claim limit must be positive")
        if not timedelta(0) < lease_duration <= timedelta(minutes=15):
            raise ValueError("prompt suggestion lease duration is invalid")
        now = _naive(now)
        claims: list[ClaimedRefresh] = []
        async with self._sessions() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(
                            prompt_suggestion_refresh.c.actor_id,
                            prompt_suggestion_refresh.c.locale,
                            prompt_suggestion_refresh.c.due_at,
                            prompt_suggestion_refresh.c.attempt_count,
                        )
                        .where(
                            *scope_predicates(prompt_suggestion_refresh, self._scope),
                            prompt_suggestion_refresh.c.due_at.is_not(None),
                            prompt_suggestion_refresh.c.due_at <= now,
                            (prompt_suggestion_refresh.c.lease_expires_at.is_(None))
                            | (prompt_suggestion_refresh.c.lease_expires_at <= now),
                        )
                        .order_by(prompt_suggestion_refresh.c.due_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                token = uuid4().hex
                expires = now + lease_duration
                await session.execute(
                    update(prompt_suggestion_refresh)
                    .where(
                        *scope_predicates(prompt_suggestion_refresh, self._scope),
                        prompt_suggestion_refresh.c.actor_id == row["actor_id"],
                        prompt_suggestion_refresh.c.locale == row["locale"],
                    )
                    .values(
                        lease_owner=worker_id,
                        lease_token=token,
                        lease_expires_at=expires,
                        claimed_due_at=row["due_at"],
                        updated_at=now,
                    )
                )
                claims.append(
                    ClaimedRefresh(
                        key=SuggestionKey(actor_id=row["actor_id"], locale=row["locale"]),
                        lease_token=token,
                        attempt_count=row["attempt_count"],
                    )
                )
        return tuple(claims)

    async def complete_refresh(
        self,
        claim: ClaimedRefresh,
        suggestions: tuple[PromptSuggestion, ...],
        *,
        now: datetime,
    ) -> bool:
        now = _naive(now)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(
                            prompt_suggestion_refresh.c.due_at,
                            prompt_suggestion_refresh.c.claimed_due_at,
                        )
                        .where(
                            *scope_predicates(prompt_suggestion_refresh, self._scope),
                            prompt_suggestion_refresh.c.actor_id == claim.key.actor_id,
                            prompt_suggestion_refresh.c.locale == claim.key.locale,
                            prompt_suggestion_refresh.c.lease_token == claim.lease_token,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return False
            await session.execute(
                delete(prompt_suggestion).where(
                    *scope_predicates(prompt_suggestion, self._scope),
                    prompt_suggestion.c.actor_id == claim.key.actor_id,
                    prompt_suggestion.c.locale == claim.key.locale,
                )
            )
            for position, suggestion in enumerate(suggestions):
                await session.execute(
                    insert(prompt_suggestion).values(
                        **{**scope_values(self._scope), "actor_id": claim.key.actor_id},
                        suggestion_id=suggestion.suggestion_id,
                        locale=claim.key.locale,
                        position=position,
                        question=suggestion.question,
                        sources_json=_sources_json(suggestion.sources),
                        generated_at=now,
                    )
                )
            next_due_at_value = None if row["due_at"] == row["claimed_due_at"] else row["due_at"]
            result = await session.execute(
                update(prompt_suggestion_refresh)
                .where(
                    *scope_predicates(prompt_suggestion_refresh, self._scope),
                    prompt_suggestion_refresh.c.actor_id == claim.key.actor_id,
                    prompt_suggestion_refresh.c.locale == claim.key.locale,
                    prompt_suggestion_refresh.c.lease_token == claim.lease_token,
                )
                .values(
                    last_refreshed_at=now,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    claimed_due_at=None,
                    failure_code=None,
                    due_at=next_due_at_value,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return False
        return True

    async def fail_refresh(
        self, claim: ClaimedRefresh, failure_code: str, *, now: datetime
    ) -> bool:
        if not failure_code or len(failure_code) > 64:
            raise ValueError("prompt suggestion failure code must be bounded")
        now = _naive(now)
        async with self._sessions() as session, session.begin():
            row = (
                await session.execute(
                    select(prompt_suggestion_refresh.c.attempt_count).where(
                        *scope_predicates(prompt_suggestion_refresh, self._scope),
                        prompt_suggestion_refresh.c.actor_id == claim.key.actor_id,
                        prompt_suggestion_refresh.c.locale == claim.key.locale,
                        prompt_suggestion_refresh.c.lease_token == claim.lease_token,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            result = await session.execute(
                update(prompt_suggestion_refresh)
                .where(
                    *scope_predicates(prompt_suggestion_refresh, self._scope),
                    prompt_suggestion_refresh.c.actor_id == claim.key.actor_id,
                    prompt_suggestion_refresh.c.locale == claim.key.locale,
                    prompt_suggestion_refresh.c.lease_token == claim.lease_token,
                )
                .values(
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    claimed_due_at=None,
                    attempt_count=row + 1,
                    failure_code=failure_code,
                    due_at=now + REFRESH_THROTTLE,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return False
        return True
