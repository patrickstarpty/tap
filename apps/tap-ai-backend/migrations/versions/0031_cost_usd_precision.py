"""Widen `model_call.cost_usd` from DECIMAL(18,8) to DECIMAL(18,10) so a
per-call USD cost below 1e-8 (plausible for a single cheap embedding token)
no longer rounds down to a silent 0.00000000 on write; historical rows are
unaffected since the new scale is a strict superset of the old one."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_cost_usd_precision"
down_revision: str | None = "0030_edge_citations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "model_call",
        "cost_usd",
        existing_type=sa.DECIMAL(18, 8),
        type_=sa.DECIMAL(18, 10),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "model_call",
        "cost_usd",
        existing_type=sa.DECIMAL(18, 10),
        type_=sa.DECIMAL(18, 8),
        existing_nullable=True,
    )
