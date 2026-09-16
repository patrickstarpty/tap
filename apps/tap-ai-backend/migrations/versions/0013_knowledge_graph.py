"""Add immutable, Project-scoped knowledge graph snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0013_knowledge_graph"
down_revision: str | None = "0012a_conversation_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns():
    return (
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
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
        "graph_snapshot",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("source_set_digest", sa.String(71), nullable=False),
        sa.Column("source_revision_ids", JSON, nullable=False),
        sa.Column("document_revision_ids", JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "snapshot_id", name="uq_graph_snapshot_project_pk"),
        *_scope_constraints("graph_snapshot"),
    )
    op.create_table(
        "graph_snapshot_revision",
        sa.Column("revision_id", sa.String(64), primary_key=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("revision_number", sa.Integer, nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "snapshot_id", "revision_number", name="uq_graph_snapshot_revision_number"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id"],
            ["graph_snapshot.project_id", "graph_snapshot.snapshot_id"],
            name="fk_graph_snapshot_revision_snapshot",
        ),
        *_scope_constraints("graph_snapshot_revision"),
    )
    op.create_table(
        "graph_active_snapshot",
        sa.Column("source_set_digest", sa.String(71), primary_key=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id"],
            ["graph_snapshot.project_id", "graph_snapshot.snapshot_id"],
            name="fk_graph_active_snapshot_snapshot",
        ),
        *_scope_constraints("graph_active_snapshot"),
    )
    op.create_table(
        "graph_snapshot_document_revision",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("document_revision_id", sa.String(128), primary_key=True),
        *_scope_columns(),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id"],
            ["graph_snapshot.project_id", "graph_snapshot.snapshot_id"],
            name="fk_graph_snapshot_document_snapshot",
        ),
        *_scope_constraints("graph_snapshot_document_revision"),
    )
    op.create_table(
        "graph_node",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("label", sa.String(512), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("canonical_key", sa.String(512), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "snapshot_id", "node_id", name="uq_graph_node_project_pk"
        ),
        sa.UniqueConstraint(
            "project_id", "snapshot_id", "canonical_key", name="uq_graph_node_canonical"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id"],
            ["graph_snapshot.project_id", "graph_snapshot.snapshot_id"],
            name="fk_graph_node_snapshot",
        ),
        *_scope_constraints("graph_node"),
    )
    op.create_table(
        "graph_edge",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("edge_id", sa.String(128), primary_key=True),
        sa.Column("source_node_id", sa.String(128), nullable=False),
        sa.Column("target_node_id", sa.String(128), nullable=False),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "snapshot_id", "edge_id", name="uq_graph_edge_project_pk"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id"],
            ["graph_snapshot.project_id", "graph_snapshot.snapshot_id"],
            name="fk_graph_edge_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id", "source_node_id"],
            ["graph_node.project_id", "graph_node.snapshot_id", "graph_node.node_id"],
            name="fk_graph_edge_source",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id", "target_node_id"],
            ["graph_node.project_id", "graph_node.snapshot_id", "graph_node.node_id"],
            name="fk_graph_edge_target",
        ),
        *_scope_constraints("graph_edge"),
    )
    for name, owner_column, owner_table, owner_id in (
        ("graph_node_evidence", "node_id", "graph_node", "node_id"),
        ("graph_edge_evidence", "edge_id", "graph_edge", "edge_id"),
    ):
        op.create_table(
            name,
            sa.Column("snapshot_id", sa.String(64), primary_key=True),
            sa.Column("evidence_id", sa.String(128), primary_key=True),
            sa.Column(owner_column, sa.String(128), primary_key=True),
            sa.Column("source_revision_id", sa.String(128), nullable=False),
            sa.Column("document_revision_id", sa.String(128), nullable=False),
            sa.Column("chunk_id", sa.String(128), nullable=False),
            sa.Column("anchor_json", JSON, nullable=False),
            sa.Column("content_digest", sa.String(71), nullable=False),
            *_scope_columns(),
            sa.ForeignKeyConstraint(
                ["project_id", "snapshot_id", owner_column],
                [
                    f"{owner_table}.project_id",
                    f"{owner_table}.snapshot_id",
                    f"{owner_table}.{owner_id}",
                ],
                name=f"fk_{name}_owner",
            ),
            *_scope_constraints(name),
        )
    op.create_table(
        "graph_inference_provenance",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("provenance_id", sa.String(128), primary_key=True),
        sa.Column("edge_id", sa.String(128), nullable=False),
        sa.Column("input_fact_ids", JSON, nullable=False),
        sa.Column("rule_digest", sa.String(71), nullable=False),
        *_scope_columns(),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id", "edge_id"],
            ["graph_edge.project_id", "graph_edge.snapshot_id", "graph_edge.edge_id"],
            name="fk_graph_inference_edge",
        ),
        *_scope_constraints("graph_inference_provenance"),
    )
    op.create_table(
        "graph_extraction_job",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("chunks_locator", sa.String(2048), nullable=False),
        sa.Column("extraction_profile_digest", sa.String(71), nullable=False),
        sa.Column("model_alias", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", DATETIME(fsp=6)),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "request_digest", name="uq_graph_extraction_request"),
        sa.ForeignKeyConstraint(
            ["project_id", "snapshot_id"],
            ["graph_snapshot.project_id", "graph_snapshot.snapshot_id"],
            name="fk_graph_extraction_snapshot",
        ),
        *_scope_constraints("graph_extraction_job"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for name in (
        "graph_extraction_job",
        "graph_inference_provenance",
        "graph_edge_evidence",
        "graph_node_evidence",
        "graph_edge",
        "graph_node",
        "graph_snapshot_document_revision",
        "graph_active_snapshot",
        "graph_snapshot_revision",
        "graph_snapshot",
    ):
        if bind.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first() is not None:
            raise RuntimeError("Graph history requires retention; downgrade refused")
    for name in (
        "graph_extraction_job",
        "graph_inference_provenance",
        "graph_edge_evidence",
        "graph_node_evidence",
        "graph_edge",
        "graph_node",
        "graph_snapshot_document_revision",
        "graph_active_snapshot",
        "graph_snapshot_revision",
        "graph_snapshot",
    ):
        op.drop_table(name)
