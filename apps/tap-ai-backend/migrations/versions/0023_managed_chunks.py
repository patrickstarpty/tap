"""Persist editable knowledge chunks and immutable management generations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_managed_chunks"
down_revision: str | None = "0022_test_design_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def scope_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    )


def constraints(name: str) -> tuple[sa.ForeignKeyConstraint, ...]:
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
        "knowledge_managed_document",
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("original_revision_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("chunks_json", sa.JSON(), nullable=False),
        sa.Column("index_status", sa.String(24), nullable=False),
        sa.Column("index_error", sa.String(240)),
        *scope_columns(),
        *constraints("knowledge_managed_document"),
    )
    op.create_table(
        "knowledge_managed_generation",
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        *scope_columns(),
        *constraints("knowledge_managed_generation"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_managed_generation")
    op.drop_table("knowledge_managed_document")
