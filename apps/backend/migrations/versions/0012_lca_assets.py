"""Persist immutable owner-scoped LCA upload asset references.

Revision ID: 0012_lca_assets
Revises: 0011_lca_releases
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0012_lca_assets"
down_revision: str | None = "0011_lca_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_lca_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(160), nullable=False),
        sa.Column("content_type", sa.String(80), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("object_ref", sa.String(128), nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM tap_lca_assets")).scalar():
        raise RuntimeError("LCA asset retention: downgrade refused while assets exist")
    op.drop_table("tap_lca_assets")
