"""`skill_maintenance_event` table definition: Skill maintenance actions
(`revised`, `enabled`, `disabled`, `owner_transferred`, `synced`; spec 4),
written in the same transaction as the Skill write they record."""

from __future__ import annotations

from sqlalchemy import Column, Index, String, Table
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.ext.asyncio import AsyncSession

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.skill_hub.domain.models import SkillMaintenanceEvent
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import augment_project_table, metadata

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
