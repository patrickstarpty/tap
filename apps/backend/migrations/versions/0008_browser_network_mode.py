"""Freeze public-web policy for new browser bundles; legacy bundles stay scoped."""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0008_browser_network_mode"
down_revision: str | None = "0007_browser_session_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tap_browser_bundles",
        sa.Column(
            "network_mode",
            sa.String(32),
            nullable=False,
            server_default="declared_origins",
        ),
    )


def downgrade() -> None:
    op.drop_column("tap_browser_bundles", "network_mode")
