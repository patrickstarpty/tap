"""`turn_skill_activation` table definition: one row per Skill a Turn used,
with how it was activated (`manual`, `pinned`, `auto`; spec 1.2/4). Manual
and pinned rows are written when the Turn input is frozen, auto rows when
`skill.activate` runs; migration `0038_skill_hub` backfills history."""

from __future__ import annotations

import hashlib

from sqlalchemy import Column, Index, Integer, String, Table, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME

from tap.platform.db.schema import augment_project_table, metadata

turn_skill_activation = Table(
    "turn_skill_activation",
    metadata,
    Column("activation_id", String(64), primary_key=True),
    Column("turn_id", String(64), nullable=False),
    Column("skill_id", String(64), nullable=False),
    Column("skill_revision_id", String(64), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("activated_at", DATETIME(fsp=6), nullable=False),
    Column("bytes", Integer, nullable=False),
    # `augment_project_table` prefixes `project_id`.
    UniqueConstraint("turn_id", "skill_id", name="uq_turn_skill_activation_turn_skill"),
)
# `skill_id -> skill.skill_id` is registered in `PROJECT_PARENT_LINKS`
# (platform/db/schema.py) so `augment_project_table` wires the FK itself.
augment_project_table(turn_skill_activation)
Index(
    "ix_turn_skill_activation_project_skill_activated",
    turn_skill_activation.c.project_id,
    turn_skill_activation.c.skill_id,
    turn_skill_activation.c.activated_at,
)


def activation_id_for(turn_id: str, skill_id: str) -> str:
    """Deterministic id: a Turn records each Skill at most once."""
    return "act-" + hashlib.sha256(f"{turn_id}/{skill_id}".encode()).hexdigest()[:24]
