"""Add authoritative TAP Insights projection versions and batch watermarks.

Revision ID: 0003_insights_projection
Revises: 0002_manifest_fingerprint
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003_insights_projection"
down_revision: str | None = "0002_manifest_fingerprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_insights_projection_versions",
        sa.Column("projection_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("next_data_version", sa.BigInteger(), nullable=False),
        sa.Column("visible_data_version", sa.BigInteger(), nullable=False),
        sa.Column("verified_row_count", sa.BigInteger(), nullable=True),
        sa.Column("verified_checksum", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("projection_version"),
    )
    op.create_table(
        "tap_insights_projection_state",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("active_projection_version", sa.String(length=128), nullable=False),
        sa.Column("visible_data_version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_projection_version"],
            ["tap_insights_projection_versions.projection_version"],
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
    )
    op.create_table(
        "tap_insights_projection_batches",
        sa.Column("projection_batch_id", sa.String(length=64), nullable=False),
        sa.Column("projection_version", sa.String(length=128), nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("data_version", sa.BigInteger(), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["projection_version"],
            ["tap_insights_projection_versions.projection_version"],
        ),
        sa.ForeignKeyConstraint(["receipt_id"], ["tap_report_receipts.receipt_id"]),
        sa.PrimaryKeyConstraint("projection_batch_id"),
        sa.UniqueConstraint(
            "projection_version",
            "receipt_id",
            name="uq_tap_insights_projection_receipt",
        ),
        sa.UniqueConstraint(
            "projection_version",
            "data_version",
            name="uq_tap_insights_projection_data_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("tap_insights_projection_batches")
    op.drop_table("tap_insights_projection_state")
    op.drop_table("tap_insights_projection_versions")
