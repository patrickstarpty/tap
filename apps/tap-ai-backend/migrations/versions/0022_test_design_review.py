"""Bind Test Plan generation scope, human review, and source impacts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0022_test_design_review"
down_revision: str | None = "0021_original_excerpt_alignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), primary_key=True),
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
    op.add_column("test_case", sa.Column("covered_requirement_ids", JSON, nullable=True))
    op.add_column("test_plan_step", sa.Column("citation_ids", JSON, nullable=True))
    op.add_column("test_plan_step", sa.Column("unknown_ids", JSON, nullable=True))
    for column in (
        sa.Column("requirement_scope_id", sa.String(128)),
        sa.Column("requirement_scope_version", sa.Integer()),
        sa.Column("requirement_scope_digest", sa.String(71)),
        sa.Column("requirement_ids", JSON, nullable=True),
        sa.Column("approved_knowledge_revision_ids", JSON, nullable=True),
        sa.Column("model_revision_id", sa.String(128)),
        sa.Column("agent_revision_id", sa.String(128)),
        sa.Column("skill_revision_ids", JSON, nullable=True),
        sa.Column("author_actor_id", sa.String(128)),
        sa.Column("strict_review_required", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("generated_content_digest", sa.String(71)),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("needs_review_reason", sa.String(512)),
    ):
        op.add_column("test_plan_revision", column)
    for column in (
        sa.Column("requirement_scope", JSON),
        sa.Column("approved_knowledge_revision_ids", JSON, nullable=True),
        sa.Column("model_revision_id", sa.String(128)),
        sa.Column("retry_idempotency_key", sa.String(128)),
    ):
        op.add_column("test_plan_generation_job", column)

    op.execute("UPDATE test_case SET covered_requirement_ids=JSON_ARRAY()")
    op.execute("UPDATE test_plan_step SET citation_ids=JSON_ARRAY(), unknown_ids=JSON_ARRAY()")
    op.execute(
        "UPDATE test_plan_revision SET requirement_ids=JSON_ARRAY(), "
        "approved_knowledge_revision_ids=JSON_ARRAY(), skill_revision_ids=JSON_ARRAY(), "
        "generated_content_digest=content_digest"
    )
    op.execute(
        "UPDATE test_plan_generation_job SET approved_knowledge_revision_ids=JSON_ARRAY()"
    )
    op.alter_column(
        "test_case", "covered_requirement_ids", existing_type=JSON, nullable=False
    )
    op.alter_column("test_plan_step", "citation_ids", existing_type=JSON, nullable=False)
    op.alter_column("test_plan_step", "unknown_ids", existing_type=JSON, nullable=False)
    op.alter_column(
        "test_plan_revision", "requirement_ids", existing_type=JSON, nullable=False
    )
    op.alter_column(
        "test_plan_revision",
        "approved_knowledge_revision_ids",
        existing_type=JSON,
        nullable=False,
    )
    op.alter_column(
        "test_plan_revision", "skill_revision_ids", existing_type=JSON, nullable=False
    )
    op.alter_column(
        "test_plan_generation_job",
        "approved_knowledge_revision_ids",
        existing_type=JSON,
        nullable=False,
    )

    op.create_table(
        "test_plan_review_decision",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column("revision_id", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("review_actor_id", sa.String(128), nullable=False),
        sa.Column("reviewed_content_digest", sa.String(71), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("decision_digest", sa.String(71), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "decision_id", name="uq_test_plan_review_decision_project_pk"
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_test_plan_review_decision_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["test_plan_revision.project_id", "test_plan_revision.revision_id"],
            name="fk_test_plan_review_decision_revision",
        ),
        *_scope_constraints("test_plan_review_decision"),
    )
    op.create_table(
        "test_plan_source_impact",
        sa.Column("impact_id", sa.String(128), primary_key=True),
        sa.Column("revision_id", sa.String(64), nullable=False),
        sa.Column("source_revision_id", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "impact_id", name="uq_test_plan_source_impact_project_pk"
        ),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_test_plan_source_impact_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["test_plan_revision.project_id", "test_plan_revision.revision_id"],
            name="fk_test_plan_source_impact_revision",
        ),
        *_scope_constraints("test_plan_source_impact"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("test_plan_review_decision", "test_plan_source_impact"):
        if bind.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first() is not None:
            raise RuntimeError("Test Plan review history requires retention; downgrade refused")
    op.drop_table("test_plan_source_impact")
    op.drop_table("test_plan_review_decision")
    for table, names in (
        (
            "test_plan_generation_job",
            (
                "retry_idempotency_key",
                "model_revision_id",
                "approved_knowledge_revision_ids",
                "requirement_scope",
            ),
        ),
        (
            "test_plan_revision",
            (
                "needs_review_reason",
                "needs_review",
                "generated_content_digest",
                "strict_review_required",
                "author_actor_id",
                "skill_revision_ids",
                "agent_revision_id",
                "model_revision_id",
                "approved_knowledge_revision_ids",
                "requirement_ids",
                "requirement_scope_digest",
                "requirement_scope_version",
                "requirement_scope_id",
            ),
        ),
        ("test_plan_step", ("unknown_ids", "citation_ids")),
        ("test_case", ("covered_requirement_ids",)),
    ):
        for name in names:
            op.drop_column(table, name)
