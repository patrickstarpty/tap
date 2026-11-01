"""`skill_maintenance_event` table definition: Skill maintenance actions
(`revised`, `enabled`, `disabled`, `owner_transferred`, `synced`; spec 4),
written in the same transaction as the Skill write they record; and
`MysqlSkillHubStore` (Task 8): the Skill Hub MySQL read models and writes.

`skill`/`skill_revision`/`ai_agent_revision` are imported lazily, inside the
methods that need them: `tap.modules.ai.adapters.mysql` imports
`insert_skill_maintenance_event` from this module at its own module scope,
so an eager import of that module here would break the cycle the first time
either module loads before the other finishes executing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Column, ColumnElement, Index, String, Table, distinct, func, select, update
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.adapters.mysql import actor_principal
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.agent_team.adapters.agent_keys import resolve_agent_keys
from tap.modules.ai.domain.assets import AssetRevisionStatus
from tap.modules.ai.domain.skill_metadata import (
    ActivationMode,
    OriginKind,
    SkillCategory,
    compatibility_of,
    display_version,
)
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import turn_input_snapshot
from tap.modules.chat.adapters.mysql_feedback import answer_feedback
from tap.modules.chat.adapters.mysql_skill_activations import turn_skill_activation
from tap.modules.skill_hub.domain.models import SkillMaintenanceEvent
from tap.modules.skill_hub.ports import (
    ActivationCount,
    Granularity,
    RankedKey,
    SkillRatings,
    SkillRecord,
    UsagePoint,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import augment_project_table, metadata
from tap.platform.reporting.period import Period

skill_maintenance_event = Table(
    "skill_maintenance_event",
    metadata,
    Column("event_id", String(64), primary_key=True),
    Column("skill_id", String(64), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("revision_id", String(64), nullable=True),
    Column("note", String(500), nullable=True),
    Column("occurred_at", DATETIME(fsp=6), nullable=False),
)
# `skill_id -> skill.skill_id` is registered in `PROJECT_PARENT_LINKS`
# (platform/db/schema.py) so `augment_project_table` wires the FK itself.
augment_project_table(skill_maintenance_event)
Index(
    "ix_skill_maintenance_event_project_skill_occurred",
    skill_maintenance_event.c.project_id,
    skill_maintenance_event.c.skill_id,
    skill_maintenance_event.c.occurred_at,
)


async def insert_skill_maintenance_event(
    session: AsyncSession, scope: ProjectScopeContext, event: SkillMaintenanceEvent
) -> None:
    """The one `skill_maintenance_event` insert, run inside the caller's
    transaction so the event and the Skill write it records commit or roll
    back together. An `IntegrityError` here (duplicate `event_id`, or the
    Skill FK) fails the whole transaction, never just the event."""
    await session.execute(
        skill_maintenance_event.insert().values(
            event_id=event.event_id,
            skill_id=event.skill_id,
            kind=event.kind,
            revision_id=event.revision_id,
            note=event.note,
            occurred_at=event.occurred_at,
            **scope_values(scope),
        )
    )


_BUILT_IN_SOURCE_KIND = "built_in"
# Closed-set priority order (manual > pinned > auto) used to order trend points.
_MODE_ORDER: dict[str, int] = {"manual": 0, "pinned": 1, "auto": 2}


def _naive(value: datetime) -> datetime:
    """MySQL DATETIME columns are naive (always UTC)."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware(value: datetime) -> datetime:
    """Attach UTC to a naive value read back from a DATETIME column."""
    return value.replace(tzinfo=UTC)


def _bounds(period: Period) -> tuple[datetime, datetime]:
    start, end_exclusive = period.utc_bounds()
    return _naive(start), _naive(end_exclusive)


