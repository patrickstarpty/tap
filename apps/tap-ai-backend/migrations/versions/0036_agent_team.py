"""Agent team view schema: `ai_agent` gains `job_title` and a maintenance
owner (`owner_actor_id`/`owner_assigned_at`, backfilled from the asset's
original creator/`created_at` before being tightened to NOT NULL);
`answer_feedback` gains a resolution trio (`resolved_at`/`resolved_by`/
`resolution_note`); a new `agent_maintenance_event` table records agent
maintenance actions (spec 1.3/2.1/4). Also deletes the `langfuse_sync_cursor`
row named `trace` so the sync worker re-walks history and backfills
`agent:`/`outcome:` tags onto existing traces; the `feedback` cursor row is
untouched."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0036_agent_team"
down_revision: str | None = "0035_skills_agents_authoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    ]


def _scope_constraints(name: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    ]


def upgrade() -> None:
    op.add_column("ai_agent", sa.Column("job_title", sa.String(64), nullable=True))
    # Nullable first so the backfill below can populate every pre-existing
    # row before the column is tightened to NOT NULL.
    op.add_column("ai_agent", sa.Column("owner_actor_id", sa.String(128), nullable=True))
    op.add_column(
        "ai_agent", sa.Column("owner_assigned_at", DATETIME(fsp=6), nullable=True)
    )
    op.execute(
        sa.text("UPDATE ai_agent SET owner_actor_id = actor_id, owner_assigned_at = created_at")
    )
    op.alter_column("ai_agent", "owner_actor_id", existing_type=sa.String(128), nullable=False)
    op.alter_column(
        "ai_agent", "owner_assigned_at", existing_type=DATETIME(fsp=6), nullable=False
    )
    op.create_foreign_key(
        "fk_ai_agent_owner_actor",
        "ai_agent",
        "actor_principal",
        ["enterprise_id", "owner_actor_id"],
        ["enterprise_id", "actor_id"],
    )

    op.add_column("answer_feedback", sa.Column("resolved_at", DATETIME(fsp=6), nullable=True))
    op.add_column("answer_feedback", sa.Column("resolved_by", sa.String(128), nullable=True))
    op.add_column(
        "answer_feedback", sa.Column("resolution_note", sa.String(500), nullable=True)
    )

    op.create_table(
        "agent_maintenance_event",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("revision_id", sa.String(64), nullable=True),
        sa.Column("feedback_id", sa.String(36), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("occurred_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "event_id", name="uq_agent_maintenance_event_project_pk"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["ai_agent.project_id", "ai_agent.agent_id"],
            name="fk_agent_maintenance_event_agent",
        ),
        *_scope_constraints("agent_maintenance_event"),
    )
    op.create_index(
        "ix_agent_maintenance_event_project_agent_occurred",
        "agent_maintenance_event",
        ["project_id", "agent_id", "occurred_at"],
    )
    op.create_index(
        "ix_agent_maintenance_event_project_occurred",
        "agent_maintenance_event",
        ["project_id", "occurred_at"],
    )

    # Backfill: the sync worker resumes from `position` on its next run, so
    # deleting the cursor row (rather than rewinding it) makes it re-walk
    # every historical trace and attach the new `agent:`/`outcome:` tags.
    # The `feedback` cursor is unrelated and stays put.
    op.execute(sa.text("DELETE FROM langfuse_sync_cursor WHERE name = 'trace'"))


def downgrade() -> None:
    # Dropping the table removes its indexes and FKs together; dropping the
    # indexes first fails under MySQL because `fk_agent_maintenance_event_agent`
    # depends on `ix_agent_maintenance_event_project_agent_occurred`.
    op.drop_table("agent_maintenance_event")

    op.drop_column("answer_feedback", "resolution_note")
    op.drop_column("answer_feedback", "resolved_by")
    op.drop_column("answer_feedback", "resolved_at")

    op.drop_constraint("fk_ai_agent_owner_actor", "ai_agent", type_="foreignkey")
    op.drop_column("ai_agent", "owner_assigned_at")
    op.drop_column("ai_agent", "owner_actor_id")
    op.drop_column("ai_agent", "job_title")

    # The deleted `trace` cursor row is never recreated here: a downgrade
    # does not restore sync worker progress, only the schema it depended on.
