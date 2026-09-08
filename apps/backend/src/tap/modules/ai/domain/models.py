"""Provider-neutral, project-scoped model gateway values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from tap.modules.access.domain.context import ProjectScopeContext


class ModelCapability(StrEnum):
    CHAT = "chat"
    EMBED = "embed"
    STRUCTURED = "structured"


class ModelOperation(StrEnum):
    CHAT = "chat"
    EMBED = "embed"
    STRUCTURED = "structured"


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    alias: str
    display_name: str
    capabilities: frozenset[ModelCapability]
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.alias or not self.display_name or not self.capabilities:
            raise ValueError("model descriptors require an alias, display name, and capability")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    scope: ProjectScopeContext
    alias: str
    operation: ModelOperation
    prompt: str
    prompt_digest: str
    context: str
    timeout_seconds: float
    idempotency_key: str
    schema: dict[str, object] | None = None
    schema_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelResult:
    output: str | tuple[float, ...] | dict[str, object]
    actual_model: str
    usage: ModelUsage
    actual_provider: str
    audit: ModelCallAudit
    provider_request_id: str | None = None
    gateway_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelCallAudit:
    scope: ProjectScopeContext
    alias: str
    operation: ModelOperation
    prompt_digest: str
    schema_digest: str | None
    context_digest: str
    idempotency_key: str
    actual_provider: str
    actual_model: str
    usage: ModelUsage


class ModelGatewayRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("model-request-rejected")


class ModelGatewayUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("model-unavailable")


def text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_digest(value: dict[str, object]) -> str:
    return text_digest(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
