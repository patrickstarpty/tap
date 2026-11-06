"""Gate workflow admission and record Chat-owned workflow projection state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0040_native_workflow_bridge"
down_revision: str | None = "0039_agent_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_task",
        sa.Column("admission_ready", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "chat_turn",
        sa.Column("execution_owner", sa.String(16), nullable=False, server_default="chat"),
    )
    op.add_column("chat_turn", sa.Column("workflow_task_id", sa.String(64)))
    op.add_column(
        "chat_turn",
        sa.Column(
            "workflow_projection_sequence", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column("chat_turn", sa.Column("cancel_requested_at", DATETIME(fsp=6)))
    op.create_index("ix_chat_turn_workflow_task", "chat_turn", ["project_id", "workflow_task_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(
            sa.text(
                "SELECT 1 FROM chat_turn WHERE execution_owner = 'workflow' "
                "OR workflow_task_id IS NOT NULL OR cancel_requested_at IS NOT NULL "
                "OR workflow_projection_sequence > 0 LIMIT 1"
            )
        ).first()
        is not None
        or connection.execute(sa.text("SELECT 1 FROM workflow_task LIMIT 1")).first() is not None
    ):
        raise RuntimeError("cannot downgrade native workflow bridge with business records")
    op.drop_index("ix_chat_turn_workflow_task", table_name="chat_turn")
    op.drop_column("chat_turn", "cancel_requested_at")
    op.drop_column("chat_turn", "workflow_projection_sequence")
    op.drop_column("chat_turn", "workflow_task_id")
    op.drop_column("chat_turn", "execution_owner")
    op.drop_column("workflow_task", "admission_ready")
