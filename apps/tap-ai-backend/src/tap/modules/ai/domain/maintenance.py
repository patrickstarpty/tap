"""AI asset maintenance events recorded with asset and feedback writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext

MaintenanceKind = Literal[
    "revised",
    "skill_upgraded",
    "disabled",
    "enabled",
    "feedback_resolved",
    "owner_transferred",
]


@dataclass(frozen=True, slots=True)
class MaintenanceEvent:
    event_id: str
    project_id: str
    agent_id: str
    actor_id: str
    kind: MaintenanceKind
    revision_id: str | None
    feedback_id: str | None
    note: str | None
    occurred_at: datetime


def new_event(
    scope: ProjectScopeContext,
    agent_id: str,
    kind: MaintenanceKind,
    *,
    revision_id: str | None = None,
    feedback_id: str | None = None,
    note: str | None = None,
    now: datetime,
) -> MaintenanceEvent:
    return MaintenanceEvent(
        event_id=f"mevt-{uuid4().hex[:16]}",
        project_id=scope.project_id,
        agent_id=agent_id,
        actor_id=scope.actor_id,
        kind=kind,
        revision_id=revision_id,
        feedback_id=feedback_id,
        note=note,
        occurred_at=now,
    )


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
