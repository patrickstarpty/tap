"""Count ingestion job deliveries so lease recovery and transient retries stay bounded."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_ingestion_delivery_count"
down_revision: str | None = "0027_prompt_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_ingestion_job",
        sa.Column("delivery_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("knowledge_ingestion_job", "delivery_count")
