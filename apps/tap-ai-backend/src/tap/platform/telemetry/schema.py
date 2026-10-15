"""Trace persistence tables: `trace_span`, `model_call`, `model_call_content`.

Column names, types, nullability and indexes follow the observability design
spec §3. `augment_project_table` appends the shared scope columns and
enterprise/actor foreign keys, matching the pattern used by `chat_turn` et al.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import (
    CHAR,
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
    Column("trace_id", CHAR(32), primary_key=True),
    Column("span_id", CHAR(16), primary_key=True),
    Column("parent_span_id", CHAR(16)),
    Column("turn_id", String(64)),
    Column("job_id", String(64)),
    Column("service_name", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("status", String(16), nullable=False),
    Column("status_message", Text),
    Column("started_at", DATETIME(fsp=6), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("attributes", JSON),
    # When this row landed in MySQL, independent of `started_at` (span clock time).
    # The Langfuse sync worker scans by this column, not `started_at`, so it never
    # misses a span whose write landed after an earlier-timestamped sibling.
    # `UTC_TIMESTAMP(6)`, not `CURRENT_TIMESTAMP(6)`: the latter follows the MySQL
    # server's session time zone, which would silently desync this naive-UTC
    # column from `started_at` (always UTC) and the worker's UTC cutoff whenever
    # the server's time zone isn't UTC. MySQL 8.0.13+/8.4 requires the extra
    # parens around a non-`CURRENT_TIMESTAMP` function default.
    Column(
        "recorded_at",
        DATETIME(fsp=6),
        nullable=False,
        server_default=sa.text("(UTC_TIMESTAMP(6))"),
    ),
)
# Rewritten to (project_id, turn_id) by `augment_project_table` below.
Index("ix_trace_span_project_turn", trace_span.c.turn_id)
augment_project_table(trace_span)
# Declared after scoping so it stays a bare `(recorded_at)` index, not project-prefixed:
# the sync worker scans across all Projects.
Index("ix_trace_span_recorded_at", trace_span.c.recorded_at)

model_call = Table(
    "model_call",
    metadata,
    Column("call_id", CHAR(36), primary_key=True),
    Column("trace_id", CHAR(32)),
    Column("span_id", CHAR(16)),
    Column("turn_id", String(64)),
    Column("job_id", String(64)),
    Column("operation", String(16), nullable=False),
    Column("model_name", String(255), nullable=False),
    Column("upstream_model", String(255)),
    Column("provider", String(64)),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    # scale=10: a per-call USD cost below 1e-8 (plausible for a single cheap
    # embedding token) would round to 0.00000000 under the original scale=8
    # column, silently reporting a real charge as free. scale=10 keeps the
    # same 8-digit integer headroom while resolving down to 1e-10.
    Column("cost_usd", DECIMAL(18, 10)),
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
    Column("call_id", CHAR(36), primary_key=True),
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

# The Langfuse sync worker is a system process reading across every Project, so
# `langfuse_sync_cursor` and `langfuse_sync_failure` deliberately carry no scope
# columns and are never joined through `augment_project_table`/`scope_predicates`.
langfuse_sync_cursor = Table(
    "langfuse_sync_cursor",
    metadata,
    Column("name", String(32), primary_key=True),
    Column("position", DATETIME(fsp=6), nullable=False),
)

langfuse_sync_failure = Table(
    "langfuse_sync_failure",
    metadata,
    Column("kind", String(16), primary_key=True),
    Column("subject_id", String(64), primary_key=True),
    Column("failure_count", Integer, nullable=False),
    Column("last_error", String(1000)),
    Column("last_failed_at", DATETIME(fsp=6), nullable=False),
)

__all__ = [
    "langfuse_sync_cursor",
    "langfuse_sync_failure",
    "model_call",
    "model_call_content",
    "trace_span",
]
