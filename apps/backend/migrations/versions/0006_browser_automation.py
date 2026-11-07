"""Durable browser sessions, immutable bundles, execution intents and evidence.

Revision ID: 0006_browser_automation
Revises: 0005_insights_semantics
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_browser_automation"
down_revision: str | None = "0005_insights_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_browser_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request", sa.JSON, nullable=False),
        sa.Column("observation", sa.JSON),
        sa.Column("owner", sa.String(80)),
        sa.Column("lease_until", sa.String(40)),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "tap_browser_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request", sa.JSON, nullable=False),
        sa.Column("observation", sa.JSON),
        sa.Column("owner", sa.String(80)),
        sa.Column("lease_until", sa.String(40)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("session_id", "sequence"),
    )
    op.create_table(
        "tap_browser_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("script_ref", sa.String(128), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("input_ref", sa.String(128), nullable=False),
        sa.Column("allowed_origins", sa.JSON, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "tap_browser_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("bundle_id", sa.String(36), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request", sa.JSON, nullable=False),
        sa.Column("owner", sa.String(80)),
        sa.Column("lease_until", sa.String(40)),
        sa.Column("cancel_requested", sa.Boolean, nullable=False),
        sa.Column("started_at", sa.String(40)),
        sa.Column("finished_at", sa.String(40)),
        sa.Column("result", sa.JSON),
        sa.Column("steps", sa.JSON),
        sa.Column("evidence", sa.JSON),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "tap_browser_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("object_ref", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "tap_browser_idempotency",
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
    )
    for table in ("sessions", "actions", "executions"):
        op.create_index(
            f"ix_tap_browser_{table}_pending",
            f"tap_browser_{table}",
            ["project_id", "status", "created_at"],
        )
    op.create_index(
        "ix_tap_browser_evidence_project", "tap_browser_evidence", ["project_id", "id"]
    )


def downgrade() -> None:
    for table in (
        "tap_browser_evidence",
        "tap_browser_executions",
        "tap_browser_bundles",
        "tap_browser_actions",
        "tap_browser_sessions",
        "tap_browser_idempotency",
    ):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(
                "browser authority requires retention; downgrade refused"
            )
    op.drop_index("ix_tap_browser_evidence_project", "tap_browser_evidence")
    for table in ("sessions", "actions", "executions"):
        op.drop_index(f"ix_tap_browser_{table}_pending", f"tap_browser_{table}")
    for table in (
        "tap_browser_idempotency",
        "tap_browser_evidence",
        "tap_browser_executions",
        "tap_browser_bundles",
        "tap_browser_actions",
        "tap_browser_sessions",
    ):
        op.drop_table(table)
