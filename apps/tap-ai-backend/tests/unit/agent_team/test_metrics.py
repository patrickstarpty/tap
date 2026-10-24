"""Unit tests for the Agent team view's pure metric-definition functions
(Task 7, 2026-10-23 agent team view): status (spec 1.4), satisfaction (spec
2.3), roster membership (spec 1.1), team summary (spec 2.6), people (spec
2.5) and the org chart (spec 1.2)."""

from __future__ import annotations

from decimal import Decimal

from tap.modules.agent_team.domain.metrics import (
    build_member_row,
    build_org_chart,
    build_person_row,
    member_status,
    people_scope,
    roster_agent_keys,
    satisfaction,
    team_summary,
)
from tap.modules.agent_team.domain.models import TAPPER_DEFAULT_AGENT_KEY, PersonRow
from tap.modules.agent_team.ports import AgentRuntimeStats, ServedActor


def _stats(
    agent_key: str,
    runs: int,
    served: tuple[ServedActor, ...] = (),
    *,
    generation_calls: int = 0,
    tool_calls: int = 0,
) -> AgentRuntimeStats:
    return AgentRuntimeStats(
        agent_key=agent_key,
        runs=runs,
        failures=0,
        abstained=0,
        canceled=0,
        latency_total_ms=runs * 10,
        latency_p50_ms=None,
        cost_usd=Decimal("1.00") * runs,
        served=served,
        generation_calls=generation_calls,
        tool_calls=tool_calls,
    )


def _build_row(*, stats, runtime_available: bool = True):
    return build_member_row(
        agent_key="agent-1",
        name="Agent 1",
        kind="custom",
        job_title=None,
        owner_actor_id="actor-1",
        joined_at=None,
        disabled=False,
        trailing_30d_runs=0 if runtime_available else None,
        stats=stats,
        last_run_at=None,
        up=0,
        down=0,
        last_maintenance_at=None,
        pending_feedback=0,
        maintenance={},
        trend=(),
        runtime_available=runtime_available,
    )


def test_status_precedence():
    # disabled 优先于 active
    assert member_status(disabled=True, trailing_30d_runs=5) == "disabled"
    # 启用且截至今天近 30 天 0 次 -> idle
    assert member_status(disabled=False, trailing_30d_runs=0) == "idle"
    assert member_status(disabled=False, trailing_30d_runs=1) == "active"


def test_disabled_agent_with_runs_stays_disabled():
    assert member_status(disabled=True, trailing_30d_runs=100) == "disabled"


def test_status_ignores_selected_period():
    # 周期为上月有运行，截至今天近 30 天无运行 -> idle；汇总活跃数仍按所选周期计 1。
    row = build_member_row(
        agent_key="agent-1",
        name="Agent 1",
        kind="custom",
        job_title=None,
        owner_actor_id="actor-1",
        joined_at=None,
        disabled=False,
        trailing_30d_runs=0,
        stats=_stats("agent-1", runs=3),
        last_run_at=None,
        up=0,
        down=0,
        last_maintenance_at=None,
        pending_feedback=0,
        maintenance={},
        trend=(),
    )
    assert row.status == "idle"
    summary = team_summary(
        stats_by_key={"agent-1": _stats("agent-1", runs=3)},
        kinds_by_key={"agent-1": "custom"},
        statuses_by_key={"agent-1": "idle"},
        person_rows=[],
        maintenance_total=0,
    )
    assert summary.active == 1
    assert summary.idle == 1


def test_calls_per_run_divides_call_volume_by_runs():
    row = _build_row(stats=_stats("agent-1", runs=5, generation_calls=8, tool_calls=2))
    assert row.generation_calls == 8
    assert row.tool_calls == 2
    assert row.calls_per_run == 2.0


def test_calls_per_run_is_none_when_runs_is_zero():
    row = _build_row(stats=_stats("agent-1", runs=0, generation_calls=0, tool_calls=0))
    assert row.runs == 0
    assert row.generation_calls == 0
    assert row.tool_calls == 0
    assert row.calls_per_run is None


def test_calls_per_run_is_none_with_no_stats_and_runtime_available():
    row = _build_row(stats=None)
    assert row.runs == 0
    assert row.generation_calls == 0
    assert row.tool_calls == 0
    assert row.calls_per_run is None


