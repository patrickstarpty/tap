"""Add immutable conversation execution authority without rewriting released revisions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0012a_conversation_governance"
down_revision: str | None = "0012_conversations"
branch_labels: str | None = None
depends_on: str | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    return column_name in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _add_column_if_missing(table_name: str, column: sa.Column[object]) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_present(table_name: str, column_name: str) -> None:
    if _has_column(table_name, column_name):
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    _add_column_if_missing("ai_agent_revision", sa.Column("system_instruction", sa.String(8000)))
    _add_column_if_missing("ai_agent_revision", sa.Column("output_schema_json", JSON))
    _add_column_if_missing("skill_revision", sa.Column("instruction_template", sa.String(8000)))
    # The briefly deployed a22 shape made these execution-content columns non-null.
    # Converge both that shape and the original released 0012 shape to the same target.
    op.alter_column(
        "ai_agent_revision",
        "system_instruction",
        existing_type=sa.String(8000),
        nullable=True,
    )
    op.alter_column("ai_agent_revision", "output_schema_json", existing_type=JSON, nullable=True)
    op.alter_column(
        "skill_revision",
        "instruction_template",
        existing_type=sa.String(8000),
        nullable=True,
    )
    op.execute(
        sa.text(
            "UPDATE ai_agent_revision SET system_instruction="
            "'knowledge-agent-system-instruction-v1' "
            "WHERE revision_id='validation-knowledge-agent-v1' AND "
            "system_instruction_digest="
            "'sha256:fce11c5d9869cb393bd12b126a667e53f2e38cea27af38891c7f6f08a123bba9'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE skill_revision SET instruction_template='citation-skill-template-v1' "
            "WHERE revision_id='validation-citation-skill-v1' AND "
            "instruction_template_digest="
            "'sha256:a5c26513151960f626d1d756736dac11f529cabe0ea9f43da1d194ed37ef0702'"
        )
    )
    op.alter_column(
        "chat_turn",
        "processing_attempt",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default="0",
    )
    _add_column_if_missing("chat_turn", sa.Column("processing_lease_token", sa.String(64)))
    _add_column_if_missing("chat_turn", sa.Column("processing_lease_expires_at", DATETIME(fsp=6)))
    _add_column_if_missing(
        "chat_event", sa.Column("stream_sequence", sa.BigInteger(), nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE chat_event event_row JOIN ("
            "SELECT event_id, ROW_NUMBER() OVER (PARTITION BY turn_row.project_id, "
            "turn_row.chat_id ORDER BY event_row.occurred_at,event_row.event_id) AS cursor_value "
            "FROM chat_event event_row JOIN chat_turn turn_row "
            "ON turn_row.project_id=event_row.project_id AND "
            "turn_row.turn_id=event_row.turn_id"
            ") ranked ON ranked.event_id=event_row.event_id "
            "SET event_row.stream_sequence=ranked.cursor_value "
            "WHERE event_row.stream_sequence IS NULL"
        )
    )
    op.alter_column("chat_event", "stream_sequence", existing_type=sa.BigInteger(), nullable=False)


def downgrade() -> None:
    _drop_column_if_present("chat_event", "stream_sequence")
    _drop_column_if_present("chat_turn", "processing_lease_expires_at")
    _drop_column_if_present("chat_turn", "processing_lease_token")
    if _has_column("chat_turn", "processing_attempt"):
        op.alter_column(
            "chat_turn",
            "processing_attempt",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default="1",
        )
    _drop_column_if_present("skill_revision", "instruction_template")
    _drop_column_if_present("ai_agent_revision", "output_schema_json")
    _drop_column_if_present("ai_agent_revision", "system_instruction")
