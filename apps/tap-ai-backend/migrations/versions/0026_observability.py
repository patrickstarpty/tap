"""Persist trace spans and model call metadata/content for observability."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, JSON, LONGTEXT

revision: str = "0026_observability"
down_revision: str | None = "0025_managed_purge_obligation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
    ]


def _scope_constraints(name: str) -> list[sa.ForeignKeyConstraint]:
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
    op.add_column("chat_turn", sa.Column("traceparent", sa.String(55), nullable=True))

    op.create_table(
        "trace_span",
        sa.Column("trace_id", sa.CHAR(32), primary_key=True),
        sa.Column("span_id", sa.CHAR(16), primary_key=True),
        sa.Column("parent_span_id", sa.CHAR(16)),
        sa.Column("turn_id", sa.String(64)),
        sa.Column("job_id", sa.String(64)),
        sa.Column("service_name", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_message", sa.Text()),
        sa.Column("started_at", DATETIME(fsp=6), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("attributes", JSON),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "trace_id", "span_id", name="uq_trace_span_project_pk"),
        *_scope_constraints("trace_span"),
    )
    op.create_index("ix_trace_span_project_turn", "trace_span", ["project_id", "turn_id"])

    op.create_table(
        "model_call",
        sa.Column("call_id", sa.CHAR(36), primary_key=True),
        sa.Column("trace_id", sa.CHAR(32)),
        sa.Column("span_id", sa.CHAR(16)),
        sa.Column("turn_id", sa.String(64)),
        sa.Column("job_id", sa.String(64)),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("upstream_model", sa.String(255)),
        sa.Column("provider", sa.String(64)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.DECIMAL(18, 8)),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("gateway_call_id", sa.String(128)),
        sa.Column("provider_request_id", sa.String(128)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "call_id", name="uq_model_call_project_pk"),
        *_scope_constraints("model_call"),
    )
    op.create_index("ix_model_call_trace", "model_call", ["trace_id"])

    op.create_table(
        "model_call_content",
        sa.Column("call_id", sa.CHAR(36), primary_key=True),
        sa.Column("request_json", LONGTEXT, nullable=False),
        sa.Column("response_text", LONGTEXT),
        sa.Column("reasoning_text", LONGTEXT),
        *_scope_columns(),
        sa.UniqueConstraint("project_id", "call_id", name="uq_model_call_content_project_pk"),
        *_scope_constraints("model_call_content"),
        sa.ForeignKeyConstraint(
            ["project_id", "call_id"],
            ["model_call.project_id", "model_call.call_id"],
            name="fk_model_call_content_call",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    trace_span_rows = bind.execute(sa.text("SELECT COUNT(*) FROM trace_span")).scalar_one()
    model_call_rows = bind.execute(sa.text("SELECT COUNT(*) FROM model_call")).scalar_one()
    model_call_content_rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM model_call_content")
    ).scalar_one()
    traceparent_rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM chat_turn WHERE traceparent IS NOT NULL")
    ).scalar_one()
    if trace_span_rows or model_call_rows or model_call_content_rows or traceparent_rows:
        raise RuntimeError("refusing to drop observability tables: trace data would be lost")
    op.drop_table("model_call_content")
    op.drop_table("model_call")
    op.drop_table("trace_span")
    op.drop_column("chat_turn", "traceparent")
