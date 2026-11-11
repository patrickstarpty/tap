"""Durable controller epochs and dispatch provenance; legacy stays agent-only."""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0009_browser_control"
down_revision: str | None = "0008_browser_network_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("control_version", sa.String(32)),
        sa.Column(
            "controller_kind", sa.String(16), nullable=False, server_default="agent"
        ),
        sa.Column("controller_id", sa.String(128)),
        sa.Column("agent_controller_id", sa.String(128)),
        sa.Column("control_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "control_state",
            sa.String(32),
            nullable=False,
            server_default="agent_active",
        ),
        sa.Column("pending_controller_id", sa.String(128)),
        sa.Column("deadline_at", sa.String(40)),
        sa.Column("observer_until", sa.String(40)),
        sa.Column(
            "recording_stopped", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column("recording_watermark", sa.Integer()),
        sa.Column("recording_start_sequence", sa.Integer()),
        sa.Column("recording_epoch", sa.Integer()),
        sa.Column(
            "handoff_observation_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    ):
        op.add_column("tap_browser_sessions", column)
    for column in (
        sa.Column("actor_kind", sa.String(16), nullable=False, server_default="agent"),
        sa.Column("controller_id", sa.String(128)),
        sa.Column("control_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("command_id", sa.String(128)),
        sa.Column("actor_expires_at", sa.String(40)),
    ):
        op.add_column("tap_browser_actions", column)
    op.create_table(
        "tap_browser_model_reservations",
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("controller_id", sa.String(128), nullable=False),
        sa.Column("control_epoch", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_table(
        "tap_browser_control_requests",
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tap_browser_model_reservations")
    op.drop_table("tap_browser_control_requests")
    for name in (
        "actor_expires_at",
        "command_id",
        "control_epoch",
        "controller_id",
        "actor_kind",
    ):
        op.drop_column("tap_browser_actions", name)
    for name in (
        "handoff_observation_sequence",
        "observer_until",
        "recording_stopped",
        "recording_watermark",
        "recording_start_sequence",
        "recording_epoch",
        "deadline_at",
        "pending_controller_id",
        "control_state",
        "control_epoch",
        "agent_controller_id",
        "controller_id",
        "controller_kind",
        "control_version",
    ):
        op.drop_column("tap_browser_sessions", name)
