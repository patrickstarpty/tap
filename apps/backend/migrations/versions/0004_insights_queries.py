"""Persist immutable TAP Insights query scope and results.

Revision ID: 0004_insights_queries
Revises: 0003_insights_projection
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


revision: str = "0004_insights_queries"
down_revision: str | None = "0003_insights_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_insights_queries",
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=256), nullable=False),
        sa.Column("metric_version", sa.String(length=128), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("from_date", sa.String(length=10), nullable=False),
        sa.Column("to_date", sa.String(length=10), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("as_of", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("projection_version", sa.String(length=128), nullable=False),
        sa.Column("visible_data_version", sa.BigInteger(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("query_id"),
    )
    op.create_index(
        "ix_tap_insights_query_project_created",
        "tap_insights_queries",
        ["project_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tap_insights_query_project_created",
        table_name="tap_insights_queries",
    )
    op.drop_table("tap_insights_queries")
