"""Add project-scoped, versioned workflow tasks and durable event ledger."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0039_agent_workflows"
down_revision: str | None = "0038_skill_hub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope() -> list[sa.Column]:
    return [
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    ]


def _scope_keys(name: str, primary_key: str) -> list[sa.Constraint]:
    return [
        sa.UniqueConstraint("project_id", primary_key, name=f"uq_{name}_project_pk"),
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
    ]


def _task_parent(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["project_id", "task_id"],
        ["workflow_task.project_id", "workflow_task.task_id"],
        name=f"fk_{name}_project_parent_0",
    )


def _node_parent(name: str, column: str, suffix: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["project_id", "task_id", column],
        ["workflow_node.project_id", "workflow_node.task_id", "workflow_node.node_id"],
        name=f"fk_{name}_same_task_{suffix}",
    )


def upgrade() -> None:
    op.create_table(
        "workflow_task",
        sa.Column("task_id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("request_json", JSON, nullable=False),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("recovering_from", sa.String(24)),
        sa.Column("plan_revision", sa.Integer, nullable=False),
        sa.Column("next_sequence", sa.BigInteger, nullable=False),
        sa.Column("budget_limit", JSON, nullable=False),
        sa.Column("budget_used", JSON, nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_until", DATETIME(fsp=6)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        *_scope(),
        *_scope_keys("workflow_task", "task_id"),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_workflow_task_project_idempotency"
        ),
    )
    op.create_index("ix_workflow_task_status", "workflow_task", ["project_id", "status"])

    op.create_table(
        "workflow_plan_revision",
        sa.Column("revision_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("revision_number", sa.Integer, nullable=False),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("dependencies", JSON, nullable=False),
        sa.Column("input_snapshot", JSON, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope(),
        *_scope_keys("workflow_plan_revision", "revision_id"),
        _task_parent("workflow_plan_revision"),
        sa.UniqueConstraint(
            "project_id", "task_id", "revision_number", name="uq_workflow_plan_revision_task_number"
        ),
    )

    op.create_table(
        "workflow_node",
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("plan_revision", sa.Integer, nullable=False),
        sa.Column("parent_node_id", sa.String(64)),
        sa.Column("agent_revision_id", sa.String(64)),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("runtime", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope(),
        *_scope_keys("workflow_node", "node_id"),
        _task_parent("workflow_node"),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_revision_id"],
            ["ai_agent_revision.project_id", "ai_agent_revision.revision_id"],
            name="fk_workflow_node_project_parent_1",
        ),
        sa.UniqueConstraint("project_id", "task_id", "node_id", name="uq_workflow_node_task_node"),
        _node_parent("workflow_node", "parent_node_id", "parent"),
        sa.CheckConstraint("kind in ('agent','service','approval')", name="ck_workflow_node_kind"),
    )

    op.create_table(
        "workflow_attempt",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("graph_run_id", sa.String(64)),
        sa.Column("checkpoint_ref", sa.String(512)),
        sa.Column("provider_ref", sa.String(512)),
        sa.Column("error_snapshot", JSON),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope(),
        *_scope_keys("workflow_attempt", "attempt_id"),
        _task_parent("workflow_attempt"),
        _node_parent("workflow_attempt", "node_id", "node"),
        sa.UniqueConstraint(
            "project_id", "task_id", "node_id", "attempt", name="uq_workflow_attempt_node_number"
        ),
    )

    op.create_table(
        "workflow_dependency",
        sa.Column("dependency_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("from_node_id", sa.String(64), nullable=False),
        sa.Column("to_node_id", sa.String(64), nullable=False),
        sa.Column("plan_revision", sa.Integer, nullable=False),
        *_scope(),
        *_scope_keys("workflow_dependency", "dependency_id"),
        _task_parent("workflow_dependency"),
        _node_parent("workflow_dependency", "from_node_id", "from"),
        _node_parent("workflow_dependency", "to_node_id", "to"),
        sa.UniqueConstraint(
            "project_id",
            "task_id",
            "from_node_id",
            "to_node_id",
            "plan_revision",
            name="uq_workflow_dependency_edge",
        ),
        sa.CheckConstraint("from_node_id <> to_node_id", name="ck_workflow_dependency_not_self"),
    )

    op.create_table(
        "workflow_event",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.BigInteger, nullable=False),
        sa.Column("node_id", sa.String(64)),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column("occurred_at", DATETIME(fsp=6), nullable=False),
        *_scope(),
        *_scope_keys("workflow_event", "event_id"),
        _task_parent("workflow_event"),
        _node_parent("workflow_event", "node_id", "node"),
        sa.UniqueConstraint(
            "project_id", "task_id", "sequence", name="uq_workflow_event_task_sequence"
        ),
    )
    op.create_index(
        "ix_workflow_event_cursor", "workflow_event", ["project_id", "task_id", "sequence"]
    )

    op.create_table(
        "workflow_command",
        sa.Column("command_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("target_node_id", sa.String(64)),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("expected_plan_revision", sa.Integer, nullable=False),
        sa.Column("receipt", JSON, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope(),
        *_scope_keys("workflow_command", "command_id"),
        _task_parent("workflow_command"),
        _node_parent("workflow_command", "target_node_id", "target"),
    )

    op.create_table(
        "workflow_artifact",
        sa.Column("artifact_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("node_id", sa.String(64)),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("object_ref", sa.String(512), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("version_ref", sa.String(128), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope(),
        *_scope_keys("workflow_artifact", "artifact_id"),
        _task_parent("workflow_artifact"),
        _node_parent("workflow_artifact", "node_id", "node"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    for name in (
        "workflow_task",
        "workflow_plan_revision",
        "workflow_node",
        "workflow_attempt",
        "workflow_dependency",
        "workflow_event",
        "workflow_command",
        "workflow_artifact",
    ):
        if connection.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first() is not None:
            raise RuntimeError("cannot downgrade workflow schema with business records")
    for name in (
        "workflow_artifact",
        "workflow_command",
        "workflow_event",
        "workflow_dependency",
        "workflow_attempt",
        "workflow_node",
        "workflow_plan_revision",
        "workflow_task",
    ):
        op.drop_table(name)
