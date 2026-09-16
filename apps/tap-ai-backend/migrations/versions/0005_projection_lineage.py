"""Add durable pre-create ownership and generation lineage receipts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005_projection_lineage"
down_revision: str | None = "0004_projection_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_projection_lineage",
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("physical_collection", sa.String(length=255), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("predecessor_collection", sa.String(length=255), nullable=False),
        sa.Column("predecessor_generation", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("alias_name", "physical_collection"),
        sa.UniqueConstraint("alias_name", "operation_id", name="uq_projection_lineage_operation"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_projection_lineage")
