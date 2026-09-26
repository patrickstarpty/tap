"""Create TAP report receipts, mapped attempts and transactional outbox.

Revision ID: 0001_report_intake
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0001_report_intake"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_report_identity_claims",
        sa.Column("identity_digest", sa.String(length=64), nullable=False),
        sa.Column("canonical_receipt_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("identity_digest"),
    )
    op.create_table(
        "tap_report_receipts",
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("identity_digest", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=256), nullable=False),
        sa.Column("source_id", sa.String(length=256), nullable=False),
        sa.Column("external_run_id", sa.String(length=256), nullable=False),
        sa.Column("batch_id", sa.String(length=256), nullable=False),
        sa.Column("shard_id", sa.String(length=256), nullable=False),
        sa.Column("correction_no", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_object_ref", sa.String(length=512), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("completeness", sa.String(length=32), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("conflict_with_receipt_id", sa.String(length=36), nullable=True),
        sa.Column("failure_reason", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("receipt_id"),
        sa.UniqueConstraint(
            "identity_digest",
            "checksum",
            name="uq_tap_report_identity_content",
        ),
    )
    op.create_table(
        "tap_report_attempts",
        sa.Column("attempt_fact_id", sa.String(length=36), nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_test_identity", sa.String(length=512), nullable=False),
        sa.Column("source_locator", sa.String(length=256), nullable=False),
        sa.Column("stable_test_id", sa.String(length=256), nullable=True),
        sa.Column("data_row", sa.String(length=256), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("missing_reasons", sa.JSON(), nullable=False),
        sa.Column("first_attempt_eligible", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["tap_report_receipts.receipt_id"]),
        sa.PrimaryKeyConstraint("attempt_fact_id"),
        sa.UniqueConstraint(
            "receipt_id", "ordinal", name="uq_tap_report_attempt_ordinal"
        ),
    )
    op.create_table(
        "tap_report_outbox",
        sa.Column("outbox_id", sa.String(length=36), nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["tap_report_receipts.receipt_id"]),
        sa.PrimaryKeyConstraint("outbox_id"),
        sa.UniqueConstraint(
            "receipt_id", "sequence_no", name="uq_tap_report_outbox_sequence"
        ),
    )
    op.create_table(
        "tap_report_transitions",
        sa.Column("transition_id", sa.String(length=36), nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["tap_report_receipts.receipt_id"]),
        sa.PrimaryKeyConstraint("transition_id"),
    )
    op.create_index(
        "ix_tap_report_transition_receipt",
        "tap_report_transitions",
        ["receipt_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    for table in (
        "tap_report_identity_claims",
        "tap_report_receipts",
        "tap_report_attempts",
        "tap_report_outbox",
        "tap_report_transitions",
    ):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("report authority requires retention; downgrade refused")
    op.drop_table("tap_report_transitions")
    op.drop_table("tap_report_outbox")
    op.drop_table("tap_report_attempts")
    op.drop_table("tap_report_receipts")
    op.drop_table("tap_report_identity_claims")