def test_call_volume_fields_are_none_when_runtime_unavailable():
    row = _build_row(stats=None, runtime_available=False)
    assert row.generation_calls is None
    assert row.tool_calls is None
    assert row.calls_per_run is None


def test_people_scope_is_owners_callers_and_maintainers():
    # `store.active_actors` already covers callers and maintainers (spec 2.5).
    active_actors = {"owner-1", "caller-2", "maintainer-3"}
    assert people_scope(owners={"owner-1"}, active_actors=active_actors) == {
        "owner-1",
        "caller-2",
        "maintainer-3",
    }


def test_satisfaction_below_10_ratings_is_insufficient():
    value, sample = satisfaction(up=8, down=1)
    assert value is None
    assert sample == "insufficient"
    value, sample = satisfaction(up=7, down=3)
    assert value == 0.7
    assert sample == "ok"


def test_summary_excludes_tapper_default_and_disabled():
    stats_by_key = {
        "agent-1": _stats("agent-1", runs=5),
        "agent-2": _stats("agent-2", runs=2),
        TAPPER_DEFAULT_AGENT_KEY: _stats(TAPPER_DEFAULT_AGENT_KEY, runs=100),
    }
    kinds_by_key = {
        "agent-1": "custom",
        "agent-2": "custom",
        TAPPER_DEFAULT_AGENT_KEY: "tapper_default",
    }
    statuses_by_key = {
        "agent-1": "active",
        "agent-2": "disabled",
        TAPPER_DEFAULT_AGENT_KEY: "active",
    }
    summary = team_summary(
        stats_by_key=stats_by_key,
        kinds_by_key=kinds_by_key,
        statuses_by_key=statuses_by_key,
        person_rows=[],
        maintenance_total=0,
    )
    # agent_total/active only count *enabled* custom+built_in Agents (global
    # constraints: "汇总 Agent 总数只计启用中的..."); disabled agent-2 and
    # tapper_default never count toward agent_total/active.
    assert summary.agent_total == 1
    assert summary.active == 1
    # Totals (runs) still include every key the caller passed in, including
    # tapper_default -- callers are responsible for scoping stats_by_key to
    # the roster they want totaled.
    assert summary.runs == 5 + 2 + 100


def test_summary_totals_ignore_trend_granularity():
    # Summary totals come from `agent_stats`, never from summing trend points
    # (trend buckets are clipped at period edges, spec 2.1).
    stats_by_key = {"agent-1": _stats("agent-1", runs=7)}
    summary = team_summary(
        stats_by_key=stats_by_key,
        kinds_by_key={"agent-1": "custom"},
        statuses_by_key={"agent-1": "active"},
        person_rows=[],
        maintenance_total=0,
    )
    assert summary.runs == 7
    assert summary.latency_total_ms == 70


def test_people_counts_owned_and_served():
    stats_by_key = {
        "agent-1": _stats(
            "agent-1", runs=5, served=(ServedActor("actor-x", 3), ServedActor("actor-y", 2))
        ),
        "agent-2": _stats("agent-2", runs=4, served=(ServedActor("actor-x", 4),)),
    }
    row = build_person_row(
        actor_id="actor-x",
        owned_agent_keys=["agent-1"],
        stats_by_key=stats_by_key,
        maintenance_count=2,
    )
    assert row == PersonRow(
        actor_id="actor-x", owned_agents=1, owned_runs=5, served_by_agents=2, runs=7, maintenance=2
    )


