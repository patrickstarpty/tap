"""Add durable mutable-projection generation, fence, and cleanup authority."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0004_projection_authority"
down_revision: str | None = "0003_tapper_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_projection_state",
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("physical_collection", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("alias_name"),
    )
    op.create_table(
        "knowledge_projection_fence",
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("alias_name", "revision_id"),
    )
    op.create_table(
        "knowledge_projection_cleanup",
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("physical_collection", sa.String(length=255), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("alias_name", "physical_collection"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_projection_cleanup")
    op.drop_table("knowledge_projection_fence")
    op.drop_table("knowledge_projection_state")
