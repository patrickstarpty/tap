"""Persist bounded original-excerpt proofs for parse inventory items."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_original_excerpt_alignment"
down_revision: str | None = "0020_knowledge_review_read_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("original_source_digest", sa.String(71)),
        sa.Column("original_start_byte", sa.Integer()),
        sa.Column("original_end_byte", sa.Integer()),
        sa.Column("original_excerpt_digest", sa.String(71)),
        sa.Column("original_alignment_reason", sa.String(128)),
        sa.Column("original_alignment_binding_digest", sa.String(71)),
    ):
        op.add_column("knowledge_parse_inventory", column)


def downgrade() -> None:
    retained = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM knowledge_parse_inventory WHERE "
                "original_source_digest IS NOT NULL OR original_start_byte IS NOT NULL OR "
                "original_end_byte IS NOT NULL OR original_excerpt_digest IS NOT NULL OR "
                "original_alignment_reason IS NOT NULL OR "
                "original_alignment_binding_digest IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if retained:
        raise RuntimeError("original excerpt alignment evidence requires retention")
    for name in (
        "original_alignment_binding_digest",
        "original_alignment_reason",
        "original_excerpt_digest",
        "original_end_byte",
        "original_start_byte",
        "original_source_digest",
    ):
        op.drop_column("knowledge_parse_inventory", name)
