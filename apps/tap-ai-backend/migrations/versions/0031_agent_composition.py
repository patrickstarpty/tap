"""Compose authored Agents from a model, a description and ordered Skill revisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision: str = "0031_agent_composition"
down_revision: str | None = "0030_skill_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_agent_revision", sa.Column("model_alias", sa.String(128), nullable=True))
    op.add_column("ai_agent_revision", sa.Column("description", sa.String(1024), nullable=True))
    op.add_column(
        "ai_agent_revision",
        sa.Column("origin", sa.String(16), nullable=False, server_default="seed"),
    )
    # Authored system instructions may reach 16000 characters.
    op.alter_column(
        "ai_agent_revision",
        "system_instruction",
        existing_type=sa.String(8000),
        type_=MEDIUMTEXT,
        existing_nullable=True,
    )
    op.create_table(
        "ai_agent_revision_skill",
        sa.Column("revision_id", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("skill_revision_id", sa.String(64), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("revision_id", "position"),
        sa.UniqueConstraint(
            "project_id",
            "revision_id",
            "skill_revision_id",
            name="uq_ai_agent_revision_skill_once",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["ai_agent_revision.project_id", "ai_agent_revision.revision_id"],
            name="fk_ai_agent_revision_skill_agent",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "skill_revision_id"],
            ["skill_revision.project_id", "skill_revision.revision_id"],
            name="fk_ai_agent_revision_skill_skill",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_ai_agent_revision_skill_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_ai_agent_revision_skill_scope_actor",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_agent_revision_skill")
    op.alter_column(
        "ai_agent_revision",
        "system_instruction",
        existing_type=MEDIUMTEXT,
        type_=sa.String(8000),
        existing_nullable=True,
    )
    op.drop_column("ai_agent_revision", "origin")
    op.drop_column("ai_agent_revision", "description")
    op.drop_column("ai_agent_revision", "model_alias")
