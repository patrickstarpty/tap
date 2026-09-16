"""Add immutable Test Plan revisions and durable generation jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0014_test_management"
down_revision: str | None = "0013_knowledge_graph"
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


def _revision_child(
    name: str, identity: str, *columns: sa.Column, extra_constraints: tuple = ()
) -> None:
    op.create_table(
        name,
        sa.Column(identity, sa.String(128), primary_key=True),
        sa.Column("revision_id", sa.String(64), primary_key=True),
        *columns,
        *_scope_columns(),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["test_plan_revision.project_id", "test_plan_revision.revision_id"],
            name=f"fk_{name}_revision",
        ),
        sa.UniqueConstraint("project_id", "revision_id", identity, name=f"uq_{name}_revision_pk"),
        *extra_constraints,
        *_scope_constraints(name),
    )


def upgrade() -> None:
    op.create_table(
        "test_plan",
        sa.Column("test_plan_id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("active_published_revision_id", sa.String(64)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "test_plan_id", name="uq_test_plan_project_pk"),
        *_scope_constraints("test_plan"),
    )
    op.create_table(
        "test_plan_revision",
        sa.Column("revision_id", sa.String(64), primary_key=True),
        sa.Column("test_plan_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("adopted_from_revision_id", sa.String(64)),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("scope_items", JSON, nullable=False),
        sa.Column("prerequisites", JSON, nullable=False),
        sa.Column("risks", JSON, nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("validation_digest", sa.String(71)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("published_at", DATETIME(fsp=6)),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "revision_id", name="uq_test_plan_revision_project_pk"),
        sa.UniqueConstraint(
            "project_id", "test_plan_id", "version", name="uq_test_plan_revision_version"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "test_plan_id"],
            ["test_plan.project_id", "test_plan.test_plan_id"],
            name="fk_test_plan_revision_plan",
        ),
        *_scope_constraints("test_plan_revision"),
    )
    op.create_foreign_key(
        "fk_test_plan_active_revision",
        "test_plan",
        "test_plan_revision",
        ["project_id", "active_published_revision_id"],
        ["project_id", "revision_id"],
    )
    _revision_child(
        "test_case",
        "case_id",
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("critical", sa.Boolean, nullable=False),
        extra_constraints=(
            sa.UniqueConstraint(
                "project_id", "revision_id", "ordinal", name="uq_test_case_ordinal"
            ),
        ),
    )
    _revision_child(
        "test_scenario",
        "scenario_id",
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        extra_constraints=(
            sa.ForeignKeyConstraint(
                ["project_id", "revision_id", "case_id"],
                ["test_case.project_id", "test_case.revision_id", "test_case.case_id"],
                name="fk_test_scenario_case",
            ),
            sa.UniqueConstraint(
                "project_id", "case_id", "ordinal", name="uq_test_scenario_ordinal"
            ),
        ),
    )
    _revision_child(
        "test_plan_step",
        "step_id",
        sa.Column("scenario_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("keyword", sa.String(8), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("expected_result", sa.Text),
        sa.Column("critical", sa.Boolean, nullable=False),
        extra_constraints=(
            sa.ForeignKeyConstraint(
                ["project_id", "revision_id", "scenario_id"],
                [
                    "test_scenario.project_id",
                    "test_scenario.revision_id",
                    "test_scenario.scenario_id",
                ],
                name="fk_test_plan_step_scenario",
            ),
            sa.UniqueConstraint(
                "project_id", "scenario_id", "ordinal", name="uq_test_plan_step_ordinal"
            ),
        ),
    )
    _revision_child(
        "test_plan_citation",
        "citation_id",
        sa.Column("source_revision_id", sa.String(128), nullable=False),
        sa.Column("document_revision_id", sa.String(128), nullable=False),
        sa.Column("chunk_id", sa.String(128), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("claim_text", sa.Text, nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
    )
    _revision_child(
        "test_plan_assumption",
        "assumption_id",
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("graph_edge_id", sa.String(128)),
    )
    _revision_child(
        "test_plan_unknown",
        "unknown_id",
        sa.Column("text", sa.Text, nullable=False),
    )
    _revision_child(
        "test_plan_coverage_gap",
        "gap_id",
        sa.Column("requirement_ref", sa.String(512), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
    )
    op.create_table(
        "test_plan_generation_job",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("test_plan_id", sa.String(64), nullable=False),
        sa.Column("revision_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("input_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("answer_evidence_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("model_alias", sa.String(128), nullable=False),
        sa.Column("agent_revision_id", sa.String(128), nullable=False),
        sa.Column("skill_revision_ids", JSON, nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
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
        sa.UniqueConstraint("project_id", "job_id", name="uq_test_plan_generation_job_project_pk"),
        sa.UniqueConstraint("project_id", "request_digest", name="uq_test_plan_generation_request"),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_test_plan_generation_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "conversation_id"],
            ["conversation.project_id", "conversation.conversation_id"],
            name="fk_test_plan_generation_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "turn_id"],
            ["chat_turn.project_id", "chat_turn.turn_id"],
            name="fk_test_plan_generation_turn",
        ),
        *_scope_constraints("test_plan_generation_job"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = (
        "test_plan_generation_job",
        "test_plan_coverage_gap",
        "test_plan_unknown",
        "test_plan_assumption",
        "test_plan_citation",
        "test_plan_step",
        "test_scenario",
        "test_case",
        "test_plan_revision",
        "test_plan",
    )
    for name in tables:
        if bind.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first() is not None:
            raise RuntimeError("Test Plan history requires retention; downgrade refused")
    op.drop_constraint("fk_test_plan_active_revision", "test_plan", type_="foreignkey")
    for name in tables:
        op.drop_table(name)
