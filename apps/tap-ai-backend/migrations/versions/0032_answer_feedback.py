"""Persist per-Turn answer feedback (up/down rating with an optional comment) as
business data; a later Langfuse sync worker reads this table and pushes it as a
score, independent of this migration."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0032_answer_feedback"
down_revision: str | None = "0031_cost_usd_precision"
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
    op.create_table(
        "answer_feedback",
        sa.Column("feedback_id", sa.CHAR(36), primary_key=True),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.CHAR(32), nullable=True),
        sa.Column("rating", sa.String(8), nullable=True),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "feedback_id", name="uq_answer_feedback_project_pk"),
        sa.UniqueConstraint(
            "project_id", "turn_id", "actor_id", name="uq_answer_feedback_turn_actor"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "turn_id"],
            ["chat_turn.project_id", "chat_turn.turn_id"],
            name="fk_answer_feedback_turn",
        ),
        *_scope_constraints("answer_feedback"),
    )
    op.create_index(
        "ix_answer_feedback_project_updated",
        "answer_feedback",
        ["project_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_answer_feedback_project_updated", table_name="answer_feedback")
    op.drop_table("answer_feedback")
