"""`turn_skill_activation` table definition: one row per Skill a Turn used,
with how it was activated (`manual`, `pinned`, `auto`; spec 1.2/4). Manual
and pinned rows are written when the Turn input is frozen, auto rows when
`skill.activate` runs; migration `0038_skill_hub` backfills history."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import (
    Column,
    ColumnElement,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
    insert,
    literal,
    select,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.assets import SkillRevision
from tap.modules.ai.domain.skill_metadata import ActivationMode, frozen_activations
from tap.modules.chat.application.ports import TurnSkillUse
from tap.modules.chat.domain.conversations import TurnInput
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import augment_project_table, metadata

turn_skill_activation = Table(
    "turn_skill_activation",
    metadata,
    Column("activation_id", String(64), primary_key=True),
    Column("turn_id", String(64), nullable=False),
    Column("skill_id", String(64), nullable=False),
    Column("skill_revision_id", String(64), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("activated_at", DATETIME(fsp=6), nullable=False),
    Column("bytes", Integer, nullable=False),
    # `augment_project_table` prefixes `project_id`.
    UniqueConstraint("turn_id", "skill_id", name="uq_turn_skill_activation_turn_skill"),
)
# `skill_id -> skill.skill_id` is registered in `PROJECT_PARENT_LINKS`
# (platform/db/schema.py) so `augment_project_table` wires the FK itself.
augment_project_table(turn_skill_activation)
Index(
    "ix_turn_skill_activation_project_skill_activated",
    turn_skill_activation.c.project_id,
    turn_skill_activation.c.skill_id,
    turn_skill_activation.c.activated_at,
)


def activation_id_for(turn_id: str, skill_id: str) -> str:
    """Deterministic id: a Turn records each Skill at most once.

    Frozen by migration 0038 (backfilled rows use it): keep these semantics
    unchanged."""
    return "act-" + hashlib.sha256(f"{turn_id}/{skill_id}".encode()).hexdigest()[:24]


def _activation_id_sql(turn_id: str, skill_id: ColumnElement[str]) -> ColumnElement[str]:
    """`activation_id_for` computed by MySQL, for rows whose `skill_id` is
    only known inside the statement. The two must stay identical."""
    return func.concat("act-", func.left(func.sha2(func.concat(turn_id, "/", skill_id), 256), 24))


async def insert_frozen_activations(
    session: AsyncSession,
    scope: ProjectScopeContext,
    *,
    turn_id: str,
    value: TurnInput,
    activated_at: datetime,
) -> None:
    """Record a Turn's manual and pinned Skills, inside the transaction that
    inserts the Turn. Mode and bytes follow `frozen_activations` (manual when
    the revision was requested, otherwise pinned; bytes of the frozen
    instruction text); each row's `skill_id` is read from `skill_revision` in
    the same statement, and a revision outside this Project writes no row."""
    from tap.modules.ai.adapters.mysql import skill_revision

    snapshot = {
        "skill_revision_ids": value.skill_revision_ids,
        "skill_instruction_templates": value.skill_instruction_templates,
        "requested_skill_revision_ids": value.requested_skill_revision_ids,
    }
    scope_columns = scope_values(scope)
    columns = [
        "activation_id",
        "turn_id",
        "skill_id",
        "skill_revision_id",
        "mode",
        "activated_at",
        "bytes",
        *scope_columns,
    ]
    for activation in frozen_activations(snapshot, frozenset()):
        rows = select(
            _activation_id_sql(turn_id, skill_revision.c.skill_id),
            literal(turn_id),
            skill_revision.c.skill_id,
            literal(activation.skill_revision_id),
            literal(activation.mode),
            literal(activated_at, DATETIME(fsp=6)),
            literal(activation.bytes),
            *(literal(item) for item in scope_columns.values()),
        ).where(
            *scope_predicates(skill_revision, scope),
            skill_revision.c.revision_id == activation.skill_revision_id,
        )
        await session.execute(insert(turn_skill_activation).from_select(columns, rows))


class _HistoricalSkills(Protocol):
    async def get_historical_skill(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> SkillRevision: ...


class MysqlSkillActivationRuntime:
    """`SkillActivationRuntime` over the asset catalog and
    `turn_skill_activation`: `load` reads the revision whatever its status
    (the `tools` node rejects a disabled one), `record_auto` writes one
    `auto` row per Turn and Skill and ignores a repeat (a resumed node)."""

    def __init__(
        self,
        catalog: _HistoricalSkills,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self._catalog = catalog
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def load(self, revision_id: str) -> SkillRevision:
        return await self._catalog.get_historical_skill(self._scope, revision_id)

    async def record_auto(
        self,
        *,
        turn_id: str,
        skill_id: str,
        skill_revision_id: str,
        bytes_: int,
        activated_at: datetime,
    ) -> None:
        if activated_at.tzinfo is not None:
            # `DATETIME` columns hold naive UTC, as `chat_turn.created_at` does.
            activated_at = activated_at.astimezone(UTC).replace(tzinfo=None)
        async with self._sessions.begin() as session:
            await session.execute(
                insert(turn_skill_activation)
                .prefix_with("IGNORE")
                .values(
                    activation_id=activation_id_for(turn_id, skill_id),
                    turn_id=turn_id,
                    skill_id=skill_id,
                    skill_revision_id=skill_revision_id,
                    mode="auto",
                    activated_at=activated_at,
                    bytes=bytes_,
                    **scope_values(self._scope),
                )
            )


class MysqlTurnSkillActivations:
    """Reads the Skills each Turn used (frozen manual/pinned rows and `auto`
    rows alike), each labelled with the display name of the revision the
    Turn used -- immutable, and the same name `skillLabels` and the catalog
    the model activated by show."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def for_turns(self, turn_ids: Sequence[str]) -> dict[str, tuple[TurnSkillUse, ...]]:
        if not turn_ids:
            return {}
        from tap.modules.ai.adapters.mysql import skill_revision

        query = (
            select(
                turn_skill_activation.c.turn_id,
                turn_skill_activation.c.skill_id,
                turn_skill_activation.c.skill_revision_id,
                turn_skill_activation.c.mode,
                skill_revision.c.display_name,
            )
            .join(
                skill_revision,
                (skill_revision.c.enterprise_id == turn_skill_activation.c.enterprise_id)
                & (skill_revision.c.project_id == turn_skill_activation.c.project_id)
                & (skill_revision.c.revision_id == turn_skill_activation.c.skill_revision_id),
            )
            .where(
                *scope_predicates(turn_skill_activation, self._scope),
                turn_skill_activation.c.turn_id.in_(tuple(turn_ids)),
            )
            .order_by(
                turn_skill_activation.c.turn_id,
                turn_skill_activation.c.activated_at,
                turn_skill_activation.c.skill_id,
            )
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        uses: dict[str, list[TurnSkillUse]] = {}
        for turn_id, skill_id, revision_id, mode, label in rows:
            uses.setdefault(turn_id, []).append(
                TurnSkillUse(skill_id, revision_id, label, cast(ActivationMode, mode))
            )
        return {turn_id: tuple(items) for turn_id, items in uses.items()}
