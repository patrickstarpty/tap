"""Create Athena's durable document, ingestion, manifest, answer, and citation ledger."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003_athena_documents"
down_revision: str | None = "0002_outbox_claim_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_document",
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("current_revision_id", sa.String(length=128), nullable=True),
        sa.Column("source_content_hash", sa.String(length=71), nullable=False),
        sa.Column("dedupe_key", sa.String(length=71), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=240), nullable=True),
        sa.Column("activated_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint("dedupe_key", name="uq_knowledge_document_dedupe_key"),
    )
    op.create_index(
        "ix_knowledge_document_status_updated_id",
        "knowledge_document",
        ["status", "updated_at", "document_id"],
        unique=False,
    )
    op.create_table(
        "knowledge_document_revision",
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("source_content_hash", sa.String(length=71), nullable=False),
        sa.Column("original_blob_locator", sa.String(length=1024), nullable=False),
        sa.Column("normalized_blob_locator", sa.String(length=1024), nullable=True),
        sa.Column("chunks_blob_locator", sa.String(length=1024), nullable=True),
        sa.Column("embeddings_blob_locator", sa.String(length=1024), nullable=True),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("chunker_version", sa.String(length=128), nullable=False),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_document.document_id"],
            name="fk_knowledge_revision_document",
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint(
            "document_id",
            "source_content_hash",
            "parser_version",
            name="uq_knowledge_revision_source_parser",
        ),
    )
    op.create_foreign_key(
        "fk_knowledge_document_current_revision",
        "knowledge_document",
        "knowledge_document_revision",
        ["current_revision_id"],
        ["revision_id"],
    )
    op.create_table(
        "knowledge_ingestion_job",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("stage_results_json", mysql.JSON(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_until", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("next_attempt_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=240), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["knowledge_document_revision.revision_id"],
            name="fk_knowledge_job_revision",
        ),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("revision_id", "kind", name="uq_knowledge_job_revision_kind"),
    )
    op.create_index(
        "ix_knowledge_job_status_retry_created",
        "knowledge_ingestion_job",
        ["status", "next_attempt_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_job_status_lease",
        "knowledge_ingestion_job",
        ["status", "lease_until"],
        unique=False,
    )
    op.create_table(
        "knowledge_chunk_manifest",
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("logical_chunk_id", sa.String(length=128), nullable=False),
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("root_id", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=128), nullable=True),
        sa.Column("anchor_json", mysql.JSON(), nullable=False),
        sa.Column("chunk_content_hash", sa.String(length=71), nullable=False),
        sa.Column("embedding_model_version", sa.String(length=128), nullable=False),
        sa.Column("index_version", sa.String(length=128), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["knowledge_document_revision.revision_id"],
            name="fk_knowledge_manifest_revision",
        ),
        sa.PrimaryKeyConstraint("chunk_id"),
        sa.UniqueConstraint(
            "revision_id", "ordinal", name="uq_knowledge_manifest_revision_ordinal"
        ),
    )
    op.create_table(
        "knowledge_answer_snapshot",
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("query_hash", sa.String(length=71), nullable=False),
        sa.Column("selected_revisions_json", mysql.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_table(
        "knowledge_citation_snapshot",
        sa.Column("citation_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("source_content_hash", sa.String(length=71), nullable=False),
        sa.Column("chunk_content_hash", sa.String(length=71), nullable=False),
        sa.Column("anchor_json", mysql.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["knowledge_answer_snapshot.trace_id"],
            name="fk_knowledge_citation_answer",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("citation_id"),
        sa.UniqueConstraint("trace_id", "citation_id", name="uq_knowledge_citation_trace_id"),
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_knowledge_document_current_revision", "knowledge_document", type_="foreignkey"
    )
    op.drop_table("knowledge_citation_snapshot")
    op.drop_table("knowledge_answer_snapshot")
    op.drop_table("knowledge_chunk_manifest")
    op.drop_index("ix_knowledge_job_status_lease", table_name="knowledge_ingestion_job")
    op.drop_index("ix_knowledge_job_status_retry_created", table_name="knowledge_ingestion_job")
    op.drop_table("knowledge_ingestion_job")
    op.drop_table("knowledge_document_revision")
    op.drop_index("ix_knowledge_document_status_updated_id", table_name="knowledge_document")
    op.drop_table("knowledge_document")
