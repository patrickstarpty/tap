"""Deterministic capability matrix and fail-closed runtime gate."""

from scripts.copilot_probe.models import CapabilityResult

REQUIRED_CAPABILITIES = frozenset(
    {
        "byok_all_calls",
        "child_identity",
        "parallel",
        "permissions",
        "control_receipt",
        "resume_workspace",
        "reconcile",
    }
)
CAPABILITIES = (
    "simple",
    "byok_all_calls",
    "child_identity",
    "parallel",
    "permissions",
    "control_receipt",
    "resume_workspace",
    "reconcile",
    "roster_scale",
    "config_freeze",
)


def capability_matrix(
    results: tuple[CapabilityResult, ...],
) -> dict[str, CapabilityResult]:
    """Keep each known scenario visible; conflicting records stay unverified."""
    matrix = {
        name: CapabilityResult(
            name,
            "unverified",
            (),
            "真实场景尚未执行；等待隔离 BYOK 配置及可信计数/终态",
        )
        for name in CAPABILITIES
    }
    seen: set[str] = set()
    for result in results:
        if result.name not in matrix:
            continue
        if result.name in seen:
            matrix[result.name] = CapabilityResult(
                result.name, "unverified", (), "同一能力出现重复或冲突结果"
            )
        else:
            matrix[result.name] = result
            seen.add(result.name)
    return matrix


def ready_for_runtime(
    results: tuple[CapabilityResult, ...], required: frozenset[str]
) -> bool:
    """Require unique, supported, evidence-backed results for the fixed core."""
    required_names = REQUIRED_CAPABILITIES | required
    if not required_names:
        return False
    names = [result.name for result in results]
    if len(names) != len(set(names)):
        return False
    by_name = {result.name: result for result in results}
    return all(
        name in by_name
        and by_name[name].status == "supported"
        and bool(by_name[name].evidence_refs)
        and all(reference.strip() for reference in by_name[name].evidence_refs)
        for name in required_names
    )
