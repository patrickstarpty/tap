"""Add governed knowledge review and atomic publication authority."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0016_knowledge_review"
down_revision: str | None = "0015_parse_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    )


def _scope_constraints(name: str) -> tuple[sa.ForeignKeyConstraint, ...]:
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
        "knowledge_review_revision",
        sa.Column("review_id", sa.String(64), primary_key=True),
        sa.Column("source_revision_ids", JSON, nullable=False),
        sa.Column("inventory_digest", sa.String(71), nullable=False),
        sa.Column("chunk_manifest_digest", sa.String(71), nullable=False),
        sa.Column("annotation_digest", sa.String(71), nullable=False),
        sa.Column("dependency_digest", sa.String(71), nullable=False),
        sa.Column("editor_actor_ids", JSON, nullable=False),
        sa.Column("reviewer_actor_id", sa.String(128)),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("blocking_item_ids", JSON, nullable=False),
        sa.Column("approved_item_ids", JSON, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "review_id", name="uq_knowledge_review_revision_project_pk"
        ),
        *_scope_constraints("knowledge_review_revision"),
    )
    op.create_table(
        "knowledge_publication",
        sa.Column("publication_id", sa.String(64), primary_key=True),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("approval_digest", sa.String(71), nullable=False),
        sa.Column("source_revision_ids", JSON, nullable=False),
        sa.Column("approved_item_ids", JSON, nullable=False),
        sa.Column("generation", sa.String(256), nullable=False),
        sa.Column("published_by", sa.String(128), nullable=False),
        sa.Column("published_at", DATETIME(fsp=6), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("withdrawn_by", sa.String(128)),
        sa.Column("withdrawn_at", DATETIME(fsp=6)),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "publication_id", name="uq_knowledge_publication_project_pk"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "review_id"],
            ["knowledge_review_revision.project_id", "knowledge_review_revision.review_id"],
            name="fk_knowledge_publication_project_parent_0",
        ),
        *_scope_constraints("knowledge_publication"),
    )
    op.create_table(
        "knowledge_current_publication",
        sa.Column("pointer_id", sa.String(128), primary_key=True),
        sa.Column("publication_id", sa.String(64), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id",
            "pointer_id",
            name="uq_knowledge_current_publication_project_pk",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "publication_id"],
            ["knowledge_publication.project_id", "knowledge_publication.publication_id"],
            name="fk_knowledge_current_publication_project_parent_0",
        ),
        *_scope_constraints("knowledge_current_publication"),
    )
    op.create_table(
        "knowledge_review_command",
        sa.Column("command_id", sa.String(128), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("result", JSON, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "command_id", name="uq_knowledge_review_command_project_pk"
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_knowledge_review_command_project_command",
        ),
        *_scope_constraints("knowledge_review_command"),
    )
    op.create_table(
        "knowledge_publication_cleanup",
        sa.Column("publication_id", sa.String(64), primary_key=True),
        sa.Column("generation", sa.String(256), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id",
            "publication_id",
            name="uq_knowledge_publication_cleanup_project_pk",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "publication_id"],
            ["knowledge_publication.project_id", "knowledge_publication.publication_id"],
            name="fk_knowledge_publication_cleanup_project_parent_0",
        ),
        *_scope_constraints("knowledge_publication_cleanup"),
    )


def downgrade() -> None:
    raise RuntimeError("knowledge review and publication history requires retention")
