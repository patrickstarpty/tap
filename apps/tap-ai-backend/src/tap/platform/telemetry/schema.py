"""Trace persistence tables: `trace_span`, `model_call`, `model_call_content`.

Column names, types, nullability and indexes follow the observability design
spec §3. `augment_project_table` appends the shared scope columns and
enterprise/actor foreign keys, matching the pattern used by `chat_turn` et al.
"""

from __future__ import annotations

from sqlalchemy import (
    DECIMAL,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON, LONGTEXT

from tap.platform.db.schema import augment_project_table, metadata

trace_span = Table(
    "trace_span",
    metadata,
    Column("trace_id", String(32), primary_key=True),
    Column("span_id", String(16), primary_key=True),
    Column("parent_span_id", String(16)),
    Column("turn_id", String(64)),
    Column("job_id", String(64)),
    Column("service_name", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("status", String(16), nullable=False),
    Column("status_message", Text),
    Column("started_at", DATETIME(fsp=6), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("attributes", JSON),
)
# Rewritten to (project_id, turn_id) by `augment_project_table` below.
Index("ix_trace_span_project_turn", trace_span.c.turn_id)
augment_project_table(trace_span)

model_call = Table(
    "model_call",
    metadata,
    Column("call_id", String(36), primary_key=True),
    Column("trace_id", String(32)),
    Column("span_id", String(16)),
    Column("turn_id", String(64)),
    Column("job_id", String(64)),
    Column("operation", String(16), nullable=False),
    Column("model_name", String(255), nullable=False),
    Column("upstream_model", String(255)),
    Column("provider", String(64)),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_usd", DECIMAL(18, 8)),
    Column("latency_ms", Integer, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("error_code", String(64)),
    Column("gateway_call_id", String(128)),
    Column("provider_request_id", String(128)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)
augment_project_table(model_call)
# Declared after scoping so it stays a bare `(trace_id)` index, not project-prefixed.
Index("ix_model_call_trace", model_call.c.trace_id)

model_call_content = Table(
    "model_call_content",
    metadata,
    Column("call_id", String(36), primary_key=True),
    Column("request_json", LONGTEXT, nullable=False),
    Column("response_text", LONGTEXT),
    Column("reasoning_text", LONGTEXT),
)
augment_project_table(model_call_content)
model_call_content.append_constraint(
    ForeignKeyConstraint(
        ["project_id", "call_id"],
        ["model_call.project_id", "model_call.call_id"],
        name="fk_model_call_content_call",
    )
)

__all__ = ["model_call", "model_call_content", "trace_span"]
