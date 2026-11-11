"""Immutable values exchanged by the browser automation engine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from .policy import validate_origin, validate_network_mode


@dataclass(frozen=True)
class BrowserLimits:
    deadline_seconds: int = 120
    memory_mb: int = 1024
    cpus: float = 1.0
    pids: int = 128
    max_output_bytes: int = 80_000_000

    def __post_init__(self) -> None:
        if not (1 <= self.deadline_seconds <= 600 and 128 <= self.memory_mb <= 4096):
            raise ValueError("browser limits out of range")
        if not (0 < self.cpus <= 2 and 32 <= self.pids <= 512):
            raise ValueError("browser limits out of range")
        if not 1_000 <= self.max_output_bytes <= 80_000_000:
            raise ValueError("browser output limit out of range")


@dataclass(frozen=True)
class BrowserBundle:
    bundle_id: str
    project_id: str
    sha256: str
    script_bytes: bytes
    input_sha256: str
    allowed_origins: tuple[str, ...]
    created_at: str
    entry_name: str = "flow.spec.ts"
    network_mode: str = "declared_origins"

    def __post_init__(self) -> None:
        validate_network_mode(self.network_mode)
        if self.entry_name != "flow.spec.ts" or len(self.script_bytes) > 256_000:
            raise ValueError("invalid frozen browser entry")
        if hashlib.sha256(self.script_bytes).hexdigest() != self.sha256:
            raise ValueError("browser bundle hash mismatch")
        if len(self.input_sha256) != 64:
            raise ValueError("invalid input hash")
        if not self.allowed_origins:
            raise ValueError("browser origins required")
        for origin in self.allowed_origins:
            if validate_origin(origin) != origin:
                raise ValueError("noncanonical origin")


@dataclass(frozen=True)
class BrowserObservation:
    session_id: str
    sequence: int
    url: str
    title: str
    bounded_snapshot: str
    fields: tuple[dict[str, Any], ...]
    screenshot_ref: str | None
    error: str | None
    extracted: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    evidence: tuple[dict[str, Any], ...] = ()
    write_started: bool | None = None


@dataclass(frozen=True)
class Evidence:
    kind: Literal["screenshot", "trace", "result", "video"]
    object_ref: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class BrowserExecution:
    execution_id: str
    project_id: str
    task_id: str
    bundle_id: str
    input_sha256: str
    status: Literal["passed", "failed", "canceled", "unknown"]
    started_at: str
    finished_at: str | None
    result: dict[str, Any]
    steps: tuple[dict[str, Any], ...]
    evidence: tuple[Evidence, ...]
    failure_code: str | None


ControlState = Literal[
    "agent_active",
    "takeover_pending",
    "human_active",
    "handback_pending",
    "blocked",
    "closed",
]


@dataclass(frozen=True)
class SemanticTarget:
    kind: Literal["test_id", "role_name", "css"]
    value: str
    name: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("test_id", "role_name", "css") or not self.value:
            raise ValueError("invalid semantic target")
        if self.kind == "role_name" and not self.name:
            raise ValueError("role target requires accessible name")


@dataclass(frozen=True)
class BrowserActionReceipt:
    command_id: str
    action_id: str
    sequence: int
    actor_kind: Literal["agent", "human"]
    control_epoch: int
    status: Literal[
        "pending",
        "running",
        "dispatching",
        "rejected",
        "completed",
        "failed",
        "unknown",
        "canceled",
    ]
    operation: str
    pre_observation_sequence: int | None
    target: SemanticTarget | None
    args: dict[str, Any]
    post_observation_sequence: int | None
    evidence_refs: tuple[dict[str, Any], ...] = ()
    schema_version: Literal["browser-control-v1"] = "browser-control-v1"

    def __post_init__(self) -> None:
        if self.operation not in {
            "navigate",
            "preflight",
            "snapshot",
            "click",
            "fill",
            "select",
            "press",
            "wait",
            "screenshot",
            "read",
            "finish",
            "scroll",
        }:
            raise ValueError("unsupported browser operation")
        if (
            self.sequence < 1
            or self.control_epoch < 0
            or not self.command_id
            or not self.action_id
        ):
            raise ValueError("invalid action receipt identity")
        if self.actor_kind not in ("agent", "human"):
            raise ValueError("invalid action receipt actor")
