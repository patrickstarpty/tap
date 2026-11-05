"""AI-owned maintenance tables and inserts within caller transactions."""

from __future__ import annotations

from sqlalchemy import Column, Index, String, Table
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.ext.asyncio import AsyncSession

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.maintenance import MaintenanceEvent, SkillMaintenanceEvent
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import augment_project_table, metadata

agent_maintenance_event = Table(
    "agent_maintenance_event",
    metadata,
    Column("event_id", String(64), primary_key=True),
    Column("agent_id", String(64), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("revision_id", String(64), nullable=True),
    Column("feedback_id", String(36), nullable=True),
    Column("note", String(500), nullable=True),
    Column("occurred_at", DATETIME(fsp=6), nullable=False),
)
# `agent_id -> ai_agent.agent_id` is registered in `PROJECT_PARENT_LINKS`
# (platform/db/schema.py) so `augment_project_table` wires the FK itself.
augment_project_table(agent_maintenance_event)
Index(
    "ix_agent_maintenance_event_project_agent_occurred",
    agent_maintenance_event.c.project_id,
    agent_maintenance_event.c.agent_id,
    agent_maintenance_event.c.occurred_at,
)
Index(
    "ix_agent_maintenance_event_project_occurred",
    agent_maintenance_event.c.project_id,
    agent_maintenance_event.c.occurred_at,
)


async def insert_maintenance_event(
    session: AsyncSession, scope: ProjectScopeContext, event: MaintenanceEvent
) -> None:
    """The one `agent_maintenance_event` insert, shared by every writer that
    records a maintenance event in the same transaction as its state change
    (revise, enable/disable, owner transfer, feedback resolution).

    An `IntegrityError` raised here (duplicate `event_id`, or the Agent FK)
    is only meaningful as a *transaction* failure: callers that map it to
    `RevisionConflict` do so because the whole transaction -- including the
    state change -- rolls back, never because the event alone conflicted."""
    await session.execute(
        agent_maintenance_event.insert().values(
            event_id=event.event_id,
            agent_id=event.agent_id,
            kind=event.kind,
            revision_id=event.revision_id,
            feedback_id=event.feedback_id,
            note=event.note,
            occurred_at=event.occurred_at,
            **scope_values(scope),
        )
    )


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
