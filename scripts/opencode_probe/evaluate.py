"""Fail-closed adjudication of real OpenCode capability evidence."""

from collections.abc import Mapping
from typing import Any, cast

from scripts.opencode_probe.models import CapabilityResult

REQUIRED = frozenset(
    {
        "byok_all_calls",
        "child_identity",
        "parallel",
        "permissions",
        "control_receipt",
        "resume_workspace",
        "reconcile",
        "budget_enforcement",
    }
)


def ready_for_runtime(
    results: tuple[CapabilityResult, ...], required: frozenset[str]
) -> bool:
    """Every required capability needs distinct, real OpenCode evidence."""
    found: dict[str, CapabilityResult] = {}
    for result in results:
        if result.name in found:
            return False
        found[result.name] = result
    return all(
        name in found
        and found[name].status == "supported"
        and bool(found[name].evidence_refs)
        and all(ref.startswith("opencode-real:") for ref in found[name].evidence_refs)
        for name in required
    )


def verified_provenance(
    sample: Mapping[str, Any], *, allow_failed_terminal: bool = False
) -> bool:
    """Reject copied fixtures, missing counters and nonterminal live samples."""
    if (
        sample.get("source") != "opencode-real"
        or sample.get("cli_version") != "1.19.5"
        or sample.get("model") != "litellm/qwen-plus"
        or sample.get("terminal")
        not in ({"complete", "failed"} if allow_failed_terminal else {"complete"})
    ):
        return False
    digest = sample.get("config_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        return False
    gate = sample.get("gate")
    sessions = sample.get("sessions")
    if not isinstance(gate, dict) or not isinstance(sessions, list):
        return False
    calls = gate.get("calls")
    records = gate.get("records")
    if type(calls) is not int or calls <= 0 or not isinstance(records, list):
        return False
    if calls != len(records) or not all(
        isinstance(record, dict)
        and record.get("claimed_session_id") in sessions
        and record.get("usage_verified") is True
        and record.get("status") == 200
        for record in records
    ):
        return False
    return True


def evaluate_samples(
    samples: Mapping[str, Mapping[str, Any]],
) -> tuple[CapabilityResult, ...]:
    """Adjudicate eight required gates from sanitized live-only samples."""

    def live(
        name: str, *, allow_failed_terminal: bool = False
    ) -> Mapping[str, Any] | None:
        sample = samples.get(name)
        return (
            sample
            if sample is not None
            and verified_provenance(sample, allow_failed_terminal=allow_failed_terminal)
            else None
        )

    def ref(name: str) -> tuple[str, ...]:
        sample = samples.get(name)
        if not isinstance(sample, Mapping) or sample.get("source") != "opencode-real":
            return ()
        session_id = sample.get("root_session_id")
        return (
            (f"opencode-real:{name}:{session_id}",)
            if isinstance(session_id, str)
            else ()
        )

    def facts(sample: Mapping[str, Any] | None) -> dict[str, Any]:
        value = sample.get("session_facts") if sample else None
        return value if isinstance(value, dict) else {}

    def child_matches(sample: Mapping[str, Any] | None, agent: str) -> bool:
        if sample is None:
            return False
        root_id = sample.get("root_session_id")
        data = facts(sample)
        children = data.get("children")
        tools = data.get("tools")
        records = sample.get("gate", {}).get("records")
        if (
            not isinstance(children, list)
            or not isinstance(tools, list)
            or not isinstance(records, list)
        ):
            return False
        return any(
            isinstance(child, dict)
            and child.get("agent") == agent
            and child.get("parent_id") == root_id
            and isinstance(child.get("started_ms"), (int, float))
            and isinstance(child.get("ended_ms"), (int, float))
            and child["started_ms"] < child["ended_ms"]
            and isinstance(child.get("message_count"), int)
            and child["message_count"] >= 2
            and any(
                isinstance(tool, dict)
                and tool.get("tool") == "task"
                and tool.get("status") == "completed"
                and tool.get("child_session_id") == child.get("id")
                for tool in tools
            )
            and any(
                isinstance(record, dict)
                and record.get("claimed_session_id") == child.get("id")
                for record in records
            )
            for child in children
        )

    auto = live("auto")
    explicit = live("explicit")
    identity = child_matches(auto, "sde") and child_matches(explicit, "qae")
    parallel = live("parallel")
    parallel_facts = facts(parallel)
    parallel_children = parallel_facts.get("children")
    parallel_artifacts = parallel.get("artifacts") if parallel else None
    parallel_ok = False
    if (
        isinstance(parallel_children, list)
        and len(parallel_children) == 2
        and isinstance(parallel_artifacts, dict)
    ):
        by_agent = {
            child.get("agent"): child
            for child in parallel_children
            if isinstance(child, dict)
        }
        sde = by_agent.get("sde")
        sdet = by_agent.get("sdet")
        if isinstance(sde, dict) and isinstance(sdet, dict):
            times = (
                sde.get("started_ms"),
                sde.get("ended_ms"),
                sdet.get("started_ms"),
                sdet.get("ended_ms"),
            )
            parallel_ok = (
                all(isinstance(value, (int, float)) for value in times)
                and max(cast(float, times[0]), cast(float, times[2]))
                < min(cast(float, times[1]), cast(float, times[3]))
                and all(name in parallel_artifacts for name in ("one.py", "two.py"))
                and any(
                    isinstance(tool, dict)
                    and tool.get("tool") == "write"
                    and tool.get("file") == "one.py"
                    and tool.get("status") == "completed"
                    for tool in sde.get("tools", [])
                )
                and any(
                    isinstance(tool, dict)
                    and tool.get("tool") == "write"
                    and tool.get("file") == "two.py"
                    and tool.get("status") == "completed"
                    for tool in sdet.get("tools", [])
                )
            )
    control = live("control")
    control_data = control.get("control") if control else None
    control_facts = facts(control)
    control_ok = (
        control is not None
        and isinstance(control_data, dict)
        and control_data.get("submitted_during_active_root") is True
        and isinstance(control_data.get("accepted_message_id"), str)
        and control_data["accepted_message_id"] != control.get("user_message_id")
        and control_data["accepted_message_id"]
        in control_facts.get("assistant_parent_ids", [])
        and "second.txt" in control.get("artifacts", {})
    )
    reconcile = live("reconcile")
    loss = reconcile.get("reconcile_loss") if reconcile else None
    reconcile_ok = (
        reconcile is not None
        and isinstance(loss, dict)
        and loss.get("controller_exit_code") == -9
        and loss.get("old_session_found") is True
        and loss.get("new_session_started") is False
        and loss.get("same_server_session_id") == reconcile.get("root_session_id")
        and reconcile.get("user_message_id")
        in facts(reconcile).get("assistant_parent_ids", [])
        and "recovered.txt" in reconcile.get("artifacts", {})
    )
    permissions = live("permissions", allow_failed_terminal=True)
    permission_children = facts(permissions).get("children")
    permission_target = permissions.get("permission_target") if permissions else None
    permission_ok = bool(
        permissions
        and isinstance(permission_children, list)
        and isinstance(permission_target, dict)
        and "before" in permission_target
        and "after" in permission_target
        and permission_target["before"] == permission_target["after"]
        and permission_target["before"] != "unsafe"
        and child_matches(permissions, "qae")
        and "denied.txt" not in permissions.get("artifacts", {})
        and any(
            isinstance(child, dict)
            and child.get("agent") == "qae"
            and child.get("parent_id") == permissions.get("root_session_id")
            and any(
                isinstance(tool, dict)
                and tool.get("tool") == "write"
                and tool.get("status") == "error"
                and tool.get("file") == "denied.txt"
                and tool.get("target_is_denied_workspace_file") is True
                and tool.get("denial_category") == "opencode_permission_denied"
                for tool in child.get("tools", [])
            )
            for child in permission_children
        )
    )
    resume = live("resume")
    resume_data = resume.get("resume") if resume else None
    resume_ok = bool(
        resume
        and isinstance(resume_data, dict)
        and resume_data.get("owned_marker_before_abort", {}).get("identity_verified")
        is True
        and resume_data.get("owned_marker_after_abort", {}).get("alive") is False
        and resume_data.get("first_digest_before_abort")
        == resume_data.get("first_digest_after_abort")
        and resume_data.get("second_absent_after_abort") is True
        and resume_data.get("first_action_count") == 1
        and "second.txt" in resume.get("artifacts", {})
    )

    outcomes = {
        "byok_all_calls": (
            False,
            ("simple", "auto", "parallel"),
            "All observed calls use the controlled gate; auxiliary subtype and unobserved call paths remain unverified.",
        ),
        "child_identity": (
            identity,
            ("auto", "explicit"),
            "Automatic and explicit child session identity or terminal evidence missing.",
        ),
        "parallel": (
            parallel_ok,
            ("parallel",),
            "Two overlapping provider child intervals with disjoint attributed writes are required.",
        ),
        "permissions": (
            permission_ok,
            ("permissions",),
            "QAE declined the write without an executed permission-denial tool event.",
        ),
        "control_receipt": (
            control_ok,
            ("control",),
            "Applied at a safe turn boundary; mid-tool injection is unverified.",
        ),
        "resume_workspace": (
            resume_ok,
            ("resume",),
            "Owned process stopped and same session continued, but a first-action run counter was not captured.",
        ),
        "reconcile": (
            reconcile_ok,
            ("reconcile",),
            "Recovered after task-owned controller loss; OpenCode server loss is unverified.",
        ),
        "budget_enforcement": (
            False,
            ("budget", "parallel"),
            "Real call-count/cost cutoff observed; timeout and cumulative child/resume limits need complete proof.",
        ),
    }
    return tuple(
        CapabilityResult(
            name,
            "supported" if passed else "unverified",
            tuple(reference for sample_name in names for reference in ref(sample_name)),
            limitation,
        )
        for name, (passed, names, limitation) in outcomes.items()
    )
