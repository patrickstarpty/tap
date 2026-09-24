"""Add versioned durable AI graph runs and LangGraph checkpoints."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON, LONGBLOB

revision: str = "0018_ai_graph_runs"
down_revision: str | None = "0017_publication_expiry"
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
        "ai_graph_run",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("graph_version", sa.String(128), nullable=False),
        sa.Column("state_schema_version", sa.Integer, nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("reasoning_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("waiting_reason", sa.Text),
        sa.Column("current_checkpoint_id", sa.String(64)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_until", DATETIME(fsp=6)),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("budget", JSON, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "run_id", name="uq_ai_graph_run_project_pk"),
        *_scope_constraints("ai_graph_run"),
    )
    op.create_table(
        "ai_graph_checkpoint",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("checkpoint_ns", sa.String(255), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("parent_checkpoint_id", sa.String(64)),
        sa.Column("checkpoint_type", sa.String(64), nullable=False),
        sa.Column("checkpoint_blob", LONGBLOB, nullable=False),
        sa.Column("metadata_type", sa.String(64), nullable=False),
        sa.Column("metadata_blob", LONGBLOB, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id",
            "run_id",
            "checkpoint_ns",
            "checkpoint_id",
            name="uq_ai_graph_checkpoint_pk",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["ai_graph_run.project_id", "ai_graph_run.run_id"],
            name="fk_ai_graph_checkpoint_run",
        ),
        *_scope_constraints("ai_graph_checkpoint"),
    )
    op.create_table(
        "ai_graph_checkpoint_write",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("checkpoint_ns", sa.String(255), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column("write_index", sa.Integer, primary_key=True),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(64), nullable=False),
        sa.Column("value_blob", LONGBLOB, nullable=False),
        sa.Column("task_path", sa.String(512), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id",
            "run_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "write_index",
            name="uq_ai_graph_checkpoint_write_pk",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id", "checkpoint_ns", "checkpoint_id"],
            [
                "ai_graph_checkpoint.project_id",
                "ai_graph_checkpoint.run_id",
                "ai_graph_checkpoint.checkpoint_ns",
                "ai_graph_checkpoint.checkpoint_id",
            ],
            name="fk_ai_graph_checkpoint_write_checkpoint",
        ),
        *_scope_constraints("ai_graph_checkpoint_write"),
    )


def downgrade() -> None:
    raise RuntimeError("AI graph checkpoints are retained workflow history")
