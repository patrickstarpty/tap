"""Add edge citation columns to `knowledge_citation_snapshot` so relation
citations (R labels) persist their graph provenance alongside chunk
citations; historical rows read back with `citation_kind` defaulted to
`"chunk"`."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_edge_citations"
down_revision: str | None = "0029_project_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_citation_snapshot",
        sa.Column("citation_kind", sa.String(8), nullable=True, server_default="chunk"),
    )
    op.add_column(
        "knowledge_citation_snapshot",
        sa.Column("graph_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "knowledge_citation_snapshot",
        sa.Column("edge_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "knowledge_citation_snapshot",
        sa.Column("subject_node_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "knowledge_citation_snapshot",
        sa.Column("object_node_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "knowledge_citation_snapshot",
        sa.Column("relation_type", sa.String(64), nullable=True),
    )
    op.add_column(
        "knowledge_citation_snapshot",
        sa.Column("relation_label", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_citation_snapshot", "relation_label")
    op.drop_column("knowledge_citation_snapshot", "relation_type")
    op.drop_column("knowledge_citation_snapshot", "object_node_id")
    op.drop_column("knowledge_citation_snapshot", "subject_node_id")
    op.drop_column("knowledge_citation_snapshot", "edge_id")
    op.drop_column("knowledge_citation_snapshot", "graph_version")
    op.drop_column("knowledge_citation_snapshot", "citation_kind")
