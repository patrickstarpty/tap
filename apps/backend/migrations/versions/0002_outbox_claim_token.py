"""Add per-attempt Outbox claim ownership."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0002_outbox_claim_token"
down_revision: str | None = "0001_turn_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("claim_token", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox", "claim_token")
