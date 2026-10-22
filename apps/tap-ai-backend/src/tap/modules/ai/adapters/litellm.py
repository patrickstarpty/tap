"""Single bounded model transport for all V1 model operations."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import math
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

import httpx

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm_catalog import (
    LiteLLMCatalog,
    LiteLLMModel,
    ModelRoles,
    validate_litellm_base_url,
)
from tap.modules.ai.application.schema import (
    StructuredOutputOverflow,
    check_schema,
    validate_output,
)
from tap.modules.ai.domain.model_calls import ModelCallRecord
from tap.modules.ai.domain.models import (
    ModelCallAudit,
    ModelCapability,
    ModelDescriptor,
    ModelGatewayRejected,
    ModelGatewayUnavailable,
    ModelOperation,
    ModelOutputTruncated,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCall,
    ModelUsage,
    schema_digest,
    structured_schema_instruction,
    text_digest,
)
from tap.modules.ai.domain.tools import TOOL_REGISTRY, known_tool_ids
from tap.modules.ai.ports.model_calls import ModelCallRecorder
from tap.platform.budget import current_meter
from tap.platform.telemetry import current_binding, span

Redact = Callable[[str], Awaitable[str]]

logger = logging.getLogger(__name__)

# Ceiling for any one call's deadline. Interactive calls stay at their own (much
# shorter) configured timeouts; only long batch work such as graph extraction is
# configured up to this bound.
MAX_GATEWAY_TIMEOUT_SECONDS = 300
# Completion-token budget bounds a caller may request per call.
MAX_OUTPUT_TOKENS = 32768
DEFAULT_OUTPUT_TOKENS = 2048
VISION_OUTPUT_TOKENS = 8192

# Audited upstream model identifiers: printable ASCII only, bounded to 256 characters.
ACTUAL_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")


# `model_call.cost_usd` is DECIMAL(18,10): 8 integer digits, 10 fractional digits.
_COST_QUANTUM = Decimal("1E-10")
_COST_LIMIT = Decimal("1E8")


def _parse_cost_header(value: str | None) -> Decimal | None:
    """Parse the LiteLLM cost header at the stored precision.

    LiteLLM computes cost in floating point (e.g. ``0.004916800000000001``); rounding here keeps
    MySQL from truncating on write. Non-finite, negative or out-of-range values are never recorded.
    """
    if value is None:
        return None
    try:
        cost = Decimal(value)
    except InvalidOperation:
        return None
    if not cost.is_finite() or cost < 0 or cost >= _COST_LIMIT:
        return None
    return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


def _reported_usage(body: dict[str, Any]) -> tuple[int | None, int | None]:
    """Token usage the provider reported, read before the output is validated.

    A call whose output is rejected (truncated, off-schema) was still billed.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None

    def count(value: object) -> int | None:
        return value if type(value) is int and 0 <= value <= 1000000 else None

    return count(usage.get("prompt_tokens")), count(usage.get("completion_tokens"))


def _resolve_actual_model(returned: object, upstream_model: str | None) -> tuple[str, str]:
    """Shared by streaming and non-streaming parsing: the deployment header's mapped
    upstream model wins; the provider-echoed ``model`` field must still be a valid,
    bounded identifier even when the header already resolved one."""

    if not isinstance(returned, str) or not returned.strip():
        raise ModelGatewayUnavailable()
    actual = upstream_model if upstream_model is not None else returned
    if ACTUAL_MODEL_PATTERN.fullmatch(actual) is None:
        raise ModelGatewayUnavailable()
    provider = actual.split("/", 1)[0] if "/" in actual else "unknown"
    if not provider:
        raise ModelGatewayUnavailable()
    return actual, provider


def _chunk_delta(chunk: dict[str, Any]) -> dict[str, Any] | None:
    """The first choice's ``delta`` object of one SSE chunk, or ``None`` when absent."""

    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    delta = choices[0].get("delta")
    return delta if isinstance(delta, dict) else None


def _chunk_delta_content(chunk: dict[str, Any]) -> str | None:
    """The text delta of one SSE chunk's first choice, or ``None`` when absent."""

    delta = _chunk_delta(chunk)
    content = delta.get("content") if delta is not None else None
    return content if isinstance(content, str) and content else None


def _chunk_finish_reason(chunk: dict[str, Any]) -> str | None:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    reason = choices[0].get("finish_reason")
    return reason if isinstance(reason, str) else None


def _validate_usage_tokens(usage: dict[str, Any]) -> tuple[int | None, int | None]:
    """Shared by `_normalize` and `chat_stream`: usage token fields must be bounded ints
    or absent; any other type or out-of-range value makes the whole call unavailable."""

    tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    if any(
        value is not None and (type(value) is not int or not 0 <= value <= 1000000)
        for value in tokens
    ):
        raise ModelGatewayUnavailable()
    return tokens  # type: ignore[return-value]


