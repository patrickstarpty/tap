"""Persist graph fragment batches and node aliases / edge relation labels."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0028_graph_fragment_batch"
down_revision: str | None = "0027_prompt_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns():
    return (
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    )


def _scope_constraints(name: str):
    return (
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    )


def upgrade() -> None:
    op.create_table(
        "graph_fragment_batch",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("batch_index", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("chunk_ids", JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("draft_json", JSON, nullable=True),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id"],
            ["graph_snapshot.project_id", "graph_snapshot.snapshot_id"],
            name="fk_graph_fragment_batch_snapshot",
        ),
        *_scope_constraints("graph_fragment_batch"),
    )
    op.add_column("graph_node", sa.Column("aliases", JSON, nullable=True))
    op.add_column(
        "graph_edge",
        sa.Column("relation_label", sa.String(64), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("graph_edge", "relation_label")
    op.drop_column("graph_node", "aliases")
    op.drop_table("graph_fragment_batch")
