"""Persist owner-scoped LCA drafts, immutable snapshots and debug-run intents.

Revision ID: 0010_lca_debug_flows
Revises: 0009_browser_control
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMBLOB

revision: str = "0010_lca_debug_flows"
down_revision: str | None = "0009_browser_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    canonical_bytes = sa.LargeBinary(length=16777215).with_variant(
        MEDIUMBLOB(), "mysql"
    )
    op.create_table(
        "tap_lca_debug_flows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("canonical_bytes", canonical_bytes, nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "tap_lca_debug_drafts",
        sa.Column("flow_id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("canonical_bytes", canonical_bytes, nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "tap_lca_debug_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("canonical_bytes", canonical_bytes, nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("generator_version", sa.String(80), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("identity_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("flow_id", "identity_digest"),
    )
    op.create_table(
        "tap_lca_debug_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("bundle_id", sa.String(36), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(20), nullable=False),
        sa.Column("execution_key", sa.String(128), nullable=False, unique=True),
        sa.Column("execution_request", sa.JSON(), nullable=False),
        sa.Column("execution_id", sa.String(36)),
    )
    op.create_table(
        "tap_lca_debug_idempotency",
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
    )
    op.create_table(
        "tap_lca_debug_recordings",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    tables = (
        "tap_lca_debug_recordings",
        "tap_lca_debug_idempotency",
        "tap_lca_debug_runs",
        "tap_lca_debug_snapshots",
        "tap_lca_debug_drafts",
        "tap_lca_debug_flows",
    )
    # Check every table before the first DROP: MySQL DDL cannot be rolled back.
    for table in tables:
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(
                "LCA debug authority requires retention; downgrade refused"
            )
    for table in tables:
        op.drop_table(table)
