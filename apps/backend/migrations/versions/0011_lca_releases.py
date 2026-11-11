"""Persist immutable, owner-scoped verified LCA releases.

Revision ID: 0011_lca_releases
Revises: 0010_lca_debug_flows
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0011_lca_releases"
down_revision: str | None = "0010_lca_debug_flows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_lca_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("verified_run_id", sa.String(36), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("flow_id", "version"),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM tap_lca_releases")).scalar():
        raise RuntimeError(
            "LCA release retention: downgrade refused while releases exist"
        )
    op.drop_table("tap_lca_releases")
