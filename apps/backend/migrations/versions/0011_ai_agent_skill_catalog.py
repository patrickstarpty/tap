"""Persist immutable approved AI Agent and Skill catalog revisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0011_ai_agent_skill_catalog"
down_revision: str | None = "0010a_source_commands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _asset(name: str, identifier: str) -> None:
    op.create_table(
        name,
        sa.Column(identifier, sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("project_id", identifier, name=f"uq_{name}_project_pk"),
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
    _asset("ai_agent", "agent_id")
    _asset("skill", "skill_id")
    op.create_table(
        "ai_agent_revision",
        sa.Column("revision_id", sa.String(64), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("system_instruction_digest", sa.String(71), nullable=False),
        sa.Column("system_instruction", sa.String(8000), nullable=False),
        sa.Column("tool_allowlist", JSON, nullable=False),
        sa.Column("output_schema_digest", sa.String(71), nullable=False),
        sa.Column("output_schema_json", JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("adopted_from_revision_id", sa.String(64)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("project_id", "revision_id", name="uq_ai_agent_revision_project_pk"),
        sa.UniqueConstraint(
            "project_id", "agent_id", "revision_number", name="uq_ai_agent_revision_number"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["ai_agent.project_id", "ai_agent.agent_id"],
            name="fk_ai_agent_revision_agent",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "adopted_from_revision_id"],
            ["ai_agent_revision.project_id", "ai_agent_revision.revision_id"],
            name="fk_ai_agent_revision_adopted",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_ai_agent_revision_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_ai_agent_revision_scope_actor",
        ),
    )
    op.create_table(
        "skill_revision",
        sa.Column("revision_id", sa.String(64), primary_key=True),
        sa.Column("skill_id", sa.String(64), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("instruction_template_digest", sa.String(71), nullable=False),
        sa.Column("instruction_template", sa.String(8000), nullable=False),
        sa.Column("applicable_tasks", JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("adopted_from_revision_id", sa.String(64)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("project_id", "revision_id", name="uq_skill_revision_project_pk"),
        sa.UniqueConstraint(
            "project_id", "skill_id", "revision_number", name="uq_skill_revision_number"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "skill_id"],
            ["skill.project_id", "skill.skill_id"],
            name="fk_skill_revision_skill",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "adopted_from_revision_id"],
            ["skill_revision.project_id", "skill_revision.revision_id"],
            name="fk_skill_revision_adopted",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_skill_revision_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_skill_revision_scope_actor",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("ai_agent_revision", "skill_revision", "ai_agent", "skill"):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("Approved AI asset revisions require retention; downgrade refused")
    op.drop_table("skill_revision")
    op.drop_table("ai_agent_revision")
    op.drop_table("skill")
    op.drop_table("ai_agent")
