"""Persist inspector cancellation, action ordering, and session evidence authority.

Revision ID: 0007_browser_session_ownership
Revises: 0006_browser_automation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_browser_session_ownership"
down_revision: str | None = "0006_browser_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tap_browser_sessions",
        sa.Column(
            "next_action_sequence", sa.Integer, nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "tap_browser_sessions",
        sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default="0"),
    )
    op.add_column("tap_browser_sessions", sa.Column("stop_receipt", sa.JSON))
    op.execute(
        "UPDATE tap_browser_sessions AS s SET next_action_sequence = "
        "COALESCE((SELECT MAX(a.sequence) + 1 FROM tap_browser_actions AS a "
        "WHERE a.session_id = s.id), 1)"
    )
    op.alter_column(
        "tap_browser_evidence",
        "execution_id",
        existing_type=sa.String(36),
        nullable=True,
    )
    op.add_column("tap_browser_evidence", sa.Column("session_id", sa.String(36)))


def downgrade() -> None:
    raise RuntimeError("browser authority requires retention; downgrade refused")
