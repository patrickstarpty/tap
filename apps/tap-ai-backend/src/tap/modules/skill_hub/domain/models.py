"""Skill Hub domain model: Skill maintenance events (spec 4).

A maintenance event records one Skill write -- a new revision, an
enable/disable toggle, an owner transfer or (phase 2) a source sync -- and
is stored in the same transaction as that write. Creating a Skill records no
event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext

SkillMaintenanceKind = Literal["revised", "enabled", "disabled", "owner_transferred", "synced"]


@dataclass(frozen=True, slots=True)
class SkillMaintenanceEvent:
    event_id: str
    project_id: str
    skill_id: str
    actor_id: str
    kind: SkillMaintenanceKind
    revision_id: str | None
    note: str | None
    occurred_at: datetime


def new_skill_event(
    scope: ProjectScopeContext,
    skill_id: str,
    kind: SkillMaintenanceKind,
    *,
    revision_id: str | None = None,
    now: datetime,
) -> SkillMaintenanceEvent:
    return SkillMaintenanceEvent(
        event_id=f"sevt-{uuid4().hex[:16]}",
        project_id=scope.project_id,
        skill_id=skill_id,
        actor_id=scope.actor_id,
        kind=kind,
        revision_id=revision_id,
        note=None,
        occurred_at=now,
    )
