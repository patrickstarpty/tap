"""Retain test-design model call intent and results for response reconciliation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0019_test_design_model_calls"
down_revision: str | None = "0018_ai_graph_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_graph_settlement",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("project_id", "run_id", name="uq_ai_graph_settlement_project_pk"),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["ai_graph_run.project_id", "ai_graph_run.run_id"],
            name="fk_ai_graph_settlement_run",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_ai_graph_settlement_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_ai_graph_settlement_scope_actor",
        ),
    )
    op.create_table(
        "test_design_model_call",
        sa.Column("call_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", JSON),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("project_id", "call_id", name="uq_test_design_model_call_project_pk"),
        sa.UniqueConstraint(
            "project_id", "request_digest", name="uq_test_design_model_call_request"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "job_id"],
            ["test_plan_generation_job.project_id", "test_plan_generation_job.job_id"],
            name="fk_test_design_call_job",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_test_design_model_call_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_test_design_model_call_scope_actor",
        ),
    )


def downgrade() -> None:
    raise RuntimeError("test-design model-call reconciliation history is retained")
