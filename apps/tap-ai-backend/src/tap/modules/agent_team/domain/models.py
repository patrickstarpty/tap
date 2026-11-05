"""Agent team domain model: reporting periods and read models.

`Period` captures a closed `[start, end]` date range used throughout the
Agent team view (spec sections 1.3/2.1/4). It and the other period/runtime
status names now live in `tap.platform.reporting` (shared with Skill Hub) and
are re-exported here unchanged. The codebase has no per-Project timezone
setting today, so `project_timezone` is pinned to `"UTC"`; adding one later
only changes that single function.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from tap.modules.ai.domain.maintenance import MaintenanceKind
from tap.platform.reporting.period import MAX_PERIOD_DATE as MAX_PERIOD_DATE
from tap.platform.reporting.period import MIN_PERIOD_DATE as MIN_PERIOD_DATE
from tap.platform.reporting.period import PERIOD_PRESETS as PERIOD_PRESETS
from tap.platform.reporting.period import Period as Period
from tap.platform.reporting.period import PeriodInvalid as PeriodInvalid
from tap.platform.reporting.period import PeriodPreset as PeriodPreset
from tap.platform.reporting.period import project_timezone as project_timezone
from tap.platform.reporting.period import trailing_30_days as trailing_30_days
from tap.platform.reporting.runtime import RuntimeStatus as RuntimeStatus

if TYPE_CHECKING:
    # Only for type checking: `ports.py` imports this module at runtime, so an
    # eager import here would be circular. Annotations are strings (PEP 563,
    # `from __future__ import annotations` above), so this import never runs.
    from tap.modules.agent_team.ports import ServedActor, TrendPoint

TAPPER_DEFAULT_AGENT_KEY = "tapper_default"
UNKNOWN_AGENT_KEY = "unknown"


def is_pending_feedback(rating: str | None, comment: str | None) -> bool:
    """Spec 2.4: a thumbs-down, or any feedback with a comment (including a
    commented thumbs-up), is pending until resolved. A plain thumbs-up and
    withdrawn feedback (rating and comment both empty) never are."""
    return rating == "down" or comment is not None


# --- Agent team view read models (spec 1.4, 2.2-2.6; Task 7) ---------------
#
# These are the view-layer dataclasses `AgentTeamService`
# (`application/service.py`) returns; field names are the snake_case form of
# the HTTP contract's camelCase fields (Task 8 wires the HTTP layer, unchanged
# here).

AgentKind = Literal["custom", "built_in", "tapper_default"]
MemberStatus = Literal["active", "idle", "disabled"]


@dataclass(frozen=True, slots=True)
class MemberRow:
    """One roster row: Task 1's personnel fields (spec 1.2) merged with
    Task 4/5's runtime metrics (spec 2.2) and Task 6's MySQL-derived
    satisfaction/maintenance/pending-feedback (spec 2.3/2.4).

    `owner_actor_id` is always `None` for `kind in ("built_in",
    "tapper_default")` -- the frontend shows "平台维护" instead (spec 1.1, 6.1).
    `top_served` holds at most the top 3 served actors by run count.

    `status` is `None` for a non-disabled Agent when the runtime backend is
    unavailable (spec 5.1 controller ruling): a disabled Agent's status is
    always `"disabled"` (known from MySQL regardless of Langfuse), but
    "active"/"idle" require the trailing-30-day run count, which an outage
    makes unknown -- reporting "idle" in that case would be a false claim.

    Controller ruling (Task 9 review, fix round 1): every run-derived field
    is `None` -- never `0` -- when the runtime backend is unavailable:
    `runs`, `failures`, `abstained`, `canceled`, `latency_total_ms`,
    `latency_p50_ms`, `cost_usd`, `served_count` (`top_served` becomes an
    empty tuple, `last_run_at` `None`, `trend` empty). Reporting `0` during
    an outage would claim "no runs happened", which is not knowable; `None`
    means "unknown". Satisfaction, pending feedback and maintenance stay
    populated -- they come from MySQL, not the runtime backend."""

    agent_key: str
    name: str
    kind: AgentKind
    job_title: str | None
    owner_actor_id: str | None
    joined_at: datetime | None
    status: MemberStatus | None
    runs: int | None
    failures: int | None
    abstained: int | None
    canceled: int | None
    latency_total_ms: int | None
    latency_p50_ms: int | None
    cost_usd: Decimal | None
    served_count: int | None
    top_served: tuple[ServedActor, ...]
    last_run_at: datetime | None
    satisfaction: float | None
    satisfaction_sample: Literal["ok", "insufficient"]
    last_maintenance_at: datetime | None
    pending_feedback: int
    maintenance: Mapping[MaintenanceKind, int]
    trend: tuple[TrendPoint, ...]
    # Spec 2.2 secondary metrics: model/tool call volume and calls-per-run.
    # `None` for all three when the runtime backend is unavailable (the
    # outage makes them unknown, same reasoning as `status`); when available,
    # `generation_calls`/`tool_calls` are real counts (0 for an Agent with no
    # runs), and `calls_per_run` is additionally `None` when `runs == 0`
    # (nothing to divide by).
    generation_calls: int | None
    tool_calls: int | None
    calls_per_run: float | None


@dataclass(frozen=True, slots=True)
class TeamSummary:
    """Spec 2.6's team-wide summary. `agent_total`/`active`/`idle` cover only
    enabled custom and built-in Agents (never `tapper_default`); totals are
    computed directly from the period's `agent_stats`, never by summing
    trend points (spec 2.1 clips trend buckets at the period's edges).

    `active`/`idle` are `None` when the runtime backend is unavailable --
    both require knowing run counts, which an outage makes unknown
    (`agent_total` stays a real number: it only depends on MySQL's
    `enabled` flag). Per the same ruling, `runs`/`latency_total_ms`/
    `cost_usd`/`served_avg` are also `None` (not `0`) during an outage."""

    agent_total: int
    active: int | None
    idle: int | None
    owners_avg: float
    served_avg: float | None
    runs: int | None
    latency_total_ms: int | None
    cost_usd: Decimal | None
    maintenance_total: int


@dataclass(frozen=True, slots=True)
class PersonRow:
    """Spec 2.5's per-person row. `runs` is this actor's own run count across
    every Agent that served them in the period.

    `owned_runs`/`runs`/`served_by_agents` are `None` (not `0`) when the
    runtime backend is unavailable -- they all depend on run counts an
    outage makes unknown. `owned_agents`/`maintenance` stay populated:
    owning an Agent and making a maintenance change are both MySQL facts,
    independent of the runtime backend."""

    actor_id: str
    owned_agents: int
    owned_runs: int | None
    served_by_agents: int | None
    runs: int | None
    maintenance: int


@dataclass(frozen=True, slots=True)
class OrgChart:
    """Spec 1.2's org chart: `owns` edges (solid, from each Agent's current
    owner) and `serves` edges (dashed, from everyone who called that Agent in
    the period). Built-in and `tapper_default` Agents carry no `owns` edge."""

    people: tuple[str, ...]
    agents: tuple[str, ...]
    edges: tuple[tuple[str, str, Literal["owns", "serves"]], ...]


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class TeamView(Generic[_T]):
    """Envelope every Agent team query returns (spec 5): the Langfuse-derived
    `runtime_status`/`computed_at` (spec 5.1), the echoed period/timezone/
    granularity (spec 2.1), and the view-specific `data`."""

    runtime_status: RuntimeStatus
    computed_at: datetime
    timezone: str
    period: Period
    granularity: str
    data: _T
