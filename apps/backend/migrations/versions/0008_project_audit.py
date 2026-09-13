"""Add the scoped Project audit ledger without rewriting historical data."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0008_project_audit"
down_revision: str | None = "0007_project_scope_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_audit",
        sa.Column("audit_id", sa.String(128, collation="utf8mb4_bin"), primary_key=True),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(32), nullable=False),
        sa.Column("identity_origin", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128, collation="utf8mb4_bin"), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("safe_metadata", JSON, nullable=False),
        sa.Column("occurred_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "enterprise_id", "project_id", "idempotency_key", name="uq_project_audit_replay"
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_project_audit_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_project_audit_scope_actor",
        ),
    )
    op.create_index(
        "ix_project_audit_occurred",
        "project_audit",
        ["enterprise_id", "project_id", "occurred_at", "audit_id"],
    )


def downgrade() -> None:
    op.drop_table("project_audit")
