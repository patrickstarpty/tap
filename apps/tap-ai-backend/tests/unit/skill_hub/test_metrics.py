"""Unit tests for the Skill Hub's pure metric definitions (Task 9,
2026-11-01 Skill Hub phase 1, spec section 3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from tap.modules.skill_hub.domain import metrics
from tap.modules.skill_hub.ports import (
    ActivationCount,
    SkillRatings,
    SkillRecord,
    SkillRuntimeStats,
    UsagePoint,
)

_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _record(
    skill_id: str,
    *,
    status: str = "enabled",
    applicable_tasks: tuple[str, ...] = ("knowledge.answer",),
) -> SkillRecord:
    return SkillRecord(
        skill_id=skill_id,
        name=skill_id,
        description=None,
        category="other",
        version="r1",
        latest_revision_id=f"{skill_id}-r1",
        revision_number=1,
        source_kind="authored",
        origin_kind="authored",
        origin_ref=None,
        origin_imported_at=None,
        owner_actor_id="owner-1",
        owner_assigned_at=_NOW,
        status=status,
        applicable_tasks=applicable_tasks,
        compatibility=None,
        updated_at=_NOW,
        built_in=False,
    )


def test_activation_total_is_sum_of_modes() -> None:
    by_skill = metrics.modes_by_skill(
        [
            ActivationCount("s1", "manual", 2),
            ActivationCount("s1", "pinned", 1),
            ActivationCount("s1", "auto", 3),
            ActivationCount("s2", "auto", 1),
        ]
    )

    assert by_skill["s1"] == {"manual": 2, "pinned": 1, "auto": 3}
    assert metrics.activation_total(by_skill["s1"]) == 6
    assert by_skill["s2"] == {"manual": 0, "pinned": 0, "auto": 1}
    assert metrics.activation_total(by_skill.get("s3", metrics.empty_modes())) == 0


def test_satisfaction_below_10_ratings_is_insufficient() -> None:
    def usage(up: int, down: int):
        return metrics.build_usage(
            by_mode=metrics.empty_modes(),
            users=0,
            ratings=SkillRatings("s1", up=up, down=down),
            last_used_at=None,
            pinned_by_agents=0,
            runtime=None,
        )

    nine = usage(5, 4)
    assert (nine.satisfaction, nine.satisfaction_sample) == (None, "insufficient")
    ten = usage(7, 3)
    assert (ten.satisfaction, ten.satisfaction_sample) == (0.7, "ok")
    unrated = metrics.build_usage(
        by_mode=metrics.empty_modes(),
        users=0,
        ratings=None,
        last_used_at=None,
        pinned_by_agents=0,
        runtime=None,
    )
    assert (unrated.satisfaction, unrated.satisfaction_sample) == (None, "insufficient")


def test_build_usage_passes_runtime_and_counts_through() -> None:
    runtime = SkillRuntimeStats("s1", runs=3, failures=1, latency_total_ms=90, cost_usd=Decimal(1))
    usage = metrics.build_usage(
        by_mode={"manual": 1, "pinned": 0, "auto": 2},
        users=2,
        ratings=None,
        last_used_at=_NOW,
        pinned_by_agents=4,
        runtime=runtime,
    )

    assert usage.by_mode == {"manual": 1, "pinned": 0, "auto": 2}
    assert (usage.users, usage.last_used_at, usage.pinned_by_agents) == (2, _NOW, 4)
    assert usage.runtime is runtime


def test_auto_share_is_none_without_activations() -> None:
    records = [_record("s1"), _record("s2")]

    assert metrics.library_summary(records, {}).auto_share is None
    shared = metrics.library_summary(
        records,
        {
            "s1": {"manual": 1, "pinned": 0, "auto": 1},
            "s2": {"manual": 0, "pinned": 1, "auto": 1},
        },
    )
    assert shared.auto_share == 0.5


def test_unused_counts_enabled_skills_with_zero_activations() -> None:
    records = [
        _record("s-used"),
        _record("s-idle"),
        _record("s-idle-test-only", applicable_tasks=("test-plan.generate",)),
        _record("s-off", status="disabled"),
    ]
    summary = metrics.library_summary(
        records,
        {
            "s-used": {"manual": 0, "pinned": 0, "auto": 2},
            # Activations recorded before the Skill was disabled still count
            # as "used in period", never as "unused".
            "s-off": {"manual": 1, "pinned": 0, "auto": 0},
            # Activations of a Skill no longer in the library are ignored.
            "s-gone": {"manual": 9, "pinned": 0, "auto": 9},
        },
    )

    assert summary.skill_total == 4
    assert summary.enabled == 3
    assert summary.used_in_period == 2
    assert summary.unused == 2
    assert summary.auto_share == 2 / 3
    assert summary.candidate_count == 2
    assert summary.candidate_limit == 60


def test_mode_totals_sum_points_per_mode() -> None:
    points = [
        UsagePoint(date(2026, 11, 1), "manual", 2),
        UsagePoint(date(2026, 11, 1), "auto", 1),
        UsagePoint(date(2026, 11, 2), "auto", 4),
    ]

    assert metrics.mode_totals(points) == {"manual": 2, "pinned": 0, "auto": 5}
