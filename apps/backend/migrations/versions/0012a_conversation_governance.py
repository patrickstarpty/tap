"""Add immutable conversation execution authority without rewriting released revisions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0012a_conversation_governance"
down_revision: str | None = "0012_conversations"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("ai_agent_revision", sa.Column("system_instruction", sa.String(8000)))
    op.add_column("ai_agent_revision", sa.Column("output_schema_json", JSON))
    op.add_column("skill_revision", sa.Column("instruction_template", sa.String(8000)))
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
    op.add_column("chat_turn", sa.Column("processing_lease_token", sa.String(64)))
    op.add_column("chat_turn", sa.Column("processing_lease_expires_at", DATETIME(fsp=6)))
    op.add_column("chat_event", sa.Column("stream_sequence", sa.BigInteger(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE chat_event event_row JOIN ("
            "SELECT event_id, ROW_NUMBER() OVER (PARTITION BY turn_row.project_id, "
            "turn_row.chat_id ORDER BY event_row.occurred_at,event_row.event_id) AS cursor_value "
            "FROM chat_event event_row JOIN chat_turn turn_row "
            "ON turn_row.project_id=event_row.project_id AND "
            "turn_row.turn_id=event_row.turn_id"
            ") ranked ON ranked.event_id=event_row.event_id "
            "SET event_row.stream_sequence=ranked.cursor_value"
        )
    )
    op.alter_column("chat_event", "stream_sequence", existing_type=sa.BigInteger(), nullable=False)


def downgrade() -> None:
    op.drop_column("chat_event", "stream_sequence")
    op.drop_column("chat_turn", "processing_lease_expires_at")
    op.drop_column("chat_turn", "processing_lease_token")
    op.alter_column(
        "chat_turn",
        "processing_attempt",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default="1",
    )
    op.drop_column("skill_revision", "instruction_template")
    op.drop_column("ai_agent_revision", "output_schema_json")
    op.drop_column("ai_agent_revision", "system_instruction")