def _parse_message_tool_calls(raw: Any) -> tuple[ModelToolCall, ...]:
    """Validate and parse the non-streaming `message.tool_calls` array into the
    gateway's `ModelToolCall` tuple. Shared by `_normalize`'s tool-call branch."""

    if not isinstance(raw, list) or not raw:
        raise ModelGatewayUnavailable()
    calls: list[ModelToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ModelGatewayUnavailable()
        call_id = item.get("id")
        function = item.get("function")
        if not isinstance(function, dict):
            raise ModelGatewayUnavailable()
        name = function.get("name")
        arguments = function.get("arguments")
        if (
            not isinstance(call_id, str)
            or not call_id
            or not isinstance(name, str)
            or not name
            or not isinstance(arguments, str)
        ):
            raise ModelGatewayUnavailable()
        calls.append(ModelToolCall(call_id, name, arguments))
    return tuple(calls)


def _safe_header(headers: httpx.Headers, name: str) -> str | None:
    """A header value safe to persist/audit: a bounded, printable-ASCII identifier."""

    value = headers.get(name)
    return (
        value
        if value is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", value)
        else None
    )


class _SSEDone(Exception):
    """Internal control-flow signal: the SSE body reached a ``data: [DONE]`` line."""


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    """One line of an SSE body: a parsed chunk, ``None`` for a line to ignore, or
    `_SSEDone` raised for the ``data: [DONE]`` terminator."""

    stripped = line.strip()
    if not stripped or not stripped.startswith("data:"):
        return None
    data = stripped[len("data:") :].strip()
    if data == "[DONE]":
        raise _SSEDone()
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        raise ModelGatewayUnavailable() from None
    if not isinstance(chunk, dict):
        raise ModelGatewayUnavailable()
    return chunk


@dataclass(slots=True)
class _ErrorCode:
    value: str | None = None


@contextlib.contextmanager
def _translate_model_errors(error_holder: _ErrorCode):
    """Shared by `_execute` and `chat_stream`: classify a raised exception into the
    `ModelCallRecord.error_code` written in their `finally` blocks, converting any
    unexpected provider/transport/parsing failure into `ModelGatewayUnavailable`."""

    try:
        yield
    except ModelGatewayRejected as exc:
        error_holder.value = str(exc)
        raise
    except ModelGatewayUnavailable as exc:
        error_holder.value = str(exc)
        raise
    except (
        TimeoutError,
        httpx.HTTPError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
    ):
        error_holder.value = "model-unavailable"
        raise ModelGatewayUnavailable() from None


def _request_json_for_record(
    payload: dict[str, Any] | None, request: ModelRequest, operation: ModelOperation
) -> str:
    """Serialize the sent payload for durable storage, never persisting raw image bytes.

    When the call never reached payload construction (validation or redaction rejected it
    first), only a minimal, prompt-free marker is stored.
    """
    if payload is None:
        return json.dumps({"model": request.alias, "operation": operation.value, "rejected": True})
    if request.image_bytes is None:
        return json.dumps(payload)
    image_digest = payload.get("metadata", {}).get("image_digest")
    sanitized = json.loads(json.dumps(payload))
    try:
        sanitized["messages"][1]["content"][1]["image_url"]["url"] = image_digest
    except (KeyError, IndexError, TypeError):
        # The payload's shape didn't match what was substituted; never persist the
        # raw (possibly base64-encoded) payload in that case, only a prompt-free marker.
        logger.warning("model call payload shape prevented image substitution; storing marker")
        return json.dumps(
            {"model": request.alias, "operation": operation.value, "image": image_digest}
        )
    return json.dumps(sanitized)


@dataclass(frozen=True, slots=True)
class LiteLLMModelGatewayConfig:
    base_url: str
    api_key: str = field(repr=False)
    roles: ModelRoles
    embedding_dimension: int
    timeout_seconds: float = 15.0
    max_retries: int = 1
    # In-flight calls (and pooled connections) per gateway instance; a call queued
    # behind full slots still spends its own deadline waiting.
    max_concurrency: int = 4
    # Streamed chat (capability-4 validation D3): a call's own `timeout_seconds`
    # bounds connect and the first token; after that each chunk may take up to
    # `stream_idle_seconds`, and the whole stream at most `stream_max_seconds`.
    # Inside a Turn the graph's wall-clock budget additionally cancels the call
    # at the Turn's remaining time (`ChatGraph._run_budgeted`).
    stream_idle_seconds: float = 15.0
    stream_max_seconds: float = 180.0

    def __post_init__(self) -> None:
        validate_litellm_base_url(self.base_url)
        if not self.api_key or len(self.api_key) > 4096:
            raise ValueError("model gateway requires a bounded credential")
        if not isinstance(self.roles, ModelRoles):
            raise ValueError("model gateway requires model roles")
        if type(self.embedding_dimension) is not int or not 1 <= self.embedding_dimension <= 4096:
            raise ValueError("model gateway dimension is invalid")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise ValueError("model gateway retry budget is invalid")
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 16:
            raise ValueError("model gateway concurrency is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= MAX_GATEWAY_TIMEOUT_SECONDS
        ):
            raise ValueError("model gateway timeout is invalid")
        for value in (self.stream_idle_seconds, self.stream_max_seconds):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not 0 < value <= MAX_GATEWAY_TIMEOUT_SECONDS
            ):
                raise ValueError("model gateway stream timeout is invalid")


@dataclass(frozen=True, slots=True)
class ModelHealth:
    problems: tuple[str, ...]
    notices: tuple[str, ...] = ()


@dataclass(slots=True)
class GatewayAttempts:
    count: int = 0


