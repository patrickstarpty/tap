"""Register Streamable HTTP MCP servers and their synchronized tool descriptors."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON

revision: str = "0032_mcp_servers"
down_revision: str | None = "0031_agent_composition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> list[sa.Column[str]]:
    return [
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    ]


def _scope_foreign_keys(name: str) -> list[sa.ForeignKeyConstraint]:
    return [
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


def upgrade() -> None:
    op.create_table(
        "ai_mcp_server",
        sa.Column("server_id", sa.String(64), primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("auth_header_env", sa.String(128), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_synced_at", DATETIME(fsp=6), nullable=True),
        sa.Column("last_error", sa.String(64), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "server_id", name="uq_ai_mcp_server_project_pk"),
        sa.UniqueConstraint("project_id", "slug", name="uq_ai_mcp_server_slug"),
        *_scope_foreign_keys("ai_mcp_server"),
    )
    op.create_table(
        "ai_mcp_tool",
        sa.Column("server_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("input_schema_json", JSON, nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        *_scope_columns(),
        sa.PrimaryKeyConstraint("server_id", "tool_name"),
        sa.ForeignKeyConstraint(
            ["project_id", "server_id"],
            ["ai_mcp_server.project_id", "ai_mcp_server.server_id"],
            name="fk_ai_mcp_tool_server",
        ),
        *_scope_foreign_keys("ai_mcp_tool"),
    )


def downgrade() -> None:
    op.drop_table("ai_mcp_tool")
    op.drop_table("ai_mcp_server")
