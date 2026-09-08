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
class LiteLLMModelGatewayConfig:
    base_url: str
    api_key: str = field(repr=False)
    chat_alias: str
    embedding_alias: str
    chat_model: str = field(repr=False)
    embedding_model: str = field(repr=False)
    embedding_dimension: int
    timeout_seconds: float = 15.0
    max_retries: int = 1
    disabled_aliases: frozenset[str] = frozenset()

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
        for value in (self.chat_alias, self.embedding_alias, self.chat_model, self.embedding_model):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value) is None:
                raise ValueError("model gateway requires fixed bounded routes")
        if self.chat_alias == self.embedding_alias or not self.disabled_aliases <= {
            self.chat_alias,
            self.embedding_alias,
        }:
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
        descriptors = (
            ModelDescriptor(
                self._config.chat_alias,
                "GPT-5.6 Sol",
                frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
            ),
            ModelDescriptor(
                self._config.embedding_alias,
                "Tapper embeddings",
                frozenset({ModelCapability.EMBED}),
            ),
        )
        return tuple(
            item for item in descriptors if item.alias not in self._config.disabled_aliases
        )

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
        alias = (
            self._config.embedding_alias
            if operation is ModelOperation.EMBED
            else self._config.chat_alias
        )
        if (
            request.operation is not operation
            or request.alias != alias
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
        configured = (
            self._config.embedding_model
            if request.operation is ModelOperation.EMBED
            else self._config.chat_model
        )
        actual = body["model"]
        # An echoed logical alias is not evidence of an actual upstream model.
        if actual not in {configured, configured.rsplit("/", 1)[-1]}:
            raise ModelGatewayUnavailable()
        group = headers.get("x-litellm-model-group")
        if group is not None and group != request.alias:
            raise ModelGatewayUnavailable()
        provider = configured.split("/", 1)[0]
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
