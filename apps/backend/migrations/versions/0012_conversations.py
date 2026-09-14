"""Persist scoped conversations and immutable turn evidence snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0012_conversations"
down_revision: str | None = "0011_ai_agent_skill_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns():
    return (
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    )


def _scope_constraints(name):
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
        "conversation",
        sa.Column("conversation_id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("project_id", "conversation_id", name="uq_conversation_project_pk"),
        *_scope_constraints("conversation"),
    )
    # Every legacy chat identity becomes a Conversation before the FK is installed.
    op.execute(
        sa.text(
            "INSERT INTO conversation "
            "(conversation_id,title,created_at,updated_at,enterprise_id,project_id,"
            "actor_id,identity_mode,identity_origin) "
            "SELECT chat_id, LEFT(MIN(message),120), MIN(created_at), MAX(created_at), "
            "MIN(enterprise_id), project_id, MIN(actor_id), MIN(identity_mode), "
            "MIN(identity_origin) FROM chat_turn GROUP BY project_id, chat_id"
        )
    )
    op.create_foreign_key(
        "fk_chat_turn_project_conversation",
        "chat_turn",
        "conversation",
        ["project_id", "chat_id"],
        ["project_id", "conversation_id"],
    )
    op.add_column(
        "chat_turn",
        sa.Column("processing_attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "turn_input_snapshot",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("snapshot_digest", sa.String(71), nullable=False),
        sa.Column("snapshot", JSON, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "snapshot_id", name="uq_turn_input_snapshot_project_pk"),
        sa.UniqueConstraint("project_id", "turn_id", name="uq_turn_input_snapshot_turn"),
        sa.ForeignKeyConstraint(
            ["project_id", "turn_id"],
            ["chat_turn.project_id", "chat_turn.turn_id"],
            name="fk_turn_input_snapshot_turn",
        ),
        *_scope_constraints("turn_input_snapshot"),
    )
    op.create_table(
        "turn_answer_evidence_snapshot",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("input_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("answer_digest", sa.String(71), nullable=False),
        sa.Column("snapshot_digest", sa.String(71), nullable=False),
        sa.Column("snapshot", JSON, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint(
            "project_id", "snapshot_id", name="uq_turn_answer_evidence_snapshot_project_pk"
        ),
        sa.UniqueConstraint("project_id", "turn_id", name="uq_turn_answer_evidence_snapshot_turn"),
        sa.ForeignKeyConstraint(
            ["project_id", "turn_id"],
            ["chat_turn.project_id", "chat_turn.turn_id"],
            name="fk_turn_answer_evidence_snapshot_turn",
        ),
        *_scope_constraints("turn_answer_evidence_snapshot"),
    )
    op.create_table(
        "turn_artifact_link",
        sa.Column("link_id", sa.String(64), primary_key=True),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("artifact_kind", sa.String(32), nullable=False),
        sa.Column("artifact_id", sa.String(128), nullable=False),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "link_id", name="uq_turn_artifact_link_project_pk"),
        sa.UniqueConstraint(
            "project_id",
            "turn_id",
            "artifact_kind",
            "artifact_id",
            name="uq_turn_artifact_link_identity",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "turn_id"],
            ["chat_turn.project_id", "chat_turn.turn_id"],
            name="fk_turn_artifact_link_turn",
        ),
        *_scope_constraints("turn_artifact_link"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("turn_artifact_link", "turn_answer_evidence_snapshot", "turn_input_snapshot"):
        if bind.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first() is not None:
            raise RuntimeError("Conversation evidence requires retention; downgrade refused")
    # Legacy-created conversations may only be removed when no independently-created row exists.
    extras = bind.execute(
        sa.text(
            "SELECT 1 FROM conversation c LEFT JOIN chat_turn t "
            "ON t.project_id=c.project_id AND t.chat_id=c.conversation_id "
            "WHERE t.turn_id IS NULL LIMIT 1"
        )
    ).first()
    if extras is not None:
        raise RuntimeError("Conversation history requires retention; downgrade refused")
    op.drop_table("turn_artifact_link")
    op.drop_table("turn_answer_evidence_snapshot")
    op.drop_table("turn_input_snapshot")
    op.drop_constraint("fk_chat_turn_project_conversation", "chat_turn", type_="foreignkey")
    op.drop_column("chat_turn", "processing_attempt")
    op.drop_table("conversation")
