"""Retain Source HTTP request identity independently from resource lifetimes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0010a_source_commands"
down_revision: str | None = "0010_knowledge_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_source_command",
        sa.Column("command_id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(128, collation="utf8mb4_0900_bin"), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(64)),
        sa.Column("document_id", sa.String(64)),
        sa.Column("revision_id", sa.String(128)),
        sa.Column("duplicate", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("result", JSON),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("completed_at", DATETIME(fsp=6)),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint(
            "project_id", "command_id", name="uq_knowledge_source_command_project_pk"
        ),
        sa.UniqueConstraint(
            "enterprise_id", "project_id", "idempotency_key", name="uq_source_command_key"
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_knowledge_source_command_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_knowledge_source_command_scope_actor",
        ),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM knowledge_source_command LIMIT 1")).first():
        raise RuntimeError("Source command facts require retention; downgrade refused")
    op.drop_table("knowledge_source_command")
