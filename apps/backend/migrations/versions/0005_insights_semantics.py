"""Version projection payload recovery and immutable query interpretation.

Revision ID: 0005_insights_semantics
Revises: 0004_insights_queries
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005_insights_semantics"
down_revision: str | None = "0004_insights_queries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tap_insights_projection_batches",
        sa.Column("payload_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tap_insights_queries",
        sa.Column(
            "fact_semantics_version", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    # Original v1 payloads lack report_coverage; the winner-first release always
    # persisted it (even when empty). Do not change any historical result bytes.
    connection = op.get_bind()
    after = ""
    while True:
        rows = connection.execute(
            sa.text(
                "SELECT query_id, result_payload FROM tap_insights_queries WHERE query_id > :after ORDER BY query_id LIMIT 25"
            ),
            {"after": after},
        ).all()
        if not rows:
            break
        for query_id, payload in rows:
            value = json.loads(payload) if isinstance(payload, str) else payload
            connection.execute(
                sa.text(
                    "UPDATE tap_insights_queries SET fact_semantics_version = :version WHERE query_id = :query_id AND fact_semantics_version = 0"
                ),
                {
                    "query_id": query_id,
                    "version": 2 if "report_coverage" in value else 1,
                },
            )
        after = rows[-1][0]
    # Existing reservations remain unversioned until an exact v1/v2 checksum
    # match classifies them under the projection-version lock. No authority is
    # deleted, replaced, or assumed to have the current interpretation.


def downgrade() -> None:
    for table, authority in (
        ("tap_insights_queries", "query history"),
        ("tap_insights_projection_batches", "projection authority"),
    ):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(f"{authority} requires retention; downgrade refused")
    op.drop_column("tap_insights_queries", "fact_semantics_version")
    op.drop_column("tap_insights_projection_batches", "payload_version")
