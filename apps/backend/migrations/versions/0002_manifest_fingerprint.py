"""Bind exact report retries to the normalized manifest.

Revision ID: 0002_manifest_fingerprint
Revises: 0001_report_intake
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op


revision: str = "0002_manifest_fingerprint"
down_revision: str | None = "0001_report_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tap_report_receipts",
        sa.Column("manifest_digest", sa.String(length=64), nullable=True),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT receipt_id, manifest FROM tap_report_receipts")
    ).mappings()
    updates: list[dict[str, Any]] = []
    for row in rows:
        manifest = row["manifest"]
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        serialized = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode()
        updates.append(
            {
                "receipt_id": row["receipt_id"],
                "manifest_digest": hashlib.sha256(serialized).hexdigest(),
            }
        )
    if updates:
        connection.execute(
            sa.text(
                "UPDATE tap_report_receipts "
                "SET manifest_digest = :manifest_digest "
                "WHERE receipt_id = :receipt_id"
            ),
            updates,
        )
    op.alter_column(
        "tap_report_receipts",
        "manifest_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_constraint(
        "uq_tap_report_identity_content",
        "tap_report_receipts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_tap_report_identity_content",
        "tap_report_receipts",
        ["identity_digest", "checksum", "manifest_digest"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_tap_report_identity_content",
        "tap_report_receipts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_tap_report_identity_content",
        "tap_report_receipts",
        ["identity_digest", "checksum"],
    )
    op.drop_column("tap_report_receipts", "manifest_digest")
