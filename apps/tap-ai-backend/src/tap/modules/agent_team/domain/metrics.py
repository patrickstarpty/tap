"""Pure metric-definition functions for the Agent team view (spec sections
1.4, 2.2-2.6). These never touch Langfuse or MySQL: `AgentTeamService`
(`application/service.py`) is the only caller and supplies every input as a
plain value, so these are unit-testable without fakes or a database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal

from tap.modules.agent_team.domain.models import (
    TAPPER_DEFAULT_AGENT_KEY,
    UNKNOWN_AGENT_KEY,
    AgentKind,
    MaintenanceKind,
    MemberRow,
    MemberStatus,
    OrgChart,
    PersonRow,
    TeamSummary,
)
from tap.modules.agent_team.ports import AgentRuntimeStats, ServedActor, TrendPoint

# Spec 2.3: fewer than 10 rated turns is "sample insufficient".
_MIN_RATED_TURNS_FOR_SATISFACTION = 10
# Spec 1.2/6.1: cards show at most the top 3 served actors.
_TOP_SERVED_LIMIT = 3


def member_status(*, disabled: bool, trailing_30d_runs: int | None) -> MemberStatus | None:
    """Spec 1.4: disabled takes precedence over active/idle; active means at
    least 1 run in the trailing 30 days ending today, independent of the
    selected period.

    `trailing_30d_runs=None` means the run count is unknown (runtime backend
    unavailable) -- the caller must not substitute 0, because that would
    report "idle" when the truth might be "active" (controller ruling: never
    claim idle during an outage). `disabled` always wins regardless, since
    it comes from MySQL, not the runtime backend."""
    if disabled:
        return "disabled"
    if trailing_30d_runs is None:
        return None
    if trailing_30d_runs >= 1:
        return "active"
    return "idle"


def satisfaction(up: int, down: int) -> tuple[float | None, Literal["ok", "insufficient"]]:
    """Spec 2.3: `up / (up + down)`; fewer than 10 rated turns reports no
    value and `"insufficient"`."""
    total = up + down
    if total < _MIN_RATED_TURNS_FOR_SATISFACTION:
        return None, "insufficient"
    return up / total, "ok"


def roster_agent_keys(
    *,
    store_agent_keys: Iterable[str],
    tapper_default_runs_in_period: int,
    tapper_default_runs_trailing_30d: int,
    tapper_default_pending_feedback: int = 0,
    runtime_available: bool = True,
) -> frozenset[str]:
    """Spec 1.1: members are `store.agents()` plus `tapper_default` when it
    ran in the selected period or the trailing 30 days, or still has pending
    feedback (otherwise that feedback would be unreachable); `unknown` never
    names a roster member and is always dropped.

    When the runtime backend is unavailable (`runtime_available=False`),
    whether `tapper_default` ran cannot be known, so it is always included
    rather than silently dropped (controller ruling)."""
    keys = {key for key in store_agent_keys if key != UNKNOWN_AGENT_KEY}
    if (
        not runtime_available
        or tapper_default_runs_in_period >= 1
        or tapper_default_runs_trailing_30d >= 1
        or tapper_default_pending_feedback >= 1
    ):
        keys.add(TAPPER_DEFAULT_AGENT_KEY)
    return frozenset(keys)


def build_member_row(
    *,
    agent_key: str,
    name: str,
    kind: AgentKind,
    job_title: str | None,
    owner_actor_id: str | None,
    joined_at: datetime | None,
    disabled: bool,
    trailing_30d_runs: int | None,
    stats: AgentRuntimeStats | None,
    last_run_at: datetime | None,
    up: int,
    down: int,
    last_maintenance_at: datetime | None,
    pending_feedback: int,
    maintenance: Mapping[MaintenanceKind, int],
    trend: tuple[TrendPoint, ...],
    runtime_available: bool = True,
) -> MemberRow:
    """Assembles one roster row.

    Controller ruling (Task 9 review, fix round 1): when `runtime_available`
    is `False`, every run-derived field is `None` -- never `0` -- because an
    outage makes the true count unknown, not zero: `runs`, `failures`,
    `abstained`, `canceled`, `latency_total_ms`, `latency_p50_ms`,
    `cost_usd`, `served_count`, `generation_calls`, `tool_calls`,
    `calls_per_run`; `top_served` becomes an empty tuple, `last_run_at`
    `None`, `trend` empty. The MySQL-derived fields (roster, owner,
    satisfaction, maintenance, pending feedback) are unaffected.

    When `runtime_available` is `True`, `stats` is `None` only because this
    Agent had no runs in the period, so those same fields report a real
    `0`/`Decimal("0")` instead."""
    status = member_status(disabled=disabled, trailing_30d_runs=trailing_30d_runs)
    satisfaction_value, satisfaction_sample = satisfaction(up, down)

    runs: int | None
    failures: int | None
    abstained: int | None
    canceled: int | None
    latency_total_ms: int | None
    latency_p50_ms: int | None
    cost_usd: Decimal | None
    served_count: int | None
    top_served: tuple[ServedActor, ...]
    generation_calls: int | None
    tool_calls: int | None
    calls_per_run: float | None
    if runtime_available:
        runs = stats.runs if stats else 0
        failures = stats.failures if stats else 0
        abstained = stats.abstained if stats else 0
        canceled = stats.canceled if stats else 0
        latency_total_ms = stats.latency_total_ms if stats else 0
        latency_p50_ms = stats.latency_p50_ms if stats else None
        cost_usd = stats.cost_usd if stats else Decimal("0")
        served_count = len(stats.served) if stats else 0
        # `AgentRuntimeStats.served` is already sorted by runs desc, actor_id asc.
        top_served = stats.served[:_TOP_SERVED_LIMIT] if stats else ()
        generation_calls = stats.generation_calls if stats else 0
        tool_calls = stats.tool_calls if stats else 0
        calls_per_run = (generation_calls + tool_calls) / runs if runs else None
    else:
        runs = None
        failures = None
        abstained = None
        canceled = None
        latency_total_ms = None
        latency_p50_ms = None
        cost_usd = None
        served_count = None
        top_served = ()
        generation_calls = None
        tool_calls = None
        calls_per_run = None
    return MemberRow(
        agent_key=agent_key,
        name=name,
        kind=kind,
        job_title=job_title,
        # Spec 1.1: built-in and tapper_default show "平台维护", never an owner.
        owner_actor_id=None if kind in ("built_in", "tapper_default") else owner_actor_id,
        joined_at=joined_at,
        status=status,
        runs=runs,
        failures=failures,
        abstained=abstained,
        canceled=canceled,
        latency_total_ms=latency_total_ms,
        latency_p50_ms=latency_p50_ms,
        cost_usd=cost_usd,
        served_count=served_count,
        top_served=top_served,
        last_run_at=last_run_at if runtime_available else None,
        satisfaction=satisfaction_value,
        satisfaction_sample=satisfaction_sample,
        last_maintenance_at=last_maintenance_at,
        pending_feedback=pending_feedback,
        maintenance=dict(maintenance),
        trend=trend if runtime_available else (),
        generation_calls=generation_calls,
        tool_calls=tool_calls,
        calls_per_run=calls_per_run,
    )


def team_summary(
    *,
    stats_by_key: Mapping[str, AgentRuntimeStats],
    kinds_by_key: Mapping[str, AgentKind],
    statuses_by_key: Mapping[str, MemberStatus | None],
    person_rows: Sequence[PersonRow],
    maintenance_total: int,
    runtime_available: bool = True,
) -> TeamSummary:
    """Spec 2.6. `stats_by_key` must already be restricted to this period's
    roster (never `tapper_default`/`unknown` keys bleeding in). Global
    constraints: `agent_total`/`active`/`idle` only ever count *enabled*
    custom and built-in Agents -- a disabled Agent counts toward neither.

    `active`/`idle` are `None` when `runtime_available` is `False`: both
    need run counts the outage makes unknown. `agent_total` is unaffected --
    it only depends on MySQL's `enabled` flag, and a disabled Agent's status
    is always `"disabled"` (never `None`) regardless of the outage, so the
    `!= "disabled"` filter below still excludes it correctly.

    Controller ruling (Task 9 review, fix round 1): `runs`/`latency_total_ms`/
    `cost_usd` are likewise `None` -- never `0`/`Decimal("0")` -- when
    `runtime_available` is `False`, and so is `served_avg` (review fix 4).

    Spec 2.6 (2026-10-27): `owners_avg` is Agents per owner (denominator:
    people owning at least one Agent) and `served_avg` is Agents used per
    caller (denominator: people with runs in the period); each is `0.0`
    when its denominator is empty."""
    countable = [
        key
        for key, kind in kinds_by_key.items()
        if kind in ("custom", "built_in") and statuses_by_key.get(key) != "disabled"
    ]
    agent_total = len(countable)
    active: int | None
    idle: int | None
    runs: int | None
    latency_total_ms: int | None
    cost_usd: Decimal | None
    if runtime_available:
        active = sum(
            1
            for key in countable
            if stats_by_key.get(key) is not None and stats_by_key[key].runs >= 1
        )
        idle = sum(1 for key in countable if statuses_by_key.get(key) == "idle")
        runs = sum(stats.runs for stats in stats_by_key.values())
        latency_total_ms = sum(stats.latency_total_ms for stats in stats_by_key.values())
        cost_usd = sum((stats.cost_usd for stats in stats_by_key.values()), Decimal("0"))
    else:
        active = None
        idle = None
        runs = None
        latency_total_ms = None
        cost_usd = None
    owners = [row for row in person_rows if row.owned_agents > 0]
    owners_avg = sum(row.owned_agents for row in owners) / len(owners) if owners else 0.0
    # `owners_avg` is a MySQL fact; `served_avg` needs Langfuse's callers and
    # their run counts, which an outage makes unknown -> `None`, not `0`.
    served_avg: float | None
    if runtime_available:
        callers = [row for row in person_rows if (row.runs or 0) > 0]
        served_avg = (
            sum((row.served_by_agents or 0) for row in callers) / len(callers) if callers else 0.0
        )
    else:
        served_avg = None
    return TeamSummary(
        agent_total=agent_total,
        active=active,
        idle=idle,
        owners_avg=owners_avg,
        served_avg=served_avg,
        runs=runs,
        latency_total_ms=latency_total_ms,
        cost_usd=cost_usd,
        maintenance_total=maintenance_total,
    )


def people_scope(
    *,
    owners: Iterable[str],
    active_actors: Iterable[str],
    runtime_callers: Iterable[str] = (),
) -> frozenset[str]:
    """Spec 2.5: all current owners union everyone who called an Agent or
    made a maintenance change in the period. `active_actors` is MySQL's view
    of callers and maintainers (`ports.AgentTeamStore.active_actors`);
    `runtime_callers` adds Langfuse's callers, the same source as each
    Agent's served list (empty when the runtime backend is unavailable)."""
    return frozenset(owners) | frozenset(active_actors) | frozenset(runtime_callers)


