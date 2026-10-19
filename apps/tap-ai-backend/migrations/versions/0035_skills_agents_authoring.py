"""Extend the approved AI catalog to carry authored/imported Skill and Agent
revisions alongside the existing built-in ones: `skill_revision` and
`ai_agent_revision` gain `description`, `source_kind`, and authoring text
columns (widening `ai_agent_revision.system_instruction` to `MEDIUMTEXT`); a
new `skill_revision_file` table records each Skill revision's packaged files;
a new `skill_import` table holds a staged import preview pending commit."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON, MEDIUMTEXT

revision: str = "0035_skills_agents_authoring"
down_revision: str | None = "0034_turn_retry"
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
    op.add_column("ai_agent_revision", sa.Column("description", sa.String(1024), nullable=True))
    op.add_column(
        "ai_agent_revision",
        sa.Column("source_kind", sa.String(16), nullable=False, server_default="built_in"),
    )
    op.alter_column(
        "ai_agent_revision",
        "system_instruction",
        existing_type=sa.String(8000),
        type_=MEDIUMTEXT(),
        existing_nullable=True,
    )
    op.add_column("ai_agent_revision", sa.Column("skill_revision_ids", JSON, nullable=True))
    op.add_column(
        "ai_agent_revision", sa.Column("default_model_alias", sa.String(128), nullable=True)
    )
    op.alter_column("ai_agent_revision", "source_kind", server_default=None)

    op.add_column("skill_revision", sa.Column("description", sa.String(1024), nullable=True))
    op.add_column(
        "skill_revision",
        sa.Column("source_kind", sa.String(16), nullable=False, server_default="built_in"),
    )
    op.add_column("skill_revision", sa.Column("instruction_body", MEDIUMTEXT(), nullable=True))
    op.add_column(
        "skill_revision", sa.Column("attached_reference_text", MEDIUMTEXT(), nullable=True)
    )
    op.add_column("skill_revision", sa.Column("frontmatter_json", JSON, nullable=True))
    op.add_column("skill_revision", sa.Column("package_digest", sa.String(71), nullable=True))
    op.alter_column("skill_revision", "source_kind", server_default=None)

    op.create_table(
        "skill_revision_file",
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("revision_id", sa.String(64), primary_key=True),
        sa.Column("relative_path", sa.String(512), primary_key=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("attached", sa.Boolean(), nullable=False),
        sa.Column("object_locator", sa.String(1024), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["skill_revision.project_id", "skill_revision.revision_id"],
            name="fk_skill_revision_file_revision",
        ),
        *_scope_constraints("skill_revision_file"),
    )

    op.create_table(
        "skill_import",
        sa.Column("import_id", sa.String(64), primary_key=True),
        sa.Column("preview_json", JSON, nullable=False),
        sa.Column("staging_locators", JSON, nullable=False),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=False),
        sa.Column("committed_result_json", JSON, nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "import_id", name="uq_skill_import_project_pk"),
        *_scope_constraints("skill_import"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("skill_revision_file", "skill_import"):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("Approved AI asset revisions require retention; downgrade refused")
    for table, column in (
        ("ai_agent_revision", "description"),
        ("ai_agent_revision", "source_kind"),
        ("ai_agent_revision", "skill_revision_ids"),
        ("ai_agent_revision", "default_model_alias"),
        ("skill_revision", "description"),
        ("skill_revision", "source_kind"),
        ("skill_revision", "instruction_body"),
        ("skill_revision", "attached_reference_text"),
        ("skill_revision", "frontmatter_json"),
        ("skill_revision", "package_digest"),
    ):
        has_data = (
            bind.execute(
                sa.text(f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL LIMIT 1")
            ).first()
            is not None
        )
        if has_data:
            raise RuntimeError("Approved AI asset revisions require retention; downgrade refused")

    op.drop_table("skill_import")
    op.drop_table("skill_revision_file")

    op.drop_column("skill_revision", "package_digest")
    op.drop_column("skill_revision", "frontmatter_json")
    op.drop_column("skill_revision", "attached_reference_text")
    op.drop_column("skill_revision", "instruction_body")
    op.drop_column("skill_revision", "source_kind")
    op.drop_column("skill_revision", "description")

    op.drop_column("ai_agent_revision", "default_model_alias")
    op.drop_column("ai_agent_revision", "skill_revision_ids")
    op.alter_column(
        "ai_agent_revision",
        "system_instruction",
        existing_type=MEDIUMTEXT(),
        type_=sa.String(8000),
        existing_nullable=True,
    )
    op.drop_column("ai_agent_revision", "source_kind")
    op.drop_column("ai_agent_revision", "description")
