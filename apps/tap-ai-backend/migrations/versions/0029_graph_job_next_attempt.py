"""Delay graph extraction retries after transient failures."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0029_graph_job_next_attempt"
down_revision: str | None = "0028_ingestion_delivery_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_extraction_job",
        sa.Column("next_attempt_at", DATETIME(fsp=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("graph_extraction_job", "next_attempt_at")