def build_person_row(
    *,
    actor_id: str,
    owned_agent_keys: Iterable[str],
    stats_by_key: Mapping[str, AgentRuntimeStats],
    maintenance_count: int,
    runtime_available: bool = True,
) -> PersonRow:
    """Spec 2.5's per-person row. `owned_agent_keys` is this actor's
    currently-owned Agents; `stats_by_key` must already be restricted to
    this period's roster (never `unknown`/dropped keys bleeding in, the
    same restriction `team_summary` requires) -- used both for `owned_runs`
    (agents they own) and `served_by_agents`/`runs` (agents that served
    them, found by scanning each Agent's `served` list for this actor), so
    that `served_by_agents` agrees with `AgentTeamService.person`'s own
    `served` list, which is built from the same roster.

    Controller ruling (Task 9 review, fix round 1): `owned_runs`/
    `served_by_agents`/`runs` are `None` -- never `0` -- when
    `runtime_available` is `False`; `owned_agents`/`maintenance` stay
    populated (MySQL facts, independent of the runtime backend)."""
    owned_keys = list(owned_agent_keys)
    owned_runs: int | None
    served_by_agents: int | None
    runs: int | None
    if runtime_available:
        owned_runs = sum(stats_by_key[key].runs for key in owned_keys if key in stats_by_key)
        served_by_agents = 0
        runs = 0
        for stats in stats_by_key.values():
            served_runs = next((s.runs for s in stats.served if s.actor_id == actor_id), 0)
            if served_runs > 0:
                served_by_agents += 1
                runs += served_runs
    else:
        owned_runs = None
        served_by_agents = None
        runs = None
    return PersonRow(
        actor_id=actor_id,
        owned_agents=len(owned_keys),
        owned_runs=owned_runs,
        served_by_agents=served_by_agents,
        runs=runs,
        maintenance=maintenance_count,
    )


def build_org_chart(
    *,
    members: Sequence[MemberRow],
    served_by_key: Mapping[str, tuple[ServedActor, ...]],
) -> OrgChart:
    """Spec 1.2: a solid `owns` edge from each Agent's current owner (none
    for built-in/`tapper_default` Agents -- `MemberRow.owner_actor_id` is
    already `None` for those), and a dashed `serves` edge from everyone who
    called that Agent in the period."""
    people: set[str] = set()
    agents: list[str] = []
    edges: list[tuple[str, str, Literal["owns", "serves"]]] = []
    for member in members:
        agents.append(member.agent_key)
        if member.owner_actor_id is not None:
            people.add(member.owner_actor_id)
            edges.append((member.owner_actor_id, member.agent_key, "owns"))
        for served in served_by_key.get(member.agent_key, ()):
            if served.runs <= 0:
                continue
            people.add(served.actor_id)
            edges.append((served.actor_id, member.agent_key, "serves"))
    return OrgChart(people=tuple(sorted(people)), agents=tuple(agents), edges=tuple(edges))
