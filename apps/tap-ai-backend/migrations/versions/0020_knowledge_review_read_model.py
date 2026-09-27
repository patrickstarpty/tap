"""Persist review checklist/history and version publication writes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0020_knowledge_review_read_model"
down_revision: str | None = "0019_test_design_model_calls"
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
    op.add_column(
        "knowledge_publication",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        "UPDATE knowledge_publication SET version = "
        "CASE WHEN status = 'withdrawn' THEN 2 ELSE 1 END"
    )
    op.execute(
        "UPDATE knowledge_review_command AS command_result "
        "JOIN knowledge_publication AS publication "
        "ON publication.project_id = command_result.project_id "
        "AND publication.publication_id = "
        "JSON_UNQUOTE(JSON_EXTRACT(command_result.result, '$.publication_id')) "
        "SET command_result.result = JSON_SET(command_result.result, '$.version', "
        "CASE JSON_UNQUOTE(JSON_EXTRACT(command_result.result, '$.status')) "
        "WHEN 'withdrawn' THEN 2 ELSE 1 END)"
    )
    op.alter_column(
        "knowledge_publication", "version", existing_type=sa.Integer(), server_default=None
    )
    op.create_table(
        "knowledge_review_item_decision",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(128), nullable=False),
        sa.Column("check_kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("decided_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "decision_id", name="uq_knowledge_review_item_decision_project_pk"
        ),
        sa.UniqueConstraint(
            "project_id",
            "review_id",
            "item_id",
            "review_version",
            name="uq_review_item_decision_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "review_id"],
            ["knowledge_review_revision.project_id", "knowledge_review_revision.review_id"],
            name="fk_knowledge_review_item_decision_project_parent_0",
        ),
        *_scope_constraints("knowledge_review_item_decision"),
    )
    op.create_table(
        "knowledge_review_history",
        sa.Column("history_id", sa.String(128), primary_key=True),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("history_actor_id", sa.String(128), nullable=False),
        sa.Column("item_id", sa.String(128)),
        sa.Column("decision_id", sa.String(128)),
        sa.Column("decision_digest", sa.String(71)),
        sa.Column("occurred_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "history_id", name="uq_knowledge_review_history_project_pk"
        ),
        sa.UniqueConstraint(
            "project_id", "review_id", "review_version", name="uq_review_history_version"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "review_id"],
            ["knowledge_review_revision.project_id", "knowledge_review_revision.review_id"],
            name="fk_knowledge_review_history_project_parent_0",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "decision_id"],
            [
                "knowledge_review_item_decision.project_id",
                "knowledge_review_item_decision.decision_id",
            ],
            name="fk_knowledge_review_history_project_parent_1",
        ),
        *_scope_constraints("knowledge_review_history"),
    )
    op.create_index(
        "ix_review_decision_project_review_version",
        "knowledge_review_item_decision",
        ["project_id", "review_id", "review_version"],
    )
    op.create_index(
        "ix_publication_project_review_cursor",
        "knowledge_publication",
        ["project_id", "review_id", "publication_id"],
    )
    op.execute(
        "INSERT INTO knowledge_review_history ("
        "history_id, review_id, review_version, action, history_actor_id, item_id, "
        "occurred_at, enterprise_id, project_id, actor_id, identity_mode, identity_origin"
        ") SELECT "
        "CONCAT('krh_', SHA2(CONCAT(project_id, ':', review_id, ':', version), 256)), "
        "review_id, version, 'migrated', actor_id, NULL, updated_at, enterprise_id, "
        "project_id, actor_id, identity_mode, identity_origin "
        "FROM knowledge_review_revision"
    )


def downgrade() -> None:
    raise RuntimeError("knowledge review checklist and history are retained")
