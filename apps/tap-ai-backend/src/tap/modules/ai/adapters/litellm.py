"""Single bounded model transport for all V1 model operations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm_catalog import (
    LiteLLMCatalog,
    LiteLLMModel,
    ModelRoles,
    validate_litellm_base_url,
)
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
    roles: ModelRoles
    embedding_dimension: int
    timeout_seconds: float = 15.0
    max_retries: int = 1

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
        catalog: LiteLLMCatalog,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if type(scope) is not ProjectScopeContext:
            raise ModelGatewayRejected()
        self._config = config
        self.scope = scope
        self._redact = redact
        self._catalog = catalog
        self._owns_client = client is None
        self._client = client
        self._slots = asyncio.Semaphore(4)

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

    async def health_problems(self) -> tuple[str, ...]:
        try:
            routes = await self._catalog.routes()
        except ModelGatewayUnavailable:
            return ("LiteLLM model catalog unavailable",)
        return self._config.roles.problems(routes)

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

    async def _model(self, request: ModelRequest, operation: ModelOperation) -> LiteLLMModel:
        """Resolve the requested model against the LiteLLM catalog; never substitute another."""

        roles = self._config.roles
        if operation is ModelOperation.EMBED and request.alias != roles.embedding_model:
            raise ModelGatewayRejected()
        model = (await self._catalog.routes()).get(request.alias)
        expected_mode = "embedding" if operation is ModelOperation.EMBED else "chat"
        if model is None or model.mode != expected_mode:
            # A misconfigured role is an outage; any other unknown model is a bad request.
            if request.alias in {roles.default_chat_model, roles.embedding_model}:
                raise ModelGatewayUnavailable()
            raise ModelGatewayRejected()
        if operation is ModelOperation.STRUCTURED and not model.supports_response_schema:
            raise ModelGatewayRejected()
        return model

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
                    if request.image_bytes is not None:
                        # JSON-object mode guarantees JSON, not the caller's schema.
                        # Send the validated snapshot explicitly for vision providers.
                        system_prompt += (
                            "\nReturn one JSON object conforming exactly to this JSON schema; "
                            "include all required fields and no additional fields:\n"
                            + json.dumps(request.schema, sort_keys=True, separators=(",", ":"))
                        )
                    payload.update(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        max_tokens=8192 if request.image_bytes is not None else 2048,
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
        if len(encoded) > (6 * 1024 * 1024 if request.image_bytes is not None else 262144):
            raise ModelGatewayRejected()
        path = (
            "v1/embeddings" if request.operation is ModelOperation.EMBED else "v1/chat/completions"
        )
        max_retries = self._config.max_retries if request.allow_retries else 0
        for attempt in range(max_retries + 1):
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

    def _normalize(
        self, request: ModelRequest, body: dict[str, Any], headers: httpx.Headers
    ) -> ModelResult:
        actual = body["model"]
        if not isinstance(actual, str) or not actual.strip() or len(actual) > 256:
            raise ModelGatewayUnavailable()
        provider = actual.split("/", 1)[0] if "/" in actual else "unknown"
        if not provider:
            raise ModelGatewayUnavailable()
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
            image_digest=(
                None
                if request.image_bytes is None
                else "sha256:" + hashlib.sha256(request.image_bytes).hexdigest()
            ),
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