def test_org_chart_edges_owns_and_serves():
    members = [
        build_member_row(
            agent_key="agent-1",
            name="Agent 1",
            kind="custom",
            job_title=None,
            owner_actor_id="owner-1",
            joined_at=None,
            disabled=False,
            trailing_30d_runs=1,
            stats=_stats("agent-1", runs=1),
            last_run_at=None,
            up=0,
            down=0,
            last_maintenance_at=None,
            pending_feedback=0,
            maintenance={},
            trend=(),
        ),
        build_member_row(
            agent_key=TAPPER_DEFAULT_AGENT_KEY,
            name="Tapper 默认",
            kind="tapper_default",
            job_title=None,
            owner_actor_id="should-be-dropped",
            joined_at=None,
            disabled=False,
            trailing_30d_runs=1,
            stats=_stats(TAPPER_DEFAULT_AGENT_KEY, runs=1),
            last_run_at=None,
            up=0,
            down=0,
            last_maintenance_at=None,
            pending_feedback=0,
            maintenance={},
            trend=(),
        ),
    ]
    chart = build_org_chart(
        members=members,
        served_by_key={
            "agent-1": (ServedActor("caller-1", 2),),
            TAPPER_DEFAULT_AGENT_KEY: (ServedActor("caller-2", 1),),
        },
    )
    assert ("owner-1", "agent-1", "owns") in chart.edges
    assert ("caller-1", "agent-1", "serves") in chart.edges
    assert ("caller-2", TAPPER_DEFAULT_AGENT_KEY, "serves") in chart.edges
    # Built-in/tapper_default Agents carry no owns edge.
    assert not any(
        edge[2] == "owns" and edge[1] == TAPPER_DEFAULT_AGENT_KEY for edge in chart.edges
    )


def test_unknown_agent_key_is_dropped_from_roster():
    keys = roster_agent_keys(
        store_agent_keys=["agent-1", "unknown"],
        tapper_default_runs_in_period=0,
        tapper_default_runs_trailing_30d=0,
    )
    assert keys == {"agent-1"}


def test_roster_always_includes_tapper_default_when_runtime_unavailable():
    # Whether tapper_default ran cannot be known during an outage, so it is
    # always included rather than silently dropped (controller ruling).
    keys = roster_agent_keys(
        store_agent_keys=["agent-1"],
        tapper_default_runs_in_period=0,
        tapper_default_runs_trailing_30d=0,
        runtime_available=False,
    )
    assert keys == {"agent-1", TAPPER_DEFAULT_AGENT_KEY}


def test_owners_avg_and_served_avg_divide_by_full_people_scope():
    # Review fix round 1, finding 1: divide by the whole spec-2.5 people
    # scope (`len(person_rows)`), not only people with a nonzero count.
    # 2 people owning [2, 0] -> average 1.0, not 2.0.
    person_rows = [
        PersonRow(
            actor_id="owner-1",
            owned_agents=2,
            owned_runs=0,
            served_by_agents=0,
            runs=0,
            maintenance=0,
        ),
        PersonRow(
            actor_id="caller-1",
            owned_agents=0,
            owned_runs=0,
            served_by_agents=0,
            runs=0,
            maintenance=0,
        ),
    ]
    summary = team_summary(
        stats_by_key={},
        kinds_by_key={},
        statuses_by_key={},
        person_rows=person_rows,
        maintenance_total=0,
    )
    assert summary.owners_avg == 1.0


def test_owners_avg_is_zero_for_empty_people_scope():
    summary = team_summary(
        stats_by_key={},
        kinds_by_key={},
        statuses_by_key={},
        person_rows=[],
        maintenance_total=0,
    )
    assert summary.owners_avg == 0.0
    assert summary.served_avg == 0.0


def test_summary_active_and_idle_are_none_when_runtime_unavailable():
    # Review fix round 1, finding 3: never claim "idle" during an outage.
    summary = team_summary(
        stats_by_key={},
        kinds_by_key={"agent-1": "custom"},
        statuses_by_key={"agent-1": None},
        person_rows=[],
        maintenance_total=0,
        runtime_available=False,
    )
    assert summary.agent_total == 1
    assert summary.active is None
    assert summary.idle is None


def test_member_status_is_none_when_trailing_runs_unknown():
    assert member_status(disabled=False, trailing_30d_runs=None) is None
    # A disabled Agent's status is known regardless of the outage.
    assert member_status(disabled=True, trailing_30d_runs=None) == "disabled"


def test_build_person_row_ignores_stats_outside_the_given_roster():
    # Review fix round 1, finding 4: callers must restrict `stats_by_key` to
    # the roster (dropping `unknown`) before calling this -- it has no way
    # to know a key is out of scope on its own, so this test documents the
    # contract by only ever passing roster-restricted input.
    stats_by_key = {"agent-1": _stats("agent-1", runs=3, served=(ServedActor("actor-x", 3),))}
    row = build_person_row(
        actor_id="actor-x",
        owned_agent_keys=[],
        stats_by_key=stats_by_key,
        maintenance_count=0,
    )
    assert row.served_by_agents == 1
    assert row.runs == 3
