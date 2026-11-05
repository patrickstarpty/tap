"""Skill Hub domain model: Skill maintenance events (spec 4) and the read
models the Skill Hub views return (spec 3/5; field names are the HTTP
fields in snake_case).

A maintenance event records one Skill write -- a new revision, an
enable/disable toggle, an owner transfer or (phase 2) a source sync -- and
is stored in the same transaction as that write. Creating a Skill records no
event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Generic, Literal, TypeVar
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.assets import SkillFile, SkillRevision
from tap.modules.ai.domain.skill_metadata import ActivationMode
from tap.platform.reporting.period import Period
from tap.platform.reporting.runtime import RuntimeStatus

if TYPE_CHECKING:
    # Only for type checking: `ports.py` imports this module at runtime, so an
    # eager import here would be circular. Annotations are strings (PEP 563).
    from tap.modules.skill_hub.ports import (
        RankedKey,
        SkillRecord,
        SkillRuntimeStats,
        UsagePoint,
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


# Spec 1.3: the system prompt lists at most 60 candidate Skills.
CANDIDATE_LIMIT = 60

# Activation runs per mode; always carries all three modes (zero when unused).
ModeCounts = dict[ActivationMode, int]


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """One library row: the Skill and its activations in the period. The
    per-mode counts are not additive across Skills (a run using N Skills
    counts once for each)."""

    record: SkillRecord
    activations: int
    by_mode: ModeCounts


@dataclass(frozen=True, slots=True)
class SkillUsage:
    """One Skill's usage in the period (spec 3). `runtime` is `None` only
    when the runtime backend is unavailable; `last_used_at` is not limited
    to the period; `pinned_by_agents` is the current count."""

    by_mode: ModeCounts
    users: int
    satisfaction: float | None
    satisfaction_sample: Literal["ok", "insufficient"]
    last_used_at: datetime | None
    pinned_by_agents: int
    runtime: SkillRuntimeStats | None


@dataclass(frozen=True, slots=True)
class LibrarySummary:
    """Spec 3's library summary. `unused` counts enabled Skills with no
    activation in the period; `auto_share` is `None` without activations;
    `candidate_count` counts enabled Skills applicable to `knowledge.answer`."""

    skill_total: int
    enabled: int
    used_in_period: int
    unused: int
    auto_share: float | None
    candidate_count: int
    candidate_limit: int = CANDIDATE_LIMIT


@dataclass(frozen=True, slots=True)
class SkillDetail:
    """`files` are the latest revision's; `pinned_by` the ids of Agents
    pinning the Skill now. `top_agents` keeps the `tapper_default` and
    `unknown` keys as-is."""

    record: SkillRecord
    usage: SkillUsage
    revisions: tuple[SkillRevision, ...]
    files: tuple[SkillFile, ...]
    pinned_by: tuple[str, ...]
    top_agents: tuple[RankedKey, ...]
    top_callers: tuple[RankedKey, ...]


@dataclass(frozen=True, slots=True)
class UsageSeries:
    """A Skill's trend at the period's granularity, plus its per-mode totals."""

    points: tuple[UsagePoint, ...]
    by_mode: ModeCounts


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class HubView(Generic[_T]):
    """Envelope every Skill Hub query returns (same shape as the Agent team
    view's): runtime status and when the runtime data was computed, the
    echoed period/timezone/granularity, and the view-specific `data`."""

    runtime_status: RuntimeStatus
    computed_at: datetime
    timezone: str
    period: Period
    granularity: str
    data: _T
