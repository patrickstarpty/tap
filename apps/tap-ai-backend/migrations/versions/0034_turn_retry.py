"""Add `chat_turn.next_attempt_at` and `chat_turn.last_failure` so a transiently
failed Turn can be deferred to a future retry time instead of being reclaimed
immediately, and so the worker can record why the last attempt failed."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0034_turn_retry"
down_revision: str | None = "0033_langfuse_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_turn",
        sa.Column("next_attempt_at", DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "chat_turn",
        sa.Column("last_failure", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_turn", "last_failure")
    op.drop_column("chat_turn", "next_attempt_at")
