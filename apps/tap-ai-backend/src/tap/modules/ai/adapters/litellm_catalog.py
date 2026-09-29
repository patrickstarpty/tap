"""Model catalog sourced from LiteLLM's `GET /v1/model/info`, cached with a bounded TTL."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from tap.modules.ai.domain.models import ModelGatewayUnavailable

_SUPPORTED_MODES = ("chat", "embedding")


@dataclass(frozen=True, slots=True)
class LiteLLMModel:
    name: str
    display_name: str
    mode: Literal["chat", "embedding"]
    supports_vision: bool
    supports_response_schema: bool
    supports_function_calling: bool


@dataclass(frozen=True, slots=True)
class LiteLLMRoutes:
    models: Mapping[str, LiteLLMModel]
    # LiteLLM deployment id (`model_info.id`, echoed as `x-litellm-model-id`) → upstream model.
    deployments: Mapping[str, str] = field(default_factory=dict)

    def get(self, name: str) -> LiteLLMModel | None:
        return self.models.get(name)

    def upstream_model(self, deployment_id: str) -> str | None:
        return self.deployments.get(deployment_id)


@dataclass(frozen=True, slots=True)
class ModelRoles:
    default_chat_model: str
    embedding_model: str
    vision_model: str | None

    def problems(self, routes: LiteLLMRoutes) -> tuple[str, ...]:
        problems: list[str] = []
        problems.extend(
            self._role_problems(
                "TAPPER_DEFAULT_CHAT_MODEL", self.default_chat_model, "chat", routes
            )
        )
        problems.extend(
            self._role_problems("TAPPER_EMBEDDING_MODEL", self.embedding_model, "embedding", routes)
        )
        if self.vision_model is not None:
            problems.extend(
                self._role_problems("TAPPER_VISION_MODEL", self.vision_model, "chat", routes)
            )
            model = routes.get(self.vision_model)
            if model is not None and model.mode == "chat" and not model.supports_vision:
                problems.append(f"TAPPER_VISION_MODEL={self.vision_model} does not support vision")
        return tuple(problems)

    def _role_problems(
        self, variable: str, model_name: str, expected_mode: str, routes: LiteLLMRoutes
    ) -> list[str]:
        model = routes.get(model_name)
        if model is None:
            return [f"{variable}={model_name} is not a known model"]
        if model.mode != expected_mode:
            return [f"{variable}={model_name} has mode {model.mode}, expected {expected_mode}"]
        return []


def validate_litellm_base_url(base_url: str) -> None:
    url = urlsplit(base_url)
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
        raise ValueError("LiteLLM requires HTTPS or loopback HTTP")


def _flag(model_info: dict[str, Any], key: str) -> bool:
    value = model_info.get(key)
    return value is True


def _merge(entries: list[dict[str, Any]]) -> LiteLLMModel | None:
    modes: set[str] = set()
    display_name = ""
    supports_vision = True
    supports_response_schema = True
    supports_function_calling = True
    name = ""
    for entry in entries:
        name = str(entry.get("model_name", ""))
        model_info = entry.get("model_info")
        if not isinstance(model_info, dict):
            model_info = {}
        mode = model_info.get("mode")
        if mode not in _SUPPORTED_MODES:
            continue
        modes.add(mode)
        candidate = model_info.get("tapper_display_name")
        if not display_name and isinstance(candidate, str) and candidate.strip():
            display_name = candidate
        supports_vision = supports_vision and _flag(model_info, "supports_vision")
        supports_response_schema = supports_response_schema and _flag(
            model_info, "supports_response_schema"
        )
        supports_function_calling = supports_function_calling and _flag(
            model_info, "supports_function_calling"
        )
    if len(modes) != 1:
        return None
    return LiteLLMModel(
        name,
        display_name or name,
        next(iter(modes)),  # type: ignore[arg-type]
        supports_vision,
        supports_response_schema,
        supports_function_calling,
    )


def _parse_routes(body: dict[str, Any]) -> LiteLLMRoutes:
    rows = body.get("data")
    if not isinstance(rows, list):
        raise ModelGatewayUnavailable()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ModelGatewayUnavailable()
        name = row.get("model_name")
        if not isinstance(name, str) or not name:
            raise ModelGatewayUnavailable()
        grouped.setdefault(name, []).append(row)
    models: dict[str, LiteLLMModel] = {}
    for name, entries in grouped.items():
        merged = _merge(entries)
        if merged is not None:
            models[name] = merged
    deployments: dict[str, str] = {}
    for row in rows:
        model_info = row.get("model_info")
        params = row.get("litellm_params")
        deployment_id = model_info.get("id") if isinstance(model_info, dict) else None
        upstream = params.get("model") if isinstance(params, dict) else None
        if (
            isinstance(deployment_id, str)
            and deployment_id
            and isinstance(upstream, str)
            and upstream
            and len(upstream) <= 256
        ):
            deployments[deployment_id] = upstream
    return LiteLLMRoutes(models, deployments)


class LiteLLMCatalog:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        validate_litellm_base_url(base_url)
        if not api_key or len(api_key) > 4096:
            raise ValueError("model catalog requires a bounded credential")
        if ttl_seconds <= 0:
            raise ValueError("model catalog requires a positive ttl")
        self._base_url = base_url
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._routes: LiteLLMRoutes | None = None
        self._fetched_at: float | None = None

    async def routes(self) -> LiteLLMRoutes:
        now = self._clock()
        if self._routes is not None and self._fetched_at is not None:
            if now - self._fetched_at < self._ttl_seconds:
                return self._routes
        async with self._lock:
            now = self._clock()
            if (
                self._routes is not None
                and self._fetched_at is not None
                and now - self._fetched_at < self._ttl_seconds
            ):
                return self._routes
            try:
                routes = await self._fetch()
            except (
                httpx.HTTPError,
                ValueError,
                TypeError,
                KeyError,
                ModelGatewayUnavailable,
            ):
                if self._routes is not None:
                    return self._routes
                raise ModelGatewayUnavailable() from None
            self._routes = routes
            self._fetched_at = self._clock()
            return routes

    async def _fetch(self) -> LiteLLMRoutes:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                transport=httpx.AsyncHTTPTransport(retries=0),
            )
        async with self._client.stream(
            "GET",
            self._base_url.rstrip("/") + "/v1/model/info",
            headers={"authorization": f"Bearer {self._api_key}"},
        ) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 1048576:
                    raise ModelGatewayUnavailable()
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ModelGatewayUnavailable()
            return _parse_routes(body)

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
