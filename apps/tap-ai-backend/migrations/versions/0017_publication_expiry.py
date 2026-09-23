"""Bind published retrieval authority to the approved review expiry."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0017_publication_expiry"
down_revision: str | None = "0016_knowledge_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_publication",
        sa.Column("expires_at", DATETIME(fsp=6), nullable=True),
    )
    op.execute(
        "UPDATE knowledge_publication AS publication "
        "JOIN knowledge_review_revision AS review "
        "ON review.project_id = publication.project_id "
        "AND review.review_id = publication.review_id "
        "SET publication.expires_at = review.expires_at"
    )
    op.execute(
        "UPDATE knowledge_review_command AS command_result "
        "JOIN knowledge_publication AS publication "
        "ON publication.project_id = command_result.project_id "
        "AND publication.publication_id = "
        "JSON_UNQUOTE(JSON_EXTRACT(command_result.result, '$.publication_id')) "
        "SET command_result.result = JSON_SET(command_result.result, '$.expires_at', "
        "DATE_FORMAT(publication.expires_at, '%Y-%m-%dT%H:%i:%s.%f+00:00'))"
    )
    op.alter_column(
        "knowledge_publication",
        "expires_at",
        existing_type=DATETIME(fsp=6),
        nullable=False,
    )


def downgrade() -> None:
    raise RuntimeError("published review expiry is authorization history and must be retained")
