"""Project-scoped workflow tables and same-task relational links."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.sql.schema import SchemaItem

from tap.platform.db.schema import augment_project_table, metadata


def _scoped(name: str, *items: SchemaItem) -> Table:
    table = Table(name, metadata, *items)
    augment_project_table(table)
    return table


workflow_task = _scoped(
    "workflow_task",
    Column("task_id", String(64), primary_key=True),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("request_json", JSON, nullable=False),
    Column("goal", Text, nullable=False),
    Column("conversation_id", String(64)),
    Column("status", String(24), nullable=False),
    Column("recovering_from", String(24)),
    Column("plan_revision", Integer, nullable=False),
    Column("next_sequence", BigInteger, nullable=False),
    Column("budget_limit", JSON, nullable=False),
    Column("budget_used", JSON, nullable=False),
    Column("admission_ready", Boolean, nullable=False, server_default="1"),
    Column("lease_owner", String(128)),
    Column("lease_token", String(64)),
    Column("lease_until", DATETIME(fsp=6)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("idempotency_key", name="uq_workflow_task_project_idempotency"),
)
Index("ix_workflow_task_status", workflow_task.c.project_id, workflow_task.c.status)

workflow_plan_revision = _scoped(
    "workflow_plan_revision",
    Column("revision_id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("goal", Text, nullable=False),
    Column("dependencies", JSON, nullable=False),
    Column("input_snapshot", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("task_id", "revision_number", name="uq_workflow_plan_revision_task_number"),
)

workflow_node = _scoped(
    "workflow_node",
    Column("node_id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False),
    Column("plan_revision", Integer, nullable=False),
    Column("parent_node_id", String(64)),
    Column("agent_revision_id", String(64)),
    Column("role", String(64), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("runtime", String(32), nullable=False),
    Column("status", String(24), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("task_id", "node_id", name="uq_workflow_node_task_node"),
    CheckConstraint("kind in ('agent','service','approval')", name="ck_workflow_node_kind"),
)
workflow_node.append_constraint(
    ForeignKeyConstraint(
        ["project_id", "task_id", "parent_node_id"],
        ["workflow_node.project_id", "workflow_node.task_id", "workflow_node.node_id"],
        name="fk_workflow_node_same_task_parent",
    )
)

workflow_attempt = _scoped(
    "workflow_attempt",
    Column("attempt_id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False),
    Column("node_id", String(64), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", String(24), nullable=False),
    Column("graph_run_id", String(64)),
    Column("checkpoint_ref", String(512)),
    Column("provider_ref", String(512)),
    Column("error_snapshot", JSON),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("task_id", "node_id", "attempt", name="uq_workflow_attempt_node_number"),
)

workflow_dependency = _scoped(
    "workflow_dependency",
    Column("dependency_id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False),
    Column("from_node_id", String(64), nullable=False),
    Column("to_node_id", String(64), nullable=False),
    Column("plan_revision", Integer, nullable=False),
    UniqueConstraint(
        "task_id", "from_node_id", "to_node_id", "plan_revision", name="uq_workflow_dependency_edge"
    ),
    CheckConstraint("from_node_id <> to_node_id", name="ck_workflow_dependency_not_self"),
)

workflow_event = _scoped(
    "workflow_event",
    Column("event_id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False),
    Column("sequence", BigInteger, nullable=False),
    Column("node_id", String(64)),
    Column("attempt", Integer, nullable=False),
    Column("kind", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("payload_digest", String(71), nullable=False),
    Column("occurred_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("task_id", "sequence", name="uq_workflow_event_task_sequence"),
)
Index(
    "ix_workflow_event_cursor",
    workflow_event.c.project_id,
    workflow_event.c.task_id,
    workflow_event.c.sequence,
)

workflow_command = _scoped(
    "workflow_command",
    Column("command_id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False),
    Column("target_node_id", String(64)),
    Column("kind", String(24), nullable=False),
    Column("payload_digest", String(71), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("expected_plan_revision", Integer, nullable=False),
    Column("receipt", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)

workflow_artifact = _scoped(
    "workflow_artifact",
    Column("artifact_id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False),
    Column("node_id", String(64)),
    Column("attempt", Integer, nullable=False),
    Column("object_ref", String(512), nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("version_ref", String(128), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)

for table, columns, name in (
    (workflow_attempt, ("node_id",), "node"),
    (workflow_event, ("node_id",), "node"),
    (workflow_command, ("target_node_id",), "target"),
    (workflow_artifact, ("node_id",), "node"),
    (workflow_dependency, ("from_node_id",), "from"),
    (workflow_dependency, ("to_node_id",), "to"),
):
    table.append_constraint(
        ForeignKeyConstraint(
            ["project_id", "task_id", *columns],
            ["workflow_node.project_id", "workflow_node.task_id", "workflow_node.node_id"],
            name=f"fk_{table.name}_same_task_{name}",
        )
    )

WORKFLOW_TABLES = (
    workflow_task,
    workflow_plan_revision,
    workflow_node,
    workflow_attempt,
    workflow_dependency,
    workflow_event,
    workflow_command,
    workflow_artifact,
)
