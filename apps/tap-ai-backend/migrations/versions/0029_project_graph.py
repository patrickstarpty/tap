"""Persist the Project-scoped merged knowledge graph: versions, nodes, edges,
provenance, aliases, communities and the merge audit log."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0029_project_graph"
down_revision: str | None = "0028_graph_fragment_batch"
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
        "graph_project_version",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("fragment_digest", sa.String(71), nullable=False),
        sa.Column("node_count", sa.Integer, nullable=False),
        sa.Column("edge_count", sa.Integer, nullable=False),
        sa.Column("merged_at", DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_graph_project_version_project_pk"),
        *_scope_constraints("graph_project_version"),
    )
    op.create_table(
        "graph_project_node",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("label", sa.String(512), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("canonical_key", sa.String(512), nullable=False),
        sa.Column("degree", sa.Integer, nullable=False),
        sa.Column("community_id", sa.String(64), nullable=True),
        sa.Column("aliases", JSON, nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "version"],
            ["graph_project_version.project_id", "graph_project_version.version"],
            name="fk_graph_project_node_version",
        ),
        sa.UniqueConstraint(
            "project_id", "version", "node_id", name="uq_graph_project_node_project_pk"
        ),
        sa.Index("ix_graph_project_node_key", "project_id", "version", "canonical_key"),
        sa.Index("ix_graph_project_node_community", "project_id", "version", "community_id"),
        *_scope_constraints("graph_project_node"),
    )
    op.create_table(
        "graph_project_edge",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("edge_id", sa.String(128), primary_key=True),
        sa.Column("source_node_id", sa.String(128), nullable=False),
        sa.Column("target_node_id", sa.String(128), nullable=False),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("relation_label", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "version"],
            ["graph_project_version.project_id", "graph_project_version.version"],
            name="fk_graph_project_edge_version",
        ),
        sa.UniqueConstraint(
            "project_id", "version", "edge_id", name="uq_graph_project_edge_project_pk"
        ),
        sa.Index("ix_graph_project_edge_source", "project_id", "version", "source_node_id"),
        sa.Index("ix_graph_project_edge_target", "project_id", "version", "target_node_id"),
        *_scope_constraints("graph_project_edge"),
    )
    op.create_table(
        "graph_project_node_source",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("source_revision_id", sa.String(128), primary_key=True),
        sa.Column("chunk_id", sa.String(128), primary_key=True),
        sa.Column("document_revision_id", sa.String(128), nullable=False),
        sa.Column("anchor_json", JSON, nullable=False),
        sa.Column("fragment_snapshot_id", sa.String(64), nullable=False),
        sa.Column("fragment_node_id", sa.String(128), nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "version", "node_id"],
            [
                "graph_project_node.project_id",
                "graph_project_node.version",
                "graph_project_node.node_id",
            ],
            name="fk_graph_project_node_source_node",
        ),
        sa.UniqueConstraint(
            "project_id",
            "version",
            "node_id",
            "source_revision_id",
            "chunk_id",
            name="uq_graph_project_node_source_project_pk",
        ),
        sa.Index("ix_graph_project_node_source_chunk", "project_id", "version", "chunk_id"),
        *_scope_constraints("graph_project_node_source"),
    )
    op.create_table(
        "graph_project_edge_evidence",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("edge_id", sa.String(128), primary_key=True),
        sa.Column("source_revision_id", sa.String(128), primary_key=True),
        sa.Column("chunk_id", sa.String(128), primary_key=True),
        sa.Column("document_revision_id", sa.String(128), nullable=False),
        sa.Column("anchor_json", JSON, nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("fragment_snapshot_id", sa.String(64), nullable=False),
        sa.Column("fragment_edge_id", sa.String(128), nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "version", "edge_id"],
            [
                "graph_project_edge.project_id",
                "graph_project_edge.version",
                "graph_project_edge.edge_id",
            ],
            name="fk_graph_project_edge_evidence_edge",
        ),
        sa.UniqueConstraint(
            "project_id",
            "version",
            "edge_id",
            "source_revision_id",
            "chunk_id",
            name="uq_graph_project_edge_evidence_project_pk",
        ),
        sa.Index("ix_graph_project_edge_evidence_chunk", "project_id", "version", "chunk_id"),
        *_scope_constraints("graph_project_edge_evidence"),
    )
    op.create_table(
        "graph_project_alias",
        sa.Column("version", sa.Integer, primary_key=True),
        # 255, not 512: under the server's utf8mb4 default, the composite
        # (version, alias_norm, node_id, project_id) key at 512 chars
        # overflows MySQL's 3072-byte index limit by 4 bytes.
        sa.Column("alias_norm", sa.String(255), primary_key=True),
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("origin", sa.String(8), nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "version", "node_id"],
            [
                "graph_project_node.project_id",
                "graph_project_node.version",
                "graph_project_node.node_id",
            ],
            name="fk_graph_project_alias_node",
        ),
        sa.UniqueConstraint(
            "project_id",
            "version",
            "alias_norm",
            "node_id",
            name="uq_graph_project_alias_project_pk",
        ),
        *_scope_constraints("graph_project_alias"),
    )
    op.create_table(
        "graph_project_community",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("community_id", sa.String(64), primary_key=True),
        sa.Column("label", sa.String(512), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "version"],
            ["graph_project_version.project_id", "graph_project_version.version"],
            name="fk_graph_project_community_version",
        ),
        sa.UniqueConstraint(
            "project_id",
            "version",
            "community_id",
            name="uq_graph_project_community_project_pk",
        ),
        *_scope_constraints("graph_project_community"),
    )
    op.create_table(
        "graph_merge_log",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("merged_from", JSON, nullable=False),
        sa.Column("rule", sa.String(16), nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "version", "node_id"],
            [
                "graph_project_node.project_id",
                "graph_project_node.version",
                "graph_project_node.node_id",
            ],
            name="fk_graph_merge_log_node",
        ),
        sa.UniqueConstraint(
            "project_id", "version", "node_id", name="uq_graph_merge_log_project_pk"
        ),
        *_scope_constraints("graph_merge_log"),
    )
    op.create_table(
        "graph_project_merge_job",
        sa.Column("due_at", DATETIME(fsp=6), nullable=True),
        sa.Column("last_reason", sa.String(32), nullable=True),
        sa.Column("claimed_due_at", DATETIME(fsp=6), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", DATETIME(fsp=6), nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_graph_project_merge_job_project_pk"),
        *_scope_constraints("graph_project_merge_job"),
    )


def downgrade() -> None:
    op.drop_table("graph_project_merge_job")
    op.drop_table("graph_merge_log")
    op.drop_table("graph_project_community")
    op.drop_table("graph_project_alias")
    op.drop_table("graph_project_edge_evidence")
    op.drop_table("graph_project_node_source")
    op.drop_table("graph_project_edge")
    op.drop_table("graph_project_node")
    op.drop_table("graph_project_version")
