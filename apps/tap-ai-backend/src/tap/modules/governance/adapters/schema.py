"""Explicit Project audit schema; caller-owned transactions append immutable facts."""

from sqlalchemy import Column, ForeignKeyConstraint, Index, String, Table, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME, JSON

from tap.platform.db.schema import metadata

project_audit = Table(
    "project_audit",
    metadata,
    Column("audit_id", String(128, collation="utf8mb4_bin"), primary_key=True),
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("identity_mode", String(32), nullable=False),
    Column("identity_origin", String(32), nullable=False),
    Column("action", String(64), nullable=False),
    Column("resource", String(64), nullable=False),
    Column("resource_id", String(128)),
    Column("outcome", String(32), nullable=False),
    Column("correlation_id", String(128), nullable=False),
    Column("idempotency_key", String(128, collation="utf8mb4_bin"), nullable=False),
    Column("content_digest", String(64), nullable=False),
    Column("safe_metadata", JSON, nullable=False),
    Column("occurred_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint(
        "enterprise_id", "project_id", "idempotency_key", name="uq_project_audit_replay"
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "project_id"],
        ["project.enterprise_id", "project.project_id"],
        name="fk_project_audit_scope_project",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "actor_id"],
        ["actor_principal.enterprise_id", "actor_principal.actor_id"],
        name="fk_project_audit_scope_actor",
    ),
)
Index(
    "ix_project_audit_occurred",
    project_audit.c.enterprise_id,
    project_audit.c.project_id,
    project_audit.c.occurred_at,
    project_audit.c.audit_id,
)
