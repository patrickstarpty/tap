"""Report MCP tools a sync left out of the registry, with their reasons."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import JSON

revision: str = "0033_mcp_skipped_tools"
down_revision: str | None = "0032_mcp_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_mcp_server", sa.Column("skipped_tools_json", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("ai_mcp_server", "skipped_tools_json")
