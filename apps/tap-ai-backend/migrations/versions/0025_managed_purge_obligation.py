"""Persist the durable purge obligation for superseded managed revisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_managed_purge_obligation"
down_revision: str | None = "0024_conversation_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_managed_document",
        sa.Column("purge_revision_id", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    retained = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM knowledge_managed_document WHERE "
                "purge_revision_id IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if retained:
        raise RuntimeError(
            "refusing to drop purge_revision_id: unresolved purge obligations would be lost"
        )
    op.drop_column("knowledge_managed_document", "purge_revision_id")
