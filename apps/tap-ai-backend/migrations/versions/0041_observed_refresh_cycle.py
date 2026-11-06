"""Persist one logical suggestion refresh identity across leases and retries."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_observed_refresh_cycle"
down_revision: str | None = "0040_native_workflow_bridge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prompt_suggestion_refresh",
        sa.Column("refresh_cycle_id", sa.CHAR(32), nullable=True),
    )


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM prompt_suggestion_refresh "
                "WHERE refresh_cycle_id IS NOT NULL LIMIT 1")
    ).first() is not None:
        raise RuntimeError("cannot downgrade an active suggestion refresh cycle")
    op.drop_column("prompt_suggestion_refresh", "refresh_cycle_id")
