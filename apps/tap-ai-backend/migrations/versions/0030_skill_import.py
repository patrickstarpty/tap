"""Record imported SKILL.md provenance on immutable Skill revisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import JSON, LONGTEXT, MEDIUMTEXT

revision: str = "0030_skill_import"
down_revision: str | None = "0029_graph_job_next_attempt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skill_revision",
        sa.Column("origin", sa.String(16), nullable=False, server_default="seed"),
    )
    op.add_column("skill_revision", sa.Column("description", sa.String(1024), nullable=True))
    op.add_column("skill_revision", sa.Column("requested_tools_json", JSON, nullable=True))
    op.add_column("skill_revision", sa.Column("front_matter_json", JSON, nullable=True))
    op.add_column("skill_revision", sa.Column("source_markdown", LONGTEXT, nullable=True))
    # Imported bodies may approach the 64 KiB SKILL.md bound.
    op.alter_column(
        "skill_revision",
        "instruction_template",
        existing_type=sa.String(8000),
        type_=MEDIUMTEXT,
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "skill_revision",
        "instruction_template",
        existing_type=MEDIUMTEXT,
        type_=sa.String(8000),
        existing_nullable=True,
    )
    op.drop_column("skill_revision", "source_markdown")
    op.drop_column("skill_revision", "front_matter_json")
    op.drop_column("skill_revision", "requested_tools_json")
    op.drop_column("skill_revision", "description")
    op.drop_column("skill_revision", "origin")
