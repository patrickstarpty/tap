"""Ports for the MySQL-backed Skill Hub read models and writes (spec 3/4):
the Skill library records, usage counts (activations by mode, users,
ratings, last use, Agents pinning a Skill), per-Skill trend and rankings,
and the two writes (category, owner transfer)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

from tap.modules.ai.domain.skill_metadata import ActivationMode, OriginKind, SkillCategory
from tap.modules.skill_hub.domain.models import SkillMaintenanceEvent
from tap.platform.reporting.period import Period

Granularity = Literal["day", "week", "month"]


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """A Skill and its latest revision. `version` is already the display
    version (`metadata.version` SemVer label, else `r<revision_number>`)."""

    skill_id: str
    name: str
    description: str | None
    category: SkillCategory
    version: str
    latest_revision_id: str
    revision_number: int
    source_kind: str
    origin_kind: OriginKind
    origin_ref: str | None
    origin_imported_at: datetime | None
    owner_actor_id: str
    owner_assigned_at: datetime
    status: str
    # Sorted ascending.
    applicable_tasks: tuple[str, ...]
    compatibility: str | None
    updated_at: datetime
    built_in: bool


@dataclass(frozen=True, slots=True)
class ActivationCount:
    skill_id: str
    mode: ActivationMode
    runs: int


@dataclass(frozen=True, slots=True)
class SkillRatings:
    skill_id: str
    up: int
    down: int


@dataclass(frozen=True, slots=True)
class UsagePoint:
    bucket_start: date
    mode: ActivationMode
    runs: int


@dataclass(frozen=True, slots=True)
class RankedKey:
    key: str
    runs: int


class SkillHubStore(Protocol):
    """MySQL read models and writes for the Skill Hub. Every query is scoped
    to the Project the store was constructed with. Usage counts fall in a
    period by `activated_at`; ratings by the rated Turn's `created_at`.
    Returned datetimes are timezone-aware UTC."""

    async def skills(self) -> list[SkillRecord]: ...

    async def skill(self, skill_id: str) -> SkillRecord | None: ...

    async def activation_counts(self, period: Period) -> list[ActivationCount]: ...

    async def distinct_users(self, period: Period) -> dict[str, int]:
        """Distinct `chat_turn.actor_id` per Skill; unused Skills are absent."""
        ...

    async def ratings(self, period: Period) -> list[SkillRatings]:
        """Raw up/down counts per Skill; the satisfaction threshold lives in
        `satisfaction()`, not here."""
        ...

    async def last_used(self) -> dict[str, datetime]:
        """The latest `activated_at` per Skill, regardless of period."""
        ...

    async def pinned_by(self) -> dict[str, tuple[str, ...]]:
        """Skill id -> ids (ascending) of Agents whose latest revision is
        enabled and lists the Skill in `pinned_skill_ids`."""
        ...

    async def usage_trend(
        self, skill_id: str, period: Period, granularity: Granularity
    ) -> list[UsagePoint]:
        """Runs per (bucket, mode), ordered by bucket then mode; UTC buckets,
        weeks start on Monday. Empty buckets are absent."""
        ...

    async def top_agents(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        """Agent keys by runs (descending, then key); Turns without an Agent
        count as `tapper_default`."""
        ...

    async def top_callers(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        """Turn `actor_id`s by runs (descending, then actor id)."""
        ...

    async def actor_exists(self, actor_id: str) -> bool: ...

    async def set_category(self, skill_id: str, category: SkillCategory) -> SkillRecord:
        """Raises `LookupError` when the Skill is not in this Project."""
        ...

    async def transfer_owner(
        self, skill_id: str, new_owner: str, event: SkillMaintenanceEvent
    ) -> SkillRecord:
        """Sets the owner (assigned at `event.occurred_at`) and records
        `event` in the same transaction. Raises `LookupError` when the Skill
        is not in this Project."""
        ...


@dataclass(frozen=True, slots=True)
class SkillRuntimeStats:
    """Secondary metrics of the runs that used one Skill (spec 3): a run
    using several Skills counts once for each of them."""

    skill_id: str
    runs: int
    failures: int
    latency_total_ms: int
    cost_usd: Decimal


class SkillRuntimeMetrics(Protocol):
    """Runtime (Langfuse-backed) per-Skill stats over a period, by the
    `skill:<skill_id>` trace tags. Raises `RuntimeMetricsUnavailable` when
    the backend cannot answer."""

    async def skill_stats(self, project_id: str, period: Period) -> list[SkillRuntimeStats]: ...
