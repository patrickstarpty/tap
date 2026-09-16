"""Create durable Knowledge Chat Turn, event, snapshot, and Outbox tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_turn_outbox"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_turn",
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("client_request_id", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("turn_id"),
        sa.UniqueConstraint(
            "chat_id",
            "client_request_id",
            name="uq_chat_turn_client_request",
        ),
    )
    op.create_table(
        "chat_event",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", mysql.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turn.turn_id"],
            name="fk_chat_event_turn",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("turn_id", "sequence", name="uq_chat_event_turn_sequence"),
    )
    op.create_table(
        "turn_snapshot",
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("snapshot", mysql.JSON(), nullable=False),
        sa.Column("snapshot_version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turn.turn_id"],
            name="fk_turn_snapshot_turn",
        ),
        sa.PrimaryKeyConstraint("turn_id"),
    )
    op.create_table(
        "outbox",
        sa.Column("outbox_id", sa.String(length=128), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=True),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_until", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("outbox_id"),
        sa.UniqueConstraint("command_id"),
    )
    op.create_index(
        "ix_outbox_claim",
        "outbox",
        ["status", "next_attempt_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_expired_lease",
        "outbox",
        ["status", "lease_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_expired_lease", table_name="outbox")
    op.drop_index("ix_outbox_claim", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("turn_snapshot")
    op.drop_table("chat_event")
    op.drop_table("chat_turn")
