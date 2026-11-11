"""Project authorized terminal human receipts, never redispatch browser actions."""

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .definition import DebugDefinition, validate_definition


@dataclass(frozen=True)
class ExcludedAction:
    action_id: str
    sequence: int
    reason: str


@dataclass(frozen=True)
class RecordingProjection:
    definition: DebugDefinition | None
    missing_sequences: tuple[int, ...]
    excluded: tuple[ExcludedAction, ...]

    @property
    def ready(self) -> bool:
        return not self.missing_sequences


def project_recording(
    receipts: list[dict[str, Any]],
    allowed_origins: list[str],
    watermark: int,
    *,
    start_sequence: int = 1,
) -> RecordingProjection:
    """Consume already authorized AA receipts within one recording interval.

    Pending commands are represented by absent terminal receipts. The caller must
    freeze the result once; later receipts require a new explicit draft version.
    This function is not the credential filter or the session authorizer.
    """
    if (
        type(watermark) is not int
        or type(start_sequence) is not int
        or start_sequence < 1
        or not start_sequence - 1 <= watermark <= start_sequence + 99
    ):
        raise ValueError("invalid recording interval")
    by_sequence: dict[int, dict[str, Any]] = {}
    action_ids: dict[str, int] = {}
    for receipt in receipts:
        sequence = receipt.get("sequence")
        if (
            receipt.get("schema_version") != "browser-control-v1"
            or receipt.get("actor_kind") != "human"
            or type(sequence) is not int
            or sequence < 1
            or not isinstance(receipt.get("action_id"), str)
            or not receipt["action_id"]
            or not isinstance(receipt.get("command_id"), str)
            or not receipt["command_id"]
            or type(receipt.get("control_epoch")) is not int
            or receipt["control_epoch"] < 0
            or receipt.get("status")
            not in {"completed", "failed", "unknown", "rejected", "canceled"}
        ):
            raise ValueError("invalid acknowledged human receipt")
        if not start_sequence <= sequence <= watermark:
            continue
        if receipt["status"] == "completed" and receipt.get("operation") in {
            "navigate",
            "click",
            "fill",
            "select",
            "scroll",
        }:
            before, after = (
                receipt.get("pre_observation_sequence"),
                receipt.get("post_observation_sequence"),
            )
            evidence = receipt.get("evidence_refs")
            if (
                type(before) is not int
                or before < 0
                or type(after) is not int
                or after < before
                or not isinstance(evidence, list)
                or not all(_valid_evidence(ref) for ref in evidence)
            ):
                raise ValueError("invalid acknowledged human receipt provenance")
        previous = by_sequence.get(sequence)
        if previous is not None and previous != receipt:
            raise ValueError("conflicting recording receipt")
        action_id = receipt["action_id"]
        if action_id in action_ids and action_ids[action_id] != sequence:
            raise ValueError("conflicting action sequence")
        action_ids[action_id] = sequence
        by_sequence[sequence] = receipt
    excluded: list[ExcludedAction] = []
    steps: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for sequence, receipt in sorted(by_sequence.items()):
        operation = receipt.get("operation")
        reason = None
        if receipt["status"] != "completed":
            reason = receipt["status"]
        elif operation not in {"navigate", "click", "fill", "select", "scroll"}:
            reason = "unsupported_operation"
        if reason:
            excluded.append(ExcludedAction(receipt["action_id"], sequence, reason))
            continue
        identity = hashlib.sha256(receipt["action_id"].encode()).hexdigest()[:24]
        step: dict[str, Any] = {
            "id": f"recorded_{identity}",
            "operation": operation,
            "arguments": json.loads(json.dumps(receipt.get("args"), allow_nan=False)),
            "timeout_ms": 10000,
            "provenance": {
                "action_id": receipt["action_id"],
                "sequence": str(sequence),
                "command_id": receipt["command_id"],
                "control_epoch": str(receipt["control_epoch"]),
                "pre_observation_sequence": str(receipt["pre_observation_sequence"]),
                "post_observation_sequence": str(receipt["post_observation_sequence"]),
                "evidence_refs": json.dumps(
                    receipt["evidence_refs"], separators=(",", ":")
                ),
            },
        }
        if operation != "navigate" and not (
            operation == "scroll" and receipt.get("target") is None
        ):
            target = receipt.get("target")
            if not isinstance(target, dict):
                raise ValueError("missing acknowledged semantic target")
            strategy = target.get("kind")
            if not isinstance(strategy, str):
                raise ValueError("unsupported semantic target")
            kind = {"test_id": "test_id", "role_name": "role", "css": "css"}.get(
                strategy
            )
            if kind is None:
                raise ValueError("unsupported semantic target")
            target_id = f"target_{identity}"
            canonical: dict[str, Any] = {
                "id": target_id,
                "kind": kind,
                "value": target.get("value"),
            }
            if kind == "role":
                canonical["name"] = target.get("name")
            if kind == "css":
                canonical["review_required"] = True
            targets.append(canonical)
            step["target_id"] = target_id
        steps.append(step)
    definition = (
        validate_definition(
            {
                "schema_version": "lca-debug-v1",
                "allowed_origins": allowed_origins,
                "targets": targets,
                "parameters": [],
                "steps": steps,
            }
        )
        if steps
        else None
    )
    return RecordingProjection(
        definition,
        tuple(n for n in range(start_sequence, watermark + 1) if n not in by_sequence),
        tuple(excluded),
    )


def _valid_evidence(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("kind"), str)
        and bool(value["kind"])
        and isinstance(value.get("object_ref"), str)
        and bool(value["object_ref"])
        and isinstance(value.get("sha256"), str)
        and len(value["sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value["sha256"])
        and type(value.get("size_bytes")) is int
        and value["size_bytes"] >= 0
    )
