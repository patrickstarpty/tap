"""`agent_maintenance_event` table definition (Task 1: schema only).

Writes/reads land in later tasks; this module exists so the migration and the
SQLAlchemy metadata agree on the table shape from the start.
"""

from __future__ import annotations

from sqlalchemy import Column, Index, String, Table
from sqlalchemy.dialects.mysql import DATETIME

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
