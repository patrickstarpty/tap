"""Add `trace_span.recorded_at` (backfilled from `started_at`) plus the Langfuse
sync worker's own `langfuse_sync_cursor` and `langfuse_sync_failure` tables. Both
new tables are system-wide (no Project scope columns): the worker reads across
every Project. Also adds a bare `answer_feedback.updated_at` index for that
same cross-Project scan."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0033_langfuse_sync"
down_revision: str | None = "0032_answer_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trace_span",
        sa.Column(
            "recorded_at",
            DATETIME(fsp=6),
            nullable=False,
            # `UTC_TIMESTAMP(6)`, not `CURRENT_TIMESTAMP(6)`: the latter follows the
            # MySQL server's session time zone, which would desync this naive-UTC
            # column from `started_at` (always UTC) and the worker's UTC cutoff
            # whenever the server's time zone isn't UTC. The parens around the
            # function default are required by MySQL 8.0.13+/8.4 for anything
            # other than bare `CURRENT_TIMESTAMP`.
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
        ),
    )
    # `ADD COLUMN ... DEFAULT (UTC_TIMESTAMP(6))` backfills existing rows with
    # the migration's run time, not their original span time; overwrite with
    # `started_at` so the sync worker's cursor reflects write order it can trust.
    op.execute(sa.text("UPDATE trace_span SET recorded_at = started_at"))
    op.create_index("ix_trace_span_recorded_at", "trace_span", ["recorded_at"])

    op.create_table(
        "langfuse_sync_cursor",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("position", DATETIME(fsp=6), nullable=False),
    )

    op.create_table(
        "langfuse_sync_failure",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("subject_id", sa.String(64), primary_key=True),
        sa.Column("failure_count", sa.Integer, nullable=False),
        sa.Column("last_error", sa.String(1000), nullable=True),
        sa.Column("last_failed_at", DATETIME(fsp=6), nullable=False),
    )

    # Bare, not Project-prefixed: the sync worker scans `updated_at` across every
    # Project, unlike the existing Project-scoped `ix_answer_feedback_project_updated`.
    op.create_index("ix_answer_feedback_updated", "answer_feedback", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_answer_feedback_updated", table_name="answer_feedback")
    op.drop_table("langfuse_sync_failure")
    op.drop_table("langfuse_sync_cursor")
    op.drop_index("ix_trace_span_recorded_at", table_name="trace_span")
    op.drop_column("trace_span", "recorded_at")
