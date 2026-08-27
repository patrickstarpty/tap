"""Shared SQLAlchemy metadata and provider-neutral transactional Outbox table."""

from sqlalchemy import BigInteger, Column, Index, Integer, MetaData, String, Table, Text
from sqlalchemy.dialects.mysql import DATETIME

metadata = MetaData()

outbox = Table(
    "outbox",
    metadata,
    Column("outbox_id", String(128), primary_key=True),
    Column("command_id", String(128), nullable=False, unique=True),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", String(64), nullable=False),
    Column("sequence", BigInteger),
    Column("message_type", String(64), nullable=False),
    Column("status", String(24), nullable=False, server_default="pending"),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", DATETIME(fsp=6), nullable=False),
    Column("claimed_by", String(128)),
    Column("claim_token", String(64)),
    Column("lease_until", DATETIME(fsp=6)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("published_at", DATETIME(fsp=6)),
    Column("last_error", Text),
)
Index("ix_outbox_claim", outbox.c.status, outbox.c.next_attempt_at, outbox.c.created_at)
Index("ix_outbox_expired_lease", outbox.c.status, outbox.c.lease_until)