def _bucket_start(day: date, granularity: Granularity) -> date:
    """UTC buckets; weeks start on Monday."""
    if granularity == "day":
        return day
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _strings(value: Any) -> tuple[str, ...]:
    """A JSON string list column; `NULL` (or any non-list) reads as empty."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _turn_join():
    """`turn_skill_activation` joined to the Turn it belongs to."""
    return turn_skill_activation.join(
        chat_turn,
        (chat_turn.c.turn_id == turn_skill_activation.c.turn_id)
        & (chat_turn.c.project_id == turn_skill_activation.c.project_id),
    )


class MysqlSkillHubStore:
    """`SkillHubStore` over `skill`/`skill_revision`, `turn_skill_activation`,
    `chat_turn`/`turn_input_snapshot`, `answer_feedback`, `ai_agent_revision`,
    `skill_maintenance_event` and `actor_principal`, scoped to one Project."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    def _activated_in(self, period: Period) -> list[ColumnElement[bool]]:
        start, end_exclusive = _bounds(period)
        return [
            *scope_predicates(turn_skill_activation, self._scope),
            turn_skill_activation.c.activated_at >= start,
            turn_skill_activation.c.activated_at < end_exclusive,
        ]

    async def _skill_rows(self, session: AsyncSession, *, skill_id: str | None = None):
        from tap.modules.ai.adapters.mysql import skill, skill_revision

        latest_numbers = (
            select(
                skill_revision.c.skill_id,
                func.max(skill_revision.c.revision_number).label("revision_number"),
            )
            .where(*scope_predicates(skill_revision, self._scope))
            .group_by(skill_revision.c.skill_id)
            .subquery()
        )
        query = (
            select(
                skill.c.skill_id,
                skill.c.category,
                skill.c.owner_actor_id,
                skill.c.owner_assigned_at,
                skill_revision.c.revision_id,
                skill_revision.c.revision_number,
                skill_revision.c.display_name,
                skill_revision.c.description,
                skill_revision.c.source_kind,
                skill_revision.c.version_label,
                skill_revision.c.origin_kind,
                skill_revision.c.origin_ref,
                skill_revision.c.origin_imported_at,
                skill_revision.c.status,
                skill_revision.c.applicable_tasks,
                skill_revision.c.frontmatter_json,
                skill_revision.c.created_at,
            )
            .select_from(
                skill.join(
                    skill_revision,
                    (skill.c.skill_id == skill_revision.c.skill_id)
                    & (skill.c.project_id == skill_revision.c.project_id),
                ).join(
                    latest_numbers,
                    (skill_revision.c.skill_id == latest_numbers.c.skill_id)
                    & (skill_revision.c.revision_number == latest_numbers.c.revision_number),
                )
            )
            .where(*scope_predicates(skill, self._scope))
            .order_by(skill.c.skill_id)
        )
        if skill_id is not None:
            query = query.where(skill.c.skill_id == skill_id)
        return (await session.execute(query)).mappings().all()

    @staticmethod
    def _to_record(row) -> SkillRecord:
        imported_at = row["origin_imported_at"]
        return SkillRecord(
            skill_id=row["skill_id"],
            name=row["display_name"],
            description=row["description"],
            category=cast(SkillCategory, row["category"]),
            version=display_version(row["version_label"], row["revision_number"]),
            latest_revision_id=row["revision_id"],
            revision_number=row["revision_number"],
            source_kind=row["source_kind"],
            origin_kind=cast(OriginKind, row["origin_kind"]),
            origin_ref=row["origin_ref"],
            origin_imported_at=_aware(imported_at) if imported_at is not None else None,
            owner_actor_id=row["owner_actor_id"],
            owner_assigned_at=_aware(row["owner_assigned_at"]),
            status=row["status"],
            applicable_tasks=tuple(sorted(_strings(row["applicable_tasks"]))),
            compatibility=compatibility_of(row["frontmatter_json"]),
            updated_at=_aware(row["created_at"]),
            built_in=row["source_kind"] == _BUILT_IN_SOURCE_KIND,
        )

    async def skills(self) -> list[SkillRecord]:
        async with self._sessions() as session:
            rows = await self._skill_rows(session)
        return [self._to_record(row) for row in rows]

    async def skill(self, skill_id: str) -> SkillRecord | None:
        async with self._sessions() as session:
            rows = await self._skill_rows(session, skill_id=skill_id)
        return self._to_record(rows[0]) if rows else None

    async def activation_counts(self, period: Period) -> list[ActivationCount]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        turn_skill_activation.c.skill_id,
                        turn_skill_activation.c.mode,
                        func.count().label("runs"),
                    )
                    .where(*self._activated_in(period))
                    .group_by(turn_skill_activation.c.skill_id, turn_skill_activation.c.mode)
                    .order_by(turn_skill_activation.c.skill_id, turn_skill_activation.c.mode)
                )
            ).all()
        return [
            ActivationCount(
                skill_id=row.skill_id, mode=cast(ActivationMode, row.mode), runs=row.runs
            )
            for row in rows
        ]

    async def distinct_users(self, period: Period) -> dict[str, int]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        turn_skill_activation.c.skill_id,
                        func.count(distinct(chat_turn.c.actor_id)).label("users"),
                    )
                    .select_from(_turn_join())
                    .where(*self._activated_in(period), *scope_predicates(chat_turn, self._scope))
                    .group_by(turn_skill_activation.c.skill_id)
                )
            ).all()
        return {row.skill_id: row.users for row in rows}

    async def ratings(self, period: Period) -> list[SkillRatings]:
        start, end_exclusive = _bounds(period)
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        turn_skill_activation.c.skill_id,
                        answer_feedback.c.rating,
                        func.count().label("ratings"),
                    )
                    .select_from(
                        _turn_join().join(
                            answer_feedback,
                            (answer_feedback.c.turn_id == chat_turn.c.turn_id)
                            & (answer_feedback.c.project_id == chat_turn.c.project_id),
                        )
                    )
                    .where(
                        *scope_predicates(turn_skill_activation, self._scope),
                        *scope_predicates(chat_turn, self._scope),
                        chat_turn.c.created_at >= start,
                        chat_turn.c.created_at < end_exclusive,
                        answer_feedback.c.rating.in_(("up", "down")),
                    )
                    .group_by(turn_skill_activation.c.skill_id, answer_feedback.c.rating)
                )
            ).all()
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"up": 0, "down": 0})
        for row in rows:
            counts[row.skill_id][row.rating] += row.ratings
        return [
            SkillRatings(skill_id=skill_id, up=value["up"], down=value["down"])
            for skill_id, value in sorted(counts.items())
        ]

    async def last_used(self) -> dict[str, datetime]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        turn_skill_activation.c.skill_id,
                        func.max(turn_skill_activation.c.activated_at).label("activated_at"),
                    )
                    .where(*scope_predicates(turn_skill_activation, self._scope))
                    .group_by(turn_skill_activation.c.skill_id)
                )
            ).all()
        return {row.skill_id: _aware(row.activated_at) for row in rows}

    async def pinned_by(self) -> dict[str, tuple[str, ...]]:
        from tap.modules.ai.adapters.mysql import ai_agent_revision

        # "Latest revision" as the Agent catalog defines it (`list_agents`).
        latest_numbers = (
            select(
                ai_agent_revision.c.agent_id,
                func.max(ai_agent_revision.c.revision_number).label("revision_number"),
            )
            .where(
                *scope_predicates(ai_agent_revision, self._scope),
                ai_agent_revision.c.system_instruction.is_not(None),
                ai_agent_revision.c.output_schema_json.is_not(None),
            )
            .group_by(ai_agent_revision.c.agent_id)
            .subquery()
        )
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(ai_agent_revision.c.agent_id, ai_agent_revision.c.pinned_skill_ids)
                    .join(
                        latest_numbers,
                        (ai_agent_revision.c.agent_id == latest_numbers.c.agent_id)
                        & (ai_agent_revision.c.revision_number == latest_numbers.c.revision_number),
                    )
                    .where(
                        *scope_predicates(ai_agent_revision, self._scope),
                        ai_agent_revision.c.status == AssetRevisionStatus.ENABLED.value,
                    )
                )
            ).all()
        agents: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            for skill_id in _strings(row.pinned_skill_ids):
                agents[skill_id].add(row.agent_id)
        return {skill_id: tuple(sorted(ids)) for skill_id, ids in agents.items()}

    async def usage_trend(
        self, skill_id: str, period: Period, granularity: Granularity
    ) -> list[UsagePoint]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        turn_skill_activation.c.activated_at, turn_skill_activation.c.mode
                    ).where(
                        *self._activated_in(period), turn_skill_activation.c.skill_id == skill_id
                    )
                )
            ).all()
        runs: Counter[tuple[date, str]] = Counter(
            (_bucket_start(row.activated_at.date(), granularity), row.mode) for row in rows
        )
        return [
            UsagePoint(bucket_start=bucket, mode=cast(ActivationMode, mode), runs=count)
            for (bucket, mode), count in sorted(
                runs.items(), key=lambda item: (item[0][0], _MODE_ORDER.get(item[0][1], 99))
            )
        ]

    async def top_agents(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(turn_skill_activation.c.turn_id, turn_input_snapshot.c.snapshot)
                    .select_from(
                        turn_skill_activation.outerjoin(
                            turn_input_snapshot,
                            (turn_input_snapshot.c.turn_id == turn_skill_activation.c.turn_id)
                            & (
                                turn_input_snapshot.c.project_id
                                == turn_skill_activation.c.project_id
                            ),
                        )
                    )
                    .where(
                        *self._activated_in(period), turn_skill_activation.c.skill_id == skill_id
                    )
                )
            ).all()
            agent_keys = await resolve_agent_keys(
                session, self._scope.project_id, {row.turn_id: row.snapshot for row in rows}
            )
        runs = Counter(agent_keys.values())
        ranked = sorted(runs.items(), key=lambda item: (-item[1], item[0]))
        return [RankedKey(key=key, runs=count) for key, count in ranked[:limit]]

    async def top_callers(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        runs = func.count().label("runs")
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(chat_turn.c.actor_id, runs)
                    .select_from(_turn_join())
                    .where(
                        *self._activated_in(period),
                        *scope_predicates(chat_turn, self._scope),
                        turn_skill_activation.c.skill_id == skill_id,
                    )
                    .group_by(chat_turn.c.actor_id)
                    .order_by(runs.desc(), chat_turn.c.actor_id)
                    .limit(limit)
                )
            ).all()
        return [RankedKey(key=row.actor_id, runs=row.runs) for row in rows]

    async def actor_exists(self, actor_id: str) -> bool:
        async with self._sessions() as session:
            row = await session.scalar(
                select(actor_principal.c.actor_id).where(
                    actor_principal.c.enterprise_id == self._scope.enterprise_id,
                    actor_principal.c.actor_id == actor_id,
                    actor_principal.c.enabled.is_(True),
                )
            )
        return row is not None

    async def set_category(self, skill_id: str, category: SkillCategory) -> SkillRecord:
        from tap.modules.ai.adapters.mysql import skill

        async with self._sessions.begin() as session:
            result = await session.execute(
                update(skill)
                .where(*scope_predicates(skill, self._scope), skill.c.skill_id == skill_id)
                .values(category=category)
            )
            if result.rowcount != 1:
                raise LookupError(f"skill {skill_id!r} not found in this Project")
            rows = await self._skill_rows(session, skill_id=skill_id)
        return self._to_record(rows[0])

    async def transfer_owner(
        self, skill_id: str, new_owner: str, event: SkillMaintenanceEvent
    ) -> SkillRecord:
        from tap.modules.ai.adapters.mysql import skill

        async with self._sessions.begin() as session:
            result = await session.execute(
                update(skill)
                .where(*scope_predicates(skill, self._scope), skill.c.skill_id == skill_id)
                .values(owner_actor_id=new_owner, owner_assigned_at=_naive(event.occurred_at))
            )
            if result.rowcount != 1:
                raise LookupError(f"skill {skill_id!r} not found in this Project")
            await insert_skill_maintenance_event(session, self._scope, event)
            rows = await self._skill_rows(session, skill_id=skill_id)
        return self._to_record(rows[0])
