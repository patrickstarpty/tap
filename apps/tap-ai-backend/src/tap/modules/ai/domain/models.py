"""Provider-neutral, project-scoped model gateway values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
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
    tool_allowlist: frozenset[str] = frozenset()
    governance_digests: tuple[str, ...] = ()
    allow_retries: bool = True
    image_bytes: bytes | None = field(default=None, repr=False)
    image_media_type: str | None = None
    # Completion-token budget for this call; ``None`` keeps the gateway's
    # per-operation default (vision structured output 8192, everything else 2048).
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationGovernance:
    model_alias: str
    system_instruction: str
    system_instruction_digest: str
    skill_instructions: tuple[str, ...]
    skill_instruction_digests: tuple[str, ...]
    tool_allowlist: frozenset[str]
    output_schema: dict[str, object]
    output_schema_digest: str
    revision_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.model_alias
            or not self.system_instruction.strip()
            or self.system_instruction_digest != text_digest(self.system_instruction)
            or len(self.skill_instructions) != len(self.skill_instruction_digests)
            or any(
                digest != text_digest(instruction)
                for instruction, digest in zip(
                    self.skill_instructions, self.skill_instruction_digests, strict=True
                )
            )
            or not self.tool_allowlist <= {"knowledge.search", "knowledge.answer"}
            or self.output_schema_digest != schema_digest(self.output_schema)
            or not self.revision_digests
        ):
            raise ModelGatewayRejected()


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
    call_id: str | None = None
    cost_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ModelTextDelta:
    """A partial chunk of streamed chat text; not itself billed or recorded."""

    text: str


# The last event of a `chat_stream` is always the complete `ModelResult` (full text,
# usage, actual_model, cost_usd, gateway_call_id) -- identical in shape to `chat`'s
# non-streaming return value.
ModelStreamEvent = ModelTextDelta | ModelResult


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
    tool_allowlist: frozenset[str] = frozenset()
    governance_digests: tuple[str, ...] = ()
    image_digest: str | None = None


class ModelGatewayRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("model-request-rejected")


class ModelGatewayUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("model-unavailable")


class ModelOutputTruncated(ModelGatewayUnavailable):
    """The model's output did not fit: it stopped at the output token budget, its
    structured output ends mid-document, or it exceeds a schema array bound.

    Reported as ``model-unavailable`` here; callers that can shrink their
    input (e.g. graph extraction splitting a batch) catch this subclass. The
    Tapper knowledge adapter (``modules/knowledge/adapters/litellm.py``)
    instead reports it as ``model-rejected`` (``AnswerRejected``), since
    retrying the same oversized request cannot help.
    """


class ModelNotSelectable(ValueError):
    """The requested alias is absent from the catalog or lacks a required capability."""

    def __init__(self) -> None:
        super().__init__("model-not-selectable")


def text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_digest(value: dict[str, object]) -> str:
    return text_digest(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
