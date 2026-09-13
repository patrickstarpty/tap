"""Register fixed validation identity without rewriting historical business rows."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_validation_identity"
down_revision: str | None = "0005_projection_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enterprise",
        sa.Column("enterprise_id", sa.String(128), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.create_table(
        "project",
        sa.Column("project_id", sa.String(128), primary_key=True),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["enterprise_id"], ["enterprise.enterprise_id"], name="fk_project_enterprise"
        ),
        sa.UniqueConstraint("enterprise_id", "project_id", name="uq_project_enterprise_project"),
    )
    op.create_table(
        "actor_principal",
        sa.Column("actor_id", sa.String(128), primary_key=True),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("principal_type", sa.String(24), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["enterprise_id"], ["enterprise.enterprise_id"], name="fk_actor_principal_enterprise"
        ),
        sa.UniqueConstraint(
            "enterprise_id", "actor_id", name="uq_actor_principal_enterprise_actor"
        ),
    )
    op.execute("INSERT INTO enterprise (enterprise_id) VALUES ('local')")
    op.execute("INSERT INTO project (project_id, enterprise_id) VALUES ('tapper-demo', 'local')")
    op.execute(
        "INSERT INTO actor_principal (actor_id, enterprise_id, principal_type) "
        "VALUES ('tapper-local-user', 'local', 'VALIDATION')"
    )


def downgrade() -> None:
    op.drop_table("actor_principal")
    op.drop_table("project")
    op.drop_table("enterprise")
