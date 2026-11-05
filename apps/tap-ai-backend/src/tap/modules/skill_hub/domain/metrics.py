"""Pure metric-definition functions for the Skill Hub (spec section 3). These
never touch Langfuse or MySQL: `SkillHubService` (`application/service.py`)
supplies every input as a plain value."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from tap.modules.ai.domain.skill_metadata import ActivationMode
from tap.modules.skill_hub.domain.models import LibrarySummary, ModeCounts, SkillUsage
from tap.modules.skill_hub.ports import (
    ActivationCount,
    SkillRatings,
    SkillRecord,
    SkillRuntimeStats,
    UsagePoint,
)
from tap.platform.reporting.satisfaction import satisfaction

# Priority order (manual > pinned > auto), also the display order.
_MODES: tuple[ActivationMode, ...] = ("manual", "pinned", "auto")
_ENABLED = "enabled"
_CANDIDATE_TASK = "knowledge.answer"


def empty_modes() -> ModeCounts:
    return {mode: 0 for mode in _MODES}


def modes_by_skill(counts: Iterable[ActivationCount]) -> dict[str, ModeCounts]:
    """Activation runs per Skill, split by mode; every returned Skill carries
    all three modes."""
    result: dict[str, ModeCounts] = {}
    for count in counts:
        modes = result.setdefault(count.skill_id, empty_modes())
        modes[count.mode] += count.runs
    return result


def activation_total(by_mode: Mapping[ActivationMode, int]) -> int:
    """A Skill's activations: the sum of its three modes."""
    return sum(by_mode.get(mode, 0) for mode in _MODES)


def mode_totals(points: Iterable[UsagePoint]) -> ModeCounts:
    totals = empty_modes()
    for point in points:
        totals[point.mode] += point.runs
    return totals


def build_usage(
    *,
    by_mode: ModeCounts,
    users: int,
    ratings: SkillRatings | None,
    last_used_at: datetime | None,
    pinned_by_agents: int,
    runtime: SkillRuntimeStats | None,
) -> SkillUsage:
    up, down = (ratings.up, ratings.down) if ratings is not None else (0, 0)
    rate, sample = satisfaction(up, down)
    return SkillUsage(
        by_mode=by_mode,
        users=users,
        satisfaction=rate,
        satisfaction_sample=sample,
        last_used_at=last_used_at,
        pinned_by_agents=pinned_by_agents,
        runtime=runtime,
    )


def library_summary(
    records: Sequence[SkillRecord], by_skill: Mapping[str, Mapping[ActivationMode, int]]
) -> LibrarySummary:
    """Spec 3's library summary over the Skills in `records`; activations of
    Skills not in `records` are ignored."""
    enabled = [record for record in records if record.status == _ENABLED]
    totals = {
        record.skill_id: activation_total(by_skill.get(record.skill_id, {})) for record in records
    }
    total_runs = sum(totals.values())
    auto_runs = sum(by_skill.get(record.skill_id, {}).get("auto", 0) for record in records)
    return LibrarySummary(
        skill_total=len(records),
        enabled=len(enabled),
        used_in_period=sum(1 for runs in totals.values() if runs > 0),
        unused=sum(1 for record in enabled if totals[record.skill_id] == 0),
        auto_share=auto_runs / total_runs if total_runs else None,
        candidate_count=sum(1 for record in enabled if _CANDIDATE_TASK in record.applicable_tasks),
    )