class LiteLLMModelGateway:
    def __init__(
        self,
        config: LiteLLMModelGatewayConfig,
        *,
        scope: ProjectScopeContext,
        redact: Redact,
        catalog: LiteLLMCatalog,
        client: httpx.AsyncClient | None = None,
        recorder: ModelCallRecorder | None = None,
    ) -> None:
        if type(scope) is not ProjectScopeContext:
            raise ModelGatewayRejected()
        self._config = config
        self.scope = scope
        self._redact = redact
        self._catalog = catalog
        self._owns_client = client is None
        self._client = client
        self._slots = asyncio.Semaphore(config.max_concurrency)
        self._recorder = recorder

    async def catalog(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]:
        self._check_scope(scope)
        models = (await self._catalog.routes()).models.values()
        chat = tuple(
            ModelDescriptor(
                item.name,
                item.display_name,
                frozenset(
                    {ModelCapability.CHAT, ModelCapability.STRUCTURED}
                    if item.supports_response_schema
                    else {ModelCapability.CHAT}
                ),
            )
            for item in models
            if item.mode == "chat"
        )
        embedding = tuple(
            ModelDescriptor(item.name, item.display_name, frozenset({ModelCapability.EMBED}))
            for item in models
            if item.mode == "embedding"
        )
        return chat + embedding

    async def health(self) -> ModelHealth:
        """Failing problems (catalog down, role misconfigured) and informational notices."""

        # Health must observe LiteLLM now; model calls may keep using the stale cache.
        try:
            routes = await self._catalog.fresh_routes()
        except ModelGatewayUnavailable:
            return ModelHealth(("LiteLLM model catalog unavailable",))
        roles = self._config.roles
        # A skipped role model is reported as a role problem, not as a notice.
        notices = tuple(
            f"LiteLLM model skipped: {name[:64]} ({reason})"
            for name, reason in routes.skipped.items()
            if name not in roles.names()
        )
        if self._recorder is not None and self._recorder.degraded():
            notices = (*notices, "model-call-recording-degraded")
        return ModelHealth(roles.problems(routes), notices)

    async def health_problems(self) -> tuple[str, ...]:
        return (await self.health()).problems

    async def chat(self, request: ModelRequest) -> ModelResult:
        return await self._execute(request, ModelOperation.CHAT)

    async def embed(self, request: ModelRequest) -> ModelResult:
        return await self._execute(request, ModelOperation.EMBED)

    async def generate_structured(self, request: ModelRequest) -> ModelResult:
        return await self._execute(request, ModelOperation.STRUCTURED)

    async def aclose(self) -> None:
        """Close the owned transport and the catalog this gateway was built on."""

        try:
            if self._owns_client and self._client is not None:
                await self._client.aclose()
        finally:
            await self._catalog.aclose()

    def _check_scope(self, scope: ProjectScopeContext) -> None:
        if type(scope) is not ProjectScopeContext or scope != self.scope:
            raise ModelGatewayRejected()

    async def _check_redacted(self, text: str) -> None:
        try:
            redacted = await self._redact(text)
        except Exception:
            raise ModelGatewayUnavailable() from None
        if redacted != text:
            raise ModelGatewayRejected()

    async def _validate(self, request: ModelRequest, operation: ModelOperation) -> None:
        self._check_scope(request.scope)
        if request.operation is not operation:
            raise ModelGatewayRejected()
        model = await self._model(request, operation)
        if request.image_bytes is not None or request.image_media_type is not None:
            image = request.image_bytes
            media_type = request.image_media_type
            if (
                operation is not ModelOperation.STRUCTURED
                or not model.supports_vision
                or type(image) is not bytes
                or not 0 < len(image) <= 4 * 1024 * 1024
                or media_type not in {"image/png", "image/jpeg"}
                or not image.startswith(
                    b"\x89PNG\r\n\x1a\n" if media_type == "image/png" else b"\xff\xd8\xff"
                )
            ):
                raise ModelGatewayRejected()
        if (
            type(request.timeout_seconds) not in {float, int}
            or not math.isfinite(request.timeout_seconds)
            or not 0 < request.timeout_seconds <= self._config.timeout_seconds
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request.idempotency_key) is None
            or (
                request.max_output_tokens is not None
                and (
                    type(request.max_output_tokens) is not int
                    or not 1 <= request.max_output_tokens <= MAX_OUTPUT_TOKENS
                    or operation is ModelOperation.EMBED
                )
            )
        ):
            raise ModelGatewayRejected()
        total_request_bytes = 0
        for text in (request.prompt, request.context):
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 262144:
                raise ModelGatewayRejected()
            total_request_bytes += len(text.encode())
            await self._check_redacted(text)
        if request.prompt_digest != text_digest(request.prompt):
            raise ModelGatewayRejected()
        if request.tools and operation is not ModelOperation.CHAT:
            raise ModelGatewayRejected()
        if request.messages and not request.tools:
            raise ModelGatewayRejected()
        for tool_spec in request.tools:
            definition = TOOL_REGISTRY.get(tool_spec.tool_id)
            if (
                definition is None
                or not definition.model_callable
                or definition.wire_name != tool_spec.wire_name
                or (request.tool_allowlist and tool_spec.tool_id not in request.tool_allowlist)
            ):
                raise ModelGatewayRejected()
        for message in request.messages:
            if (
                message.role not in {"assistant", "tool"}
                or not isinstance(message.content, str)
                or (message.role == "tool" and not message.tool_call_id)
            ):
                raise ModelGatewayRejected()
            total_request_bytes += len(message.content.encode())
            if message.content:
                await self._check_redacted(message.content)
            for call in message.tool_calls:
                total_request_bytes += len(call.arguments_json.encode())
                await self._check_redacted(call.arguments_json)
        if operation is ModelOperation.STRUCTURED and request.schema:
            # `_prepare` sends the schema as trusted instruction text too.
            total_request_bytes += len(structured_schema_instruction(request.schema).encode())
        if total_request_bytes > 262144:
            raise ModelGatewayRejected()
        governed = bool(request.governance_digests or request.tool_allowlist)
        if (
            type(request.tool_allowlist) is not frozenset
            or not request.tool_allowlist <= known_tool_ids()
            or type(request.governance_digests) is not tuple
            or any(
                not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
                for digest in request.governance_digests
            )
            or (
                governed
                and (
                    # A governed request may run as STRUCTURED (closed JSON schema,
                    # e.g. the test-design/Insights capability) or as CHAT with no
                    # schema (PR 3: a governed/pinned-template grounded answer
                    # streams Markdown with inline `[S1]`/`[R1]` markers instead of
                    # requesting JSON) -- never any other operation.
                    operation not in (ModelOperation.STRUCTURED, ModelOperation.CHAT)
                    or "knowledge.answer" not in request.tool_allowlist
                    or not request.governance_digests
                )
            )
        ):
            raise ModelGatewayRejected()
        if operation is ModelOperation.STRUCTURED:
            if not request.schema or request.schema_digest != schema_digest(request.schema):
                raise ModelGatewayRejected()
            try:
                check_schema(request.schema)
            except ValueError:
                raise ModelGatewayRejected() from None
            serialized_schema = json.dumps(request.schema, sort_keys=True, separators=(",", ":"))
            await self._check_redacted(serialized_schema)
        elif request.schema is not None or request.schema_digest is not None:
            raise ModelGatewayRejected()

    async def _model(self, request: ModelRequest, operation: ModelOperation) -> LiteLLMModel:
        """Resolve the requested model against the LiteLLM catalog; never substitute another."""

        roles = self._config.roles
        if operation is ModelOperation.EMBED and request.alias != roles.embedding_model:
            raise ModelGatewayRejected()
        model = (await self._catalog.routes()).get(request.alias)
        expected_mode = "embedding" if operation is ModelOperation.EMBED else "chat"
        wants_image = request.image_bytes is not None
        role_models = {roles.default_chat_model, roles.embedding_model}
        if wants_image and roles.vision_model is not None:
            role_models.add(roles.vision_model)
        # A misconfigured role is an outage; any other unknown or unfit model is a bad request.
        role_request = request.alias in role_models
        if model is None or model.mode != expected_mode:
            if role_request:
                raise ModelGatewayUnavailable()
            raise ModelGatewayRejected()
        if wants_image and request.alias == roles.vision_model and not model.supports_vision:
            raise ModelGatewayUnavailable()
        if operation is ModelOperation.STRUCTURED and not model.supports_response_schema:
            raise ModelGatewayRejected()
        return model

    def _ensure_client(self) -> httpx.AsyncClient:
        """The lazily-created transport shared by `_post` and `_post_stream`."""

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout_seconds,
                limits=httpx.Limits(
                    max_connections=self._config.max_concurrency,
                    max_keepalive_connections=self._config.max_concurrency,
                ),
                transport=httpx.AsyncHTTPTransport(retries=0),
            )
        return self._client

    def _encode_payload(self, payload: dict[str, Any], *, size_limit: int) -> bytes:
        encoded = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(encoded) > size_limit:
            raise ModelGatewayRejected()
        return encoded

    def _check_timeout_seconds(self, request: ModelRequest) -> None:
        if (
            type(request.timeout_seconds) not in {int, float}
            or not math.isfinite(request.timeout_seconds)
            or not 0 < request.timeout_seconds <= self._config.timeout_seconds
        ):
            raise ModelGatewayRejected()

    async def _prepare(
        self, request: ModelRequest, operation: ModelOperation
    ) -> tuple[ModelRequest, dict[str, Any]]:
        """Validate (incl. redaction) and build the provider payload; shared by the
        non-streaming and streaming call paths."""

        # Callers cannot mutate the schema after its digest is checked.
        if request.schema is not None:
            snapshot = json.dumps(request.schema, allow_nan=False)
            if len(snapshot.encode()) > 262144:
                raise ModelGatewayRejected()
            request = replace(request, schema=json.loads(snapshot))
        await self._validate(request, operation)
        payload: dict[str, Any] = {
            "model": request.alias,
            "metadata": {
                "enterprise_id": request.scope.enterprise_id,
                "project_id": request.scope.project_id,
                "operation": operation.value,
                "prompt_digest": request.prompt_digest,
                "context_digest": text_digest(request.context),
                "schema_digest": request.schema_digest,
                "idempotency_key": request.idempotency_key,
                "tool_allowlist": sorted(request.tool_allowlist),
                "governance_digests": list(request.governance_digests),
                "image_digest": (
                    None
                    if request.image_bytes is None
                    else "sha256:" + hashlib.sha256(request.image_bytes).hexdigest()
                ),
            },
        }
        if operation is ModelOperation.EMBED:
            payload.update(
                input=request.context,
                dimensions=self._config.embedding_dimension,
                encoding_format="float",
            )
        else:
            user_content: str | list[dict[str, Any]] = request.context
            if request.image_bytes is not None:
                user_content = [
                    {"type": "text", "text": request.context},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:"
                            + str(request.image_media_type)
                            + ";base64,"
                            + base64.b64encode(request.image_bytes).decode("ascii")
                        },
                    },
                ]
            system_prompt = request.prompt
            if operation is ModelOperation.STRUCTURED:
                # Send the validated, redaction-checked schema snapshot in the
                # trusted instructions: JSON-object mode (vision) guarantees
                # JSON, not the caller's schema, and DashScope's `json_schema`
                # mode constrains decoding without showing the schema to the
                # model (prompt tokens do not grow with it), so enum choices
                # and field meanings were otherwise invisible.
                # Invariant: only platform-owned schemas may reach STRUCTURED,
                # because the schema becomes trusted instruction text.
                system_prompt += structured_schema_instruction(request.schema)
            payload.update(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=(
                    request.max_output_tokens
                    if request.max_output_tokens is not None
                    else VISION_OUTPUT_TOKENS
                    if request.image_bytes is not None
                    else DEFAULT_OUTPUT_TOKENS
                ),
            )
            if operation is ModelOperation.STRUCTURED:
                payload["temperature"] = 0
                payload["response_format"] = (
                    {"type": "json_object"}
                    if request.image_bytes is not None
                    else {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "governed_output",
                            "schema": request.schema,
                            "strict": True,
                        },
                    }
                )
            if request.tools:
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": spec.wire_name,
                            "description": spec.description,
                            "parameters": dict(spec.parameters),
                        },
                    }
                    for spec in request.tools
                ]
                payload["tool_choice"] = "auto"
            for message in request.messages:
                entry: dict[str, Any] = {"role": message.role, "content": message.content}
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments_json},
                        }
                        for call in message.tool_calls
                    ]
                if message.tool_call_id is not None:
                    entry["tool_call_id"] = message.tool_call_id
                payload["messages"].append(entry)
        return request, payload

    def _settle_meter(
        self, meter: Any, attempts: GatewayAttempts, cost_usd: Decimal | None
    ) -> None:
        if meter is not None and attempts.count > 0:
            cost_micros = (
                int((cost_usd * 1_000_000).to_integral_value(rounding=ROUND_HALF_UP))
                if cost_usd is not None
                else 0
            )
            meter.after_call(cost_micros)

    def _build_record(
        self,
        *,
        call_id: str,
        request: ModelRequest,
        operation: ModelOperation,
        current_span: Any,
        started: float,
        attempts: GatewayAttempts,
        status: str,
        error_code: str | None,
        upstream_model: str | None,
        provider: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cost_usd: Decimal | None,
        gateway_call_id: str | None,
        provider_request_id: str | None,
        payload: dict[str, Any] | None,
        response_text: str | None,
        reasoning_text: str | None,
    ) -> ModelCallRecord:
        span_context = current_span.get_span_context()
        binding = current_binding()
        return ModelCallRecord(
            call_id=call_id,
            scope=request.scope,
            trace_id=(format(span_context.trace_id, "032x") if span_context.is_valid else None),
            span_id=(format(span_context.span_id, "016x") if span_context.is_valid else None),
            turn_id=binding.turn_id,
            job_id=binding.job_id,
            operation=operation,
            model_name=request.alias,
            upstream_model=upstream_model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=int((time.monotonic() - started) * 1000),
            attempts=attempts.count,
            status=status,  # type: ignore[arg-type]
            error_code=error_code,
            gateway_call_id=gateway_call_id,
            provider_request_id=provider_request_id,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            request_json=_request_json_for_record(payload, request, operation),
            response_text=response_text,
            reasoning_text=reasoning_text,
        )

    async def _write_record(self, record: ModelCallRecord) -> None:
        if self._recorder is not None:
            try:
                await self._recorder.record(record)
            except Exception:
                logger.warning("failed to record model call", exc_info=True)

    async def _execute(self, request: ModelRequest, operation: ModelOperation) -> ModelResult:
        meters_this_call = operation in (ModelOperation.CHAT, ModelOperation.STRUCTURED)
        meter = current_meter() if meters_this_call else None
        if meter is not None:
            # Must run before any provider I/O and outside the span/record
            # machinery below: an exhausted budget propagates unchanged and
            # produces no ModelCallRecord.
            meter.before_call()

        call_id = str(uuid4())
        span_kind = "embeddings" if operation is ModelOperation.EMBED else "chat"
        attempts = GatewayAttempts()
        started = time.monotonic()
        payload: dict[str, Any] | None = None
        response_text: str | None = None
        reasoning_text: str | None = None
        cost_usd: Decimal | None = None
        gateway_call_id: str | None = None
        provider_request_id: str | None = None
        upstream_model: str | None = None
        provider: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        status: str = "error"
        error_holder = _ErrorCode()

        with span(
            f"{span_kind} {request.alias}",
            {
                "gen_ai.operation.name": span_kind,
                "gen_ai.request.model": request.alias,
                "tap.model_call_id": call_id,
            },
        ) as current_span:
            try:
                with _translate_model_errors(error_holder):
                    # Includes redaction, queue time, transport, retry, and parsing in one
                    # deadline.
                    self._check_timeout_seconds(request)
                    async with asyncio.timeout(request.timeout_seconds):
                        request, payload = await self._prepare(request, operation)
                        body, headers = await self._post(request, payload, attempts)
                        cost_usd = _parse_cost_header(headers.get("x-litellm-response-cost"))
                        input_tokens, output_tokens = _reported_usage(body)
                        result = self._normalize(
                            request, body, headers, self._upstream_model(headers)
                        )
                        gateway_call_id = result.gateway_call_id
                        provider_request_id = result.provider_request_id
                        upstream_model = result.actual_model
                        provider = result.actual_provider
                        input_tokens = result.usage.input_tokens
                        output_tokens = result.usage.output_tokens
                        if operation is ModelOperation.EMBED:
                            rows = body.get("data")
                            if isinstance(rows, list):
                                vector = (
                                    rows[0].get("embedding") if isinstance(rows[0], dict) else None
                                )
                                if isinstance(vector, list):
                                    response_text = json.dumps(
                                        {"dimension": len(vector), "count": len(rows)}
                                    )
                        else:
                            message = body["choices"][0]["message"]
                            if result.tool_calls:
                                response_text = json.dumps(
                                    [
                                        {"name": call.name, "arguments": call.arguments_json}
                                        for call in result.tool_calls
                                    ]
                                )
                            else:
                                content = message.get("content")
                                if isinstance(content, str):
                                    response_text = content
                            reasoning = message.get("reasoning_content")
                            if isinstance(reasoning, str):
                                reasoning_text = reasoning
                        current_span.set_attribute("gen_ai.response.model", result.actual_model)
                        current_span.set_attribute("gen_ai.provider.name", result.actual_provider)
                        if input_tokens is not None:
                            current_span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                        if output_tokens is not None:
                            current_span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                        binding = current_binding()
                        binding.usage.input_tokens += input_tokens or 0
                        binding.usage.output_tokens += output_tokens or 0
                        status = "ok"
                        return replace(result, call_id=call_id, cost_usd=cost_usd)
            finally:
                error_code = error_holder.value
                self._settle_meter(meter, attempts, cost_usd)
                record = self._build_record(
                    call_id=call_id,
                    request=request,
                    operation=operation,
                    current_span=current_span,
                    started=started,
                    attempts=attempts,
                    status=status,
                    error_code=error_code,
                    upstream_model=upstream_model,
                    provider=provider,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    gateway_call_id=gateway_call_id,
                    provider_request_id=provider_request_id,
                    payload=payload,
                    response_text=response_text,
                    reasoning_text=reasoning_text,
                )
                await self._write_record(record)

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Stream chat text deltas, ending with the full `ModelResult`.

        Shares pre-flight validation/payload-building (`_prepare`), exception
        classification (`_translate_model_errors`), and the metering and
        `ModelCallRecord` bookkeeping (`_settle_meter`/`_build_record`/
        `_write_record`) with `_execute`. Retries (`_post_stream`) are only
        attempted before the first content delta is yielded to the caller; once
        streaming has started, any interruption raises `ModelGatewayUnavailable`
        without retrying, and the `finally` below still records exactly one
        `ModelCallRecord`.

        Deadlines (capability-4 validation D3) apply only around this
        generator's own awaits (`_prepare`, each `anext` of the inner SSE stream)
        -- never across a `yield` -- so a timeout here can only mean the provider
        stalled, not that the consumer was slow between chunks. Until the first
        token (content or tool-call delta) the request's own `timeout_seconds` is
        one absolute instant from the call's start; after it, each chunk must
        arrive within `stream_idle_seconds`, and the whole stream ends by
        `stream_max_seconds`. Inside a Turn, the graph's wall-clock budget
        cancels the call at the Turn's remaining time. The
        inner stream is driven through `contextlib.aclosing` so that an early
        consumer exit (`aclose()`/`break`) or the consumer's task being cancelled
        still closes the HTTP response and releases the concurrency slot
        immediately, rather than waiting on garbage collection.
        """

        operation = ModelOperation.CHAT
        meter = current_meter()
        if meter is not None:
            meter.before_call()

        call_id = str(uuid4())
        attempts = GatewayAttempts()
        started = time.monotonic()
        payload: dict[str, Any] | None = None
        response_text: str | None = None
        cost_usd: Decimal | None = None
        gateway_call_id: str | None = None
        provider_request_id: str | None = None
        upstream_model: str | None = None
        provider: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        status: str = "error"
        error_holder = _ErrorCode()

        with span(
            f"chat {request.alias}",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": request.alias,
                "tap.model_call_id": call_id,
            },
        ) as current_span:
            try:
                with _translate_model_errors(error_holder):
                    self._check_timeout_seconds(request)
                    loop = asyncio.get_running_loop()
                    stream_started = loop.time()
                    first_token_deadline = stream_started + request.timeout_seconds
                    overall_deadline = stream_started + self._config.stream_max_seconds
                    token_seen = False

                    async with asyncio.timeout_at(first_token_deadline):
                        request, payload = await self._prepare(request, operation)
                    payload["stream"] = True
                    payload["stream_options"] = {"include_usage": True}

                    chunks: list[str] = []
                    returned_model: str | None = None
                    finish_reason: str | None = None
                    rejected_delta = False
                    tool_call_fragments: dict[int, dict[str, str | None]] = {}
                    headers = httpx.Headers()

                    post_stream = self._post_stream(request, payload, attempts)
                    async with contextlib.aclosing(post_stream):
                        while True:
                            wait_until = (
                                min(
                                    loop.time() + self._config.stream_idle_seconds, overall_deadline
                                )
                                if token_seen
                                else min(first_token_deadline, overall_deadline)
                            )
                            try:
                                async with asyncio.timeout_at(wait_until):
                                    chunk, chunk_headers = await anext(post_stream)
                            except StopAsyncIteration:
                                break
                            headers = chunk_headers
                            model_field = chunk.get("model")
                            if returned_model is None and isinstance(model_field, str):
                                returned_model = model_field
                            delta = _chunk_delta(chunk)
                            if delta is not None:
                                content = delta.get("content")
                                if isinstance(content, str) and content:
                                    token_seen = True
                                    chunks.append(content)
                                    yield ModelTextDelta(content)
                                if delta.get("refusal"):
                                    rejected_delta = True
                                raw_tool_calls = delta.get("tool_calls")
                                if raw_tool_calls:
                                    token_seen = True
                                    if not request.tools or not isinstance(raw_tool_calls, list):
                                        rejected_delta = True
                                    else:
                                        for fragment in raw_tool_calls:
                                            if not isinstance(fragment, dict):
                                                rejected_delta = True
                                                continue
                                            index = fragment.get("index")
                                            if type(index) is not int or index < 0:
                                                rejected_delta = True
                                                continue
                                            entry = tool_call_fragments.setdefault(
                                                index, {"id": None, "name": None, "arguments": ""}
                                            )
                                            fragment_id = fragment.get("id")
                                            if isinstance(fragment_id, str) and fragment_id:
                                                entry["id"] = fragment_id
                                            function = fragment.get("function")
                                            if isinstance(function, dict):
                                                fragment_name = function.get("name")
                                                if isinstance(fragment_name, str) and fragment_name:
                                                    entry["name"] = fragment_name
                                                arguments_fragment = function.get("arguments")
                                                if isinstance(arguments_fragment, str):
                                                    entry["arguments"] = (
                                                        entry["arguments"] or ""
                                                    ) + arguments_fragment
                            reason = _chunk_finish_reason(chunk)
                            if reason is not None:
                                finish_reason = reason
                            usage_field = chunk.get("usage")
                            if isinstance(usage_field, dict):
                                input_tokens, output_tokens = _validate_usage_tokens(usage_field)

                    full_text = "".join(chunks)
                    tool_calls: tuple[ModelToolCall, ...] = ()
                    if rejected_delta:
                        raise ModelGatewayUnavailable()
                    if finish_reason == "tool_calls" and request.tools:
                        if not tool_call_fragments:
                            raise ModelGatewayUnavailable()
                        ordered = [entry for _, entry in sorted(tool_call_fragments.items())]
                        built_calls: list[ModelToolCall] = []
                        for entry in ordered:
                            entry_id = entry["id"]
                            entry_name = entry["name"]
                            if not entry_id or not entry_name:
                                raise ModelGatewayUnavailable()
                            built_calls.append(
                                ModelToolCall(entry_id, entry_name, entry["arguments"] or "")
                            )
                        tool_calls = tuple(built_calls)
                    else:
                        # A tool-call round must end with `finish_reason == "tool_calls"`;
                        # accumulated fragments under any other finish reason (notably
                        # "stop") mirror `_normalize`'s `message.tool_calls` + "stop"
                        # rejection rather than silently discarding the calls.
                        if tool_call_fragments:
                            raise ModelGatewayUnavailable()
                        if finish_reason == "length":
                            raise ModelOutputTruncated()
                        if finish_reason is not None and finish_reason != "stop":
                            raise ModelGatewayUnavailable()
                        if not full_text.strip():
                            raise ModelGatewayUnavailable()
                    response_text = (
                        json.dumps(
                            [
                                {"name": call.name, "arguments": call.arguments_json}
                                for call in tool_calls
                            ]
                        )
                        if tool_calls
                        else full_text
                    )
                    cost_usd = _parse_cost_header(headers.get("x-litellm-response-cost"))
                    upstream_model = self._upstream_model(headers)
                    actual, provider = _resolve_actual_model(returned_model, upstream_model)
                    normalized_usage = ModelUsage(input_tokens, output_tokens)
                    audit = ModelCallAudit(
                        request.scope,
                        request.alias,
                        operation,
                        request.prompt_digest,
                        request.schema_digest,
                        text_digest(request.context),
                        request.idempotency_key,
                        provider,
                        actual,
                        normalized_usage,
                        tool_allowlist=request.tool_allowlist,
                        governance_digests=request.governance_digests,
                    )
                    gateway_call_id = _safe_header(headers, "x-litellm-call-id")
                    provider_request_id = _safe_header(headers, "x-request-id")
                    current_span.set_attribute("gen_ai.response.model", actual)
                    current_span.set_attribute("gen_ai.provider.name", provider)
                    if input_tokens is not None:
                        current_span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    if output_tokens is not None:
                        current_span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                    binding = current_binding()
                    binding.usage.input_tokens += input_tokens or 0
                    binding.usage.output_tokens += output_tokens or 0
                    status = "ok"
                    yield ModelResult(
                        full_text,
                        actual,
                        normalized_usage,
                        provider,
                        audit,
                        provider_request_id,
                        gateway_call_id,
                        call_id,
                        cost_usd,
                        tool_calls,
                    )
            finally:
                error_code = error_holder.value
                self._settle_meter(meter, attempts, cost_usd)
                record = self._build_record(
                    call_id=call_id,
                    request=request,
                    operation=operation,
                    current_span=current_span,
                    started=started,
                    attempts=attempts,
                    status=status,
                    error_code=error_code,
                    upstream_model=upstream_model,
                    provider=provider,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    gateway_call_id=gateway_call_id,
                    provider_request_id=provider_request_id,
                    payload=payload,
                    response_text=response_text,
                    reasoning_text=None,
                )
                await self._write_record(record)

    async def _post_stream(
        self, request: ModelRequest, payload: dict[str, Any], attempts: GatewayAttempts
    ) -> AsyncGenerator[tuple[dict[str, Any], httpx.Headers], None]:
        """Parse the OpenAI-compatible SSE body: ``data: {...}`` lines terminated by
        ``data: [DONE]``. Retries a retryable status (5xx/408/429) or a transport
        error only before any content delta has reached the caller; once streaming
        has started, any interruption raises `ModelGatewayUnavailable` immediately.
        A body that ends without a terminating ``data: [DONE]`` line is itself an
        interruption and also raises `ModelGatewayUnavailable`."""

        client = self._ensure_client()
        encoded = self._encode_payload(payload, size_limit=262144)
        max_retries = self._config.max_retries if request.allow_retries else 0
        content_yielded = False
        for attempt in range(max_retries + 1):
            attempts.count += 1
            total_bytes = 0
            try:
                async with (
                    self._slots,
                    client.stream(
                        "POST",
                        self._config.base_url.rstrip("/") + "/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {self._config.api_key}",
                            "content-type": "application/json",
                            "idempotency-key": request.idempotency_key,
                        },
                        content=encoded,
                        # Transport reads wait as long as the stream idle bound; the
                        # first-token and idle deadlines are enforced by `chat_stream`.
                        timeout=httpx.Timeout(
                            request.timeout_seconds,
                            read=max(request.timeout_seconds, self._config.stream_idle_seconds),
                        ),
                    ) as response,
                ):
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        if attempt < max_retries and not content_yielded:
                            continue
                    response.raise_for_status()
                    headers = response.headers
                    buffer = ""
                    try:
                        async for raw in response.aiter_text():
                            total_bytes += len(raw.encode())
                            if total_bytes > 1048576:
                                raise ModelGatewayUnavailable()
                            buffer += raw
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                chunk = _parse_sse_line(line)
                                if chunk is None:
                                    continue
                                if _chunk_delta_content(chunk) is not None:
                                    content_yielded = True
                                yield chunk, headers
                        trailing = buffer.strip()
                        if trailing:
                            chunk = _parse_sse_line(trailing)
                            if chunk is not None:
                                if _chunk_delta_content(chunk) is not None:
                                    content_yielded = True
                                yield chunk, headers
                    except _SSEDone:
                        return
                    # The body ended without a terminating `data: [DONE]` line.
                    raise ModelGatewayUnavailable()
            except httpx.TransportError:
                if content_yielded or attempt == max_retries:
                    raise ModelGatewayUnavailable() from None
                continue
        raise ModelGatewayUnavailable()

    async def _post(
        self, request: ModelRequest, payload: dict[str, Any], attempts: GatewayAttempts
    ) -> tuple[dict[str, Any], httpx.Headers]:
        client = self._ensure_client()
        encoded = self._encode_payload(
            payload, size_limit=6 * 1024 * 1024 if request.image_bytes is not None else 262144
        )
        path = (
            "v1/embeddings" if request.operation is ModelOperation.EMBED else "v1/chat/completions"
        )
        max_retries = self._config.max_retries if request.allow_retries else 0
        for attempt in range(max_retries + 1):
            attempts.count += 1
            try:
                async with (
                    self._slots,
                    client.stream(
                        "POST",
                        self._config.base_url.rstrip("/") + "/" + path,
                        headers={
                            "authorization": f"Bearer {self._config.api_key}",
                            "content-type": "application/json",
                            "idempotency-key": request.idempotency_key,
                        },
                        content=encoded,
                        timeout=request.timeout_seconds,
                    ) as response,
                ):
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        if attempt < max_retries:
                            continue
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1048576:
                            raise ModelGatewayUnavailable()
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ModelGatewayUnavailable()
                    return body, response.headers
            except httpx.TransportError:
                if attempt == max_retries:
                    raise ModelGatewayUnavailable() from None
        raise ModelGatewayUnavailable()

    def _upstream_model(self, headers: httpx.Headers) -> str | None:
        """Map LiteLLM's served deployment id to its upstream model from cached routes only."""

        deployment_id = headers.get("x-litellm-model-id")
        routes = self._catalog.cached_routes()
        if not deployment_id or routes is None:
            return None
        return routes.upstream_model(deployment_id)

    def _normalize(
        self,
        request: ModelRequest,
        body: dict[str, Any],
        headers: httpx.Headers,
        upstream_model: str | None = None,
    ) -> ModelResult:
        # LiteLLM may echo the model without its provider prefix; the deployment id is exact.
        actual, provider = _resolve_actual_model(body["model"], upstream_model)
        usage = body.get("usage", {})
        if not isinstance(usage, dict):
            raise ModelGatewayUnavailable()
        normalized_usage = ModelUsage(*_validate_usage_tokens(usage))
        output: str | tuple[float, ...] | dict[str, object]
        tool_calls: tuple[ModelToolCall, ...] = ()
        if request.operation is ModelOperation.EMBED:
            rows = body["data"]
            if (
                not isinstance(rows, list)
                or len(rows) != 1
                or not isinstance(rows[0], dict)
                or rows[0].get("index", 0) != 0
            ):
                raise ModelGatewayUnavailable()
            vector = rows[0]["embedding"]
            if (
                not isinstance(vector, list)
                or len(vector) != self._config.embedding_dimension
                or any(
                    type(value) not in {int, float} or not math.isfinite(value) for value in vector
                )
            ):
                raise ModelGatewayUnavailable()
            output = tuple(float(value) for value in vector)
        else:
            choices = body["choices"]
            if (
                isinstance(choices, list)
                and len(choices) == 1
                and isinstance(choices[0], dict)
                and choices[0].get("finish_reason") == "length"
            ):
                raise ModelOutputTruncated()
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
                or not isinstance(choices[0].get("message"), dict)
            ):
                raise ModelGatewayUnavailable()
            message = choices[0]["message"]
            finish_reason = choices[0].get("finish_reason", "stop")
            if message.get("refusal"):
                raise ModelGatewayUnavailable()
            if finish_reason == "tool_calls":
                if not request.tools:
                    raise ModelGatewayUnavailable()
                tool_calls = _parse_message_tool_calls(message.get("tool_calls"))
                content = message.get("content")
                output = content if isinstance(content, str) else ""
            else:
                if finish_reason != "stop" or message.get("tool_calls"):
                    raise ModelGatewayUnavailable()
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ModelGatewayUnavailable()
                output = content
                if request.operation is ModelOperation.STRUCTURED:
                    try:
                        output = json.loads(content)
                    except json.JSONDecodeError as error:
                        if error.msg.startswith("Unterminated string") or error.pos >= len(
                            content.rstrip()
                        ):
                            raise ModelOutputTruncated() from None
                        raise
                    if not isinstance(output, dict):
                        raise ModelGatewayUnavailable()
                    assert request.schema is not None
                    try:
                        validate_output(request.schema, output)
                    except StructuredOutputOverflow:
                        raise ModelOutputTruncated() from None
        audit = ModelCallAudit(
            request.scope,
            request.alias,
            request.operation,
            request.prompt_digest,
            request.schema_digest,
            text_digest(request.context),
            request.idempotency_key,
            provider,
            actual,
            normalized_usage,
            tool_allowlist=request.tool_allowlist,
            governance_digests=request.governance_digests,
            image_digest=(
                None
                if request.image_bytes is None
                else "sha256:" + hashlib.sha256(request.image_bytes).hexdigest()
            ),
        )

        return ModelResult(
            output,
            actual,
            normalized_usage,
            provider,
            audit,
            _safe_header(headers, "x-request-id"),
            _safe_header(headers, "x-litellm-call-id"),
            tool_calls=tool_calls,
        )
