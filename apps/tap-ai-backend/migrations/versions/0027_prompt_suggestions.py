"""Persist the prompt suggestion refresh queue and suggestion cache."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0027_prompt_suggestions"
down_revision: str | None = "0026_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns():
    return (
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    )


def _scope_constraints(name: str):
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "prompt_suggestion_refresh",
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), primary_key=True),
        sa.Column("locale", sa.CHAR(2), primary_key=True),
        *_scope_columns(),
        sa.Column("due_at", DATETIME(fsp=6), nullable=True),
        sa.Column("last_reason", sa.String(32), nullable=True),
        sa.Column("last_refreshed_at", DATETIME(fsp=6), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_token", sa.CHAR(32), nullable=True),
        sa.Column("lease_expires_at", DATETIME(fsp=6), nullable=True),
        sa.Column("claimed_due_at", DATETIME(fsp=6), nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "enterprise_id",
            "project_id",
            "actor_id",
            "locale",
            name="uq_prompt_suggestion_refresh_identity",
        ),
        sa.Index("ix_prompt_suggestion_refresh_due", "project_id", "due_at"),
        *_scope_constraints("prompt_suggestion_refresh"),
    )
    op.create_table(
        "prompt_suggestion",
        sa.Column("suggestion_id", sa.CHAR(32), primary_key=True),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        *_scope_columns(),
        sa.Column("locale", sa.CHAR(2), nullable=False),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("question", sa.String(500), nullable=False),
        sa.Column("sources_json", JSON, nullable=False),
        sa.Column("generated_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("project_id", "suggestion_id", name="uq_prompt_suggestion_project_pk"),
        sa.Index(
            "ix_prompt_suggestion_actor_locale_position",
            "project_id",
            "actor_id",
            "locale",
            "position",
        ),
        *_scope_constraints("prompt_suggestion"),
    )


def downgrade() -> None:
    op.drop_table("prompt_suggestion")
    op.drop_table("prompt_suggestion_refresh")
