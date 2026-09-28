"""Record owner soft deletion of Conversations while retaining Turn evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0023_conversation_history"
down_revision: str | None = "0022_test_design_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("deleted_at", DATETIME(fsp=6), nullable=True))
    op.add_column("conversation", sa.Column("deleted_by", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation", "deleted_by")
    op.drop_column("conversation", "deleted_at")
