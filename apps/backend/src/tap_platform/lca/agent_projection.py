"""Loss-aware projection of stored AA actions. Never parses or executes scripts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .definition import validate_definition

_OBSERVATIONS = {"snapshot", "screenshot", "preflight", "finish", "recording"}


def _identity(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def project_agent_actions(
    rows: list[dict[str, Any]],
    allowed_origins: list[str],
    plan: list[dict[str, Any]],
    action_steps: dict[str, str],
    source: dict[str, str],
    preserve_observations: bool = False,
) -> dict[str, Any]:
    """Caller must first bind source execution, task, session and project.

    Any unsupported/missing operation blocks executable import. The report still
    preserves every issue and the original immutable source references.
    """
    if len(rows) > 1000 or len(plan) > 100:
        raise ValueError("AA projection exceeds bounded input")
    plans = {item["id"]: item for item in plan}
    if len(plans) != len(plan):
        raise ValueError("Duplicate BDD identity")
    identities, sequences = set(), set()
    steps: list[dict[str, Any]] = []
    targets, groups, issues, observations = [], [], [], []
    last_group = None
    observed_sequences: set[int] = set()
    read_business_steps: dict[str, str] = {}
    for row in sorted(rows, key=lambda item: item["sequence"]):
        identity, sequence = row["id"], row["sequence"]
        if not isinstance(identity, str) or type(sequence) is not int or sequence < 1:
            raise ValueError("Invalid AA action identity")
        if identity in identities or sequence in sequences:
            raise ValueError("Duplicate AA receipt")
        identities.add(identity)
        sequences.add(sequence)
        request = row["request"]
        operation = request.get("type")
        observation = row.get("observation") or {}
        reason = None
        bound = (observation.get("extracted") or {}).get("control_receipt")
        canonical_target = None
        if isinstance(bound, dict) and operation in {"click", "fill", "select"}:
            before, after = (
                bound.get("pre_observation_sequence"),
                observation.get("sequence"),
            )
            target, args = bound.get("target"), bound.get("args")
            if (
                type(before) is not int
                or type(after) is not int
                or before < 0
                or after < before
                or not isinstance(target, dict)
                or not isinstance(args, dict)
            ):
                reason = "invalid_control_receipt"
            else:
                kind = {"test_id": "test_id", "role_name": "role", "css": "css"}.get(
                    str(target.get("kind"))
                )
                if kind is None:
                    reason = "unsupported_control_target"
                else:
                    canonical_target = {"kind": kind, "value": target.get("value")}
                    if kind == "role":
                        canonical_target["name"] = target.get("name")
                    if kind == "css":
                        canonical_target["review_required"] = True
                    request = dict(request)
                    if operation in {"fill", "select"} and isinstance(
                        args.get("value"), str
                    ):
                        request.pop("input_ref", None)
                        request["value"] = args["value"]
        if preserve_observations and operation == "snapshot" and steps:
            operation = "observe"
        if (
            operation in _OBSERVATIONS
            and row["status"] == "completed"
            and not observation.get("error")
        ):
            observations.append({"action_id": identity, "operation": operation})
            continue
        if row.get("actor_kind", "agent") != "agent":
            reason = "human_action_requires_separate_review"
        elif row["status"] != "completed" or observation.get("error"):
            reason = "unconfirmed_action"
        elif "input_ref" in request:
            reason = "protected_input"
        elif operation not in {
            "navigate",
            "click",
            "fill",
            "select",
            "read",
            "observe",
        }:
            reason = "unsupported_operation"
        observation_sequence = observation.get("sequence")
        if type(observation_sequence) is int:
            if observation_sequence in observed_sequences:
                raise ValueError("Ambiguous source observation sequence")
            observed_sequences.add(observation_sequence)
        bdd_id = action_steps.get(identity) or action_steps.get(
            f"observation:{observation_sequence}"
        )
        if operation == "observe" and bdd_id is None:
            bdd_id = last_group
        # Finalization rechecks the same read contract. Preserve this actual extra
        # read and inherit only an earlier identical contract's business step.
        read_contract = None
        if operation == "read":
            read_contract = json.dumps(
                {k: v for k, v in request.items() if k != "expected_epoch"},
                sort_keys=True,
            )
            if bdd_id is None:
                bdd_id = read_business_steps.get(read_contract)
        bdd = plans.get(bdd_id)
        if reason is None and (
            not bdd
            or bdd.get("keyword") not in {"Given", "When", "Then", "And", "But"}
            or not isinstance(bdd.get("title"), str)
            or not bdd["title"].strip()
        ):
            reason = "missing_business_step"
        selector = request.get("selector", "")
        target_facts = [
            field
            for field in observation.get("fields", [])
            if field.get("context") == "action_target"
            or field.get("selector") == selector
        ]
        if any(
            field.get("type") == "password" or field.get("value") == "[hidden]"
            for field in target_facts
        ):
            reason = "protected_input"
        observed_targets = [
            field for field in target_facts if field.get("context") == "action_target"
        ]
        if isinstance(selector, str) and (
            "aria-ref=" in selector or "[ref=" in selector
        ):
            if len(observed_targets) == 1:
                selector = observed_targets[0].get("selector", selector)
        if (
            reason is None
            and operation not in {"navigate", "observe"}
            and canonical_target is None
        ):
            if (
                not isinstance(selector, str)
                or not selector
                or len(selector) > 1024
                or ">>" in selector
                or "aria-ref=" in selector
                or "[ref=" in selector
                or re.match(r"^(?:text|role|internal:|xpath)=", selector)
            ):
                reason = "unstable_or_unsupported_locator"
        arguments: dict[str, Any] = {}
        if operation == "navigate":
            arguments = {"url": request.get("url")}
        elif operation in {"fill", "select"}:
            arguments = {"value": request.get("value")}
            if (
                operation == "select"
                and canonical_target is not None
                and any(
                    field.get("tag") not in (None, "select")
                    for field in observed_targets
                )
            ):
                reason = reason or "custom_select_requires_review"
            # A successful production control receipt can only come from the
            # native single-select actuator, which acknowledges the canonical
            # option value after checking its effect. Custom comboboxes throw
            # before producing a successful receipt. Unbound sources need DOM
            # option evidence below; an input label alone is insufficient.
            if operation == "select" and canonical_target is None:
                native = [
                    field for field in target_facts if field.get("tag") == "select"
                ]
                if len(native) != 1:
                    reason = reason or "custom_select_requires_review"
                else:
                    matches = [
                        option
                        for option in native[0].get("options", [])
                        if request.get("value")
                        in (option.get("value"), option.get("label"))
                    ]
                    if len(matches) != 1 or native[0].get("value") != matches[0].get(
                        "value"
                    ):
                        reason = reason or "native_option_evidence_unavailable"
                    else:
                        arguments = {"value": matches[0]["value"]}
        elif operation == "read":
            operation = "assert_read"
            arguments = {
                key: request[key]
                for key in (
                    "attribute",
                    "name",
                    "equals",
                    "contains",
                    "nonempty",
                    "positive",
                    "pattern",
                )
                if key in request
            }
            arguments.setdefault("attribute", "text")
        if reason:
            issues.append(
                {
                    "action_id": identity,
                    "sequence": sequence,
                    "operation": request.get("type"),
                    "reason": reason,
                }
            )
            continue
        assert bdd is not None
        if read_contract is not None and bdd_id is not None:
            read_business_steps[read_contract] = bdd_id
        step_id = _identity("aa_", identity)
        step: dict[str, Any] = {
            "id": step_id,
            "operation": operation,
            "arguments": arguments,
            "timeout_ms": request.get("timeout_ms", 30000),
            **(
                {
                    "target_contract": {
                        key: request[key]
                        for key in ("target_texts", "scope_text")
                        if key in request
                    }
                }
                if "target_texts" in request or "scope_text" in request
                else {}
            ),
            "provenance": {
                "source": "aa",
                "source_action_id": identity,
                "source_sequence": str(sequence),
                "source_bdd_id": str(bdd_id),
                **{
                    f"source_bdd_{key}": bdd[key]
                    for key in ("expected_result", "verification_kind")
                    if isinstance(bdd.get(key), str)
                },
                **(
                    {"source_output_name": request["name"]}
                    if request.get("type") == "read"
                    and isinstance(request.get("name"), str)
                    else {}
                ),
                **{f"source_{key}": value for key, value in source.items()},
            },
        }
        if operation not in {"navigate", "observe"}:
            target_id = _identity("target_", identity)
            targets.append(
                {
                    "id": target_id,
                    **(
                        canonical_target
                        or {
                            "kind": "css",
                            "value": selector.removeprefix("css="),
                            "review_required": True,
                        }
                    ),
                }
            )
            step["target_id"] = target_id
        steps.append(step)
        # Repeated/interleaved business steps remain separate consecutive groups
        # so visual order is exactly execution order, never regrouped by title.
        if bdd_id != last_group:
            groups.append(
                {
                    "id": _identity("bdd_", identity),
                    "keyword": bdd["keyword"],
                    "description": bdd["title"],
                    "action_ids": [],
                }
            )
            last_group = bdd_id
        groups[-1]["action_ids"].append(step_id)
    definition = None
    if not steps:
        issues.append(
            {
                "action_id": "",
                "sequence": 0,
                "operation": "",
                "reason": "no_replayable_actions",
            }
        )
    if not issues:
        try:
            definition = validate_definition(
                {
                    "schema_version": "lca-debug-v1",
                    "allowed_origins": allowed_origins,
                    "targets": targets,
                    "parameters": [],
                    "steps": steps,
                    "bdd_steps": groups,
                }
            ).model_dump(exclude_unset=True)
        except ValueError:
            issues.append(
                {
                    "action_id": "",
                    "sequence": 0,
                    "operation": "",
                    "reason": "definition_requires_manual_review",
                }
            )
    return {
        "schema_version": "aa-lca-draft-v1",
        "complete": definition is not None,
        "definition": definition,
        "issues": issues,
        "observations": observations,
        "source": source,
        "review_required": True,
    }
