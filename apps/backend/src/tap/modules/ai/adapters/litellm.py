"""Single bounded model transport for all V1 model operations."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

import httpx

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.schema import check_schema, validate_output
from tap.modules.ai.domain.models import (
    ModelCallAudit,
    ModelCapability,
    ModelDescriptor,
    ModelGatewayRejected,
    ModelGatewayUnavailable,
    ModelOperation,
    ModelRequest,
    ModelResult,
    ModelUsage,
    schema_digest,
    text_digest,
)

Redact = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ProviderModelMapping:
    """Explicit private upstream identity, distinct from a governed routing alias."""

    provider: str = field(repr=False)
    model: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.provider) is None
            or not isinstance(self.model, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}", self.model) is None
        ):
            raise ValueError("model gateway requires an explicit provider/model mapping")

    @classmethod
    def from_route(cls, route: str) -> ProviderModelMapping:
        if not isinstance(route, str) or "/" not in route:
            raise ValueError("model gateway requires an explicit provider/model mapping")
        provider, model = route.split("/", 1)
        return cls(provider, model)

    @property
    def route(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True, slots=True)
class ChatModelRoute:
    """One approved browser-facing alias bound to one private upstream model."""

    alias: str
    display_name: str
    target: ProviderModelMapping = field(repr=False)

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", self.alias) is None
            or not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 128
            or not isinstance(self.target, ProviderModelMapping)
        ):
            raise ValueError("chat model route must be a bounded approved mapping")


@dataclass(frozen=True, slots=True)
class LiteLLMModelGatewayConfig:
    base_url: str
    api_key: str = field(repr=False)
    chat_alias: str
    embedding_alias: str
    chat_model: ProviderModelMapping = field(repr=False)
    embedding_model: ProviderModelMapping = field(repr=False)
    embedding_dimension: int
    timeout_seconds: float = 15.0
    max_retries: int = 1
    disabled_aliases: frozenset[str] = frozenset()
    additional_chat_models: tuple[ChatModelRoute, ...] = ()

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if (
            url.username
            or url.password
            or url.query
            or url.fragment
            or not url.hostname
            or (
                url.scheme != "https"
                and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        ):
            raise ValueError("model gateway requires HTTPS or loopback HTTP")
        if not self.api_key or len(self.api_key) > 4096:
            raise ValueError("model gateway requires a bounded credential")
        for value in (self.chat_alias, self.embedding_alias):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value) is None:
                raise ValueError("model gateway requires fixed bounded routes")
        chat_routes = (
            ChatModelRoute(self.chat_alias, "Qwen Plus", self.chat_model),
            *self.additional_chat_models,
        )
        aliases = {item.alias for item in chat_routes} | {self.embedding_alias}
        if len(aliases) != len(chat_routes) + 1:
            raise ValueError("model gateway aliases must be disjoint")
        targets = (
            self.chat_model,
            self.embedding_model,
            *(item.target for item in chat_routes[1:]),
        )
        for target in targets:
            if (
                not isinstance(target, ProviderModelMapping)
                or target.provider in aliases
                or target.model in aliases
                or target.model.rsplit("/", 1)[-1] in aliases
                or target.route in aliases
            ):
                raise ValueError("provider/model mapping cannot use a logical alias")
        if not self.disabled_aliases <= aliases:
            raise ValueError("model gateway aliases must be disjoint")
        if type(self.embedding_dimension) is not int or not 1 <= self.embedding_dimension <= 4096:
            raise ValueError("model gateway dimension is invalid")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise ValueError("model gateway retry budget is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 60
        ):
            raise ValueError("model gateway timeout is invalid")


class LiteLLMModelGateway:
    def __init__(
        self,
        config: LiteLLMModelGatewayConfig,
        *,
        scope: ProjectScopeContext,
        redact: Redact,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if type(scope) is not ProjectScopeContext:
            raise ModelGatewayRejected()
        self._config = config
        self.scope = scope
        self._redact = redact
        self._owns_client = client is None
        self._client = client
        self._slots = asyncio.Semaphore(4)

    async def catalog(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]:
        self._check_scope(scope)
        descriptors = tuple(
            ModelDescriptor(
                item.alias,
                item.display_name,
                frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
            )
            for item in self._chat_routes()
        ) + (
            ModelDescriptor(
                self._config.embedding_alias,
                "Tapper embeddings",
                frozenset({ModelCapability.EMBED}),
            ),
        )
        return tuple(
            item for item in descriptors if item.alias not in self._config.disabled_aliases
        )

    def _chat_routes(self) -> tuple[ChatModelRoute, ...]:
        return (
            ChatModelRoute(self._config.chat_alias, "Qwen Plus", self._config.chat_model),
            *self._config.additional_chat_models,
        )

    def _chat_route(self, alias: str) -> ChatModelRoute | None:
        return next((item for item in self._chat_routes() if item.alias == alias), None)

    async def chat(self, request: ModelRequest) -> ModelResult:
        return await self._execute(request, ModelOperation.CHAT)

    async def embed(self, request: ModelRequest) -> ModelResult:
        return await self._execute(request, ModelOperation.EMBED)

    async def generate_structured(self, request: ModelRequest) -> ModelResult:
        return await self._execute(request, ModelOperation.STRUCTURED)

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

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
        alias = self._config.embedding_alias if operation is ModelOperation.EMBED else request.alias
        chat_route = None if operation is ModelOperation.EMBED else self._chat_route(request.alias)
        if (
            request.operation is not operation
            or (operation is ModelOperation.EMBED and request.alias != alias)
            or (operation is not ModelOperation.EMBED and chat_route is None)
            or alias in self._config.disabled_aliases
        ):
            raise ModelGatewayRejected()
        if (
            type(request.timeout_seconds) not in {float, int}
            or not math.isfinite(request.timeout_seconds)
            or not 0 < request.timeout_seconds <= self._config.timeout_seconds
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request.idempotency_key) is None
        ):
            raise ModelGatewayRejected()
        for text in (request.prompt, request.context):
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 262144:
                raise ModelGatewayRejected()
            await self._check_redacted(text)
        if request.prompt_digest != text_digest(request.prompt):
            raise ModelGatewayRejected()
        governed = bool(request.governance_digests or request.tool_allowlist)
        if (
            type(request.tool_allowlist) is not frozenset
            or not request.tool_allowlist <= {"knowledge.search", "knowledge.answer"}
            or type(request.governance_digests) is not tuple
            or any(
                not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
                for digest in request.governance_digests
            )
            or (
                governed
                and (
                    operation is not ModelOperation.STRUCTURED
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

    async def _execute(self, request: ModelRequest, operation: ModelOperation) -> ModelResult:
        try:
            # Includes redaction, queue time, transport, retry, and parsing in one deadline.
            if (
                type(request.timeout_seconds) not in {int, float}
                or not math.isfinite(request.timeout_seconds)
                or not 0 < request.timeout_seconds <= self._config.timeout_seconds
            ):
                raise ModelGatewayRejected()
            async with asyncio.timeout(request.timeout_seconds):
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
                    },
                }
                if operation is ModelOperation.EMBED:
                    payload.update(
                        input=request.context,
                        dimensions=self._config.embedding_dimension,
                        encoding_format="float",
                    )
                else:
                    payload.update(
                        messages=[
                            {"role": "system", "content": request.prompt},
                            {"role": "user", "content": request.context},
                        ],
                        max_tokens=2048,
                    )
                    if operation is ModelOperation.STRUCTURED:
                        payload["temperature"] = 0
                        payload["response_format"] = {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "governed_output",
                                "schema": request.schema,
                                "strict": True,
                            },
                        }
                body, headers = await self._post(request, payload)
                return self._normalize(request, body, headers)
        except ModelGatewayRejected:
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
            raise ModelGatewayUnavailable() from None

    async def _post(
        self, request: ModelRequest, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], httpx.Headers]:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout_seconds,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
                transport=httpx.AsyncHTTPTransport(retries=0),
            )
        encoded = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(encoded) > 262144:
            raise ModelGatewayRejected()
        path = (
            "v1/embeddings" if request.operation is ModelOperation.EMBED else "v1/chat/completions"
        )
        for attempt in range(self._config.max_retries + 1):
            try:
                async with (
                    self._slots,
                    self._client.stream(
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
                        if attempt < self._config.max_retries:
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
                if attempt == self._config.max_retries:
                    raise ModelGatewayUnavailable() from None
        raise ModelGatewayUnavailable()

    def _normalize(
        self, request: ModelRequest, body: dict[str, Any], headers: httpx.Headers
    ) -> ModelResult:
        route = self._chat_route(request.alias)
        mapping = (
            self._config.embedding_model
            if request.operation is ModelOperation.EMBED
            else (route.target if route is not None else self._config.chat_model)
        )
        returned_model = body["model"]
        aliases = {item.alias for item in self._chat_routes()} | {self._config.embedding_alias}
        deployment_id = headers.get("x-litellm-model-id")
        if returned_model in aliases:
            if returned_model != request.alias or deployment_id != mapping.route:
                raise ModelGatewayUnavailable()
        elif returned_model not in {mapping.route, mapping.model}:
            raise ModelGatewayUnavailable()
        actual = mapping.route
        group = headers.get("x-litellm-model-group")
        if group is not None and group != request.alias:
            raise ModelGatewayUnavailable()
        provider = mapping.provider
        usage = body.get("usage", {})
        if not isinstance(usage, dict):
            raise ModelGatewayUnavailable()
        tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        if any(
            value is not None and (type(value) is not int or not 0 <= value <= 1000000)
            for value in tokens
        ):
            raise ModelGatewayUnavailable()
        normalized_usage = ModelUsage(*tokens)
        output: str | tuple[float, ...] | dict[str, object]
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
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason", "stop") != "stop"
                or not isinstance(choices[0].get("message"), dict)
                or choices[0]["message"].get("tool_calls")
                or choices[0]["message"].get("refusal")
            ):
                raise ModelGatewayUnavailable()
            content = choices[0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ModelGatewayUnavailable()
            output = content
            if request.operation is ModelOperation.STRUCTURED:
                output = json.loads(content)
                if not isinstance(output, dict):
                    raise ModelGatewayUnavailable()
                assert request.schema is not None
                validate_output(request.schema, output)
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
        )

        def safe_header(name: str) -> str | None:
            value = headers.get(name)
            return (
                value
                if value is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", value)
                else None
            )

        return ModelResult(
            output,
            actual,
            normalized_usage,
            provider,
            audit,
            safe_header("x-request-id"),
            safe_header("x-litellm-call-id"),
        )
