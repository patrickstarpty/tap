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
    op.add_column("test_plan_citation", sa.Column("anchor_json", JSON))
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
        sa.Column("strict_review_required", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    ):
        op.add_column("test_plan_generation_job", column)

    op.execute("UPDATE test_case SET covered_requirement_ids=JSON_ARRAY()")
    op.execute("UPDATE test_plan_step SET citation_ids=JSON_ARRAY(), unknown_ids=JSON_ARRAY()")
    op.execute(
        "UPDATE test_plan_revision SET requirement_ids=JSON_ARRAY(), "
        "approved_knowledge_revision_ids=JSON_ARRAY(), skill_revision_ids=JSON_ARRAY(), "
        "generated_content_digest=content_digest"
    )
    op.execute("UPDATE test_plan_generation_job SET approved_knowledge_revision_ids=JSON_ARRAY()")
    op.alter_column("test_case", "covered_requirement_ids", existing_type=JSON, nullable=False)
    op.alter_column("test_plan_step", "citation_ids", existing_type=JSON, nullable=False)
    op.alter_column("test_plan_step", "unknown_ids", existing_type=JSON, nullable=False)
    op.alter_column("test_plan_revision", "requirement_ids", existing_type=JSON, nullable=False)
    op.alter_column(
        "test_plan_revision",
        "approved_knowledge_revision_ids",
        existing_type=JSON,
        nullable=False,
    )
    op.alter_column("test_plan_revision", "skill_revision_ids", existing_type=JSON, nullable=False)
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
    op.create_table(
        "test_plan_write_command",
        sa.Column("command_id", sa.String(128), primary_key=True),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("result_kind", sa.String(16), nullable=False),
        sa.Column("result_id", sa.String(128), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "command_id", name="uq_test_plan_write_command_project_pk"
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_test_plan_write_command_idempotency",
        ),
        *_scope_constraints("test_plan_write_command"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    governed_checks = (
        "SELECT 1 FROM test_plan_revision WHERE requirement_scope_id IS NOT NULL "
        "OR JSON_LENGTH(requirement_ids) > 0 OR JSON_LENGTH(approved_knowledge_revision_ids) > 0 "
        "OR model_revision_id IS NOT NULL OR agent_revision_id IS NOT NULL "
        "OR JSON_LENGTH(skill_revision_ids) > 0 OR author_actor_id IS NOT NULL "
        "OR generated_content_digest IS NOT NULL OR needs_review = 1 LIMIT 1",
        "SELECT 1 FROM test_plan_generation_job WHERE requirement_scope IS NOT NULL "
        "OR JSON_LENGTH(approved_knowledge_revision_ids) > 0 OR model_revision_id IS NOT NULL "
        "OR retry_idempotency_key IS NOT NULL OR row_version <> 1 LIMIT 1",
        "SELECT 1 FROM test_case WHERE JSON_LENGTH(covered_requirement_ids) > 0 LIMIT 1",
        "SELECT 1 FROM test_plan_step WHERE JSON_LENGTH(citation_ids) > 0 "
        "OR JSON_LENGTH(unknown_ids) > 0 LIMIT 1",
        "SELECT 1 FROM test_plan_citation WHERE anchor_json IS NOT NULL LIMIT 1",
    )
    if any(bind.execute(sa.text(statement)).first() is not None for statement in governed_checks):
        raise RuntimeError("Test Plan governance data requires retention; downgrade refused")
    for name in (
        "test_plan_review_decision",
        "test_plan_source_impact",
        "test_plan_write_command",
    ):
        if bind.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first() is not None:
            raise RuntimeError("Test Plan review history requires retention; downgrade refused")
    op.drop_table("test_plan_write_command")
    op.drop_table("test_plan_source_impact")
    op.drop_table("test_plan_review_decision")
    for table, names in (
        (
            "test_plan_generation_job",
            (
                "retry_idempotency_key",
                "row_version",
                "strict_review_required",
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
        ("test_plan_citation", ("anchor_json",)),
        ("test_case", ("covered_requirement_ids",)),
    ):
        for name in names:
            op.drop_column(table, name)
