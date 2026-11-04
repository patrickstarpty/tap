"""Persist immutable relation citations and their original-text claim links."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import JSON

revision: str = "0034_relation_citations"
down_revision: str | None = "0033_mcp_skipped_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_relation_citation",
        sa.Column("citation_id", sa.String(64), primary_key=True),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("edge_id", sa.String(128), nullable=False),
        sa.Column("source_node_id", sa.String(128), nullable=False),
        sa.Column("target_node_id", sa.String(128), nullable=False),
        sa.Column("source_label", sa.String(512), nullable=False),
        sa.Column("target_label", sa.String(512), nullable=False),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("claim_index", sa.Integer, nullable=False),
        sa.Column("path_edge_ids", JSON, nullable=False),
        sa.Column("original_citation_ids", JSON, nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint(
            "project_id", "citation_id", name="uq_chat_relation_citation_project_pk"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "turn_id"],
            ["chat_turn.project_id", "chat_turn.turn_id"],
            name="fk_chat_relation_citation_turn",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_chat_relation_citation_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_chat_relation_citation_scope_actor",
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_relation_citation")
