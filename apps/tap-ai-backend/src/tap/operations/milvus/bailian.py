"""Strict direct Bailian adapter for the bounded embedding research runner only."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx

from tap.modules.knowledge.ports.models import Embedding, EmbeddingUsage

EMBEDDING_ALIAS = "research-embedding-v1"
EMBEDDING_DIMENSION = 1536
PROVIDER_MODEL = "text-embedding-v4"
UNIT_PRICE_CNY_PER_1000_INPUT_TOKENS = Decimal("0.0005")
PRICING_SOURCE = "official_rate_2026-08-27"
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_BEIJING_WORKSPACE_HOST = re.compile(r"ws-[a-z0-9]{8,64}\.cn-beijing\.maas\.aliyuncs\.com\Z")
_HTTP_CLIENT_LOGGER_NAMES = (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)


class BailianEmbeddingUnavailable(Exception):
    """The direct bounded research route did not return a safe embedding."""


@dataclass(frozen=True, slots=True)
class BailianEmbeddingConfig:
    api_base: str = field(repr=False)
    api_key: str = field(repr=False)
    deadline_seconds: float = 15.0
    max_response_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if not _valid_api_base(self.api_base):
            raise ValueError("Bailian research API base is invalid")
        if (
            not isinstance(self.api_key, str)
            or not self.api_key
            or len(self.api_key) > 256
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in self.api_key)
        ):
            raise ValueError("Bailian research API key is invalid")
        if (
            type(self.deadline_seconds) not in {int, float}
            or isinstance(self.deadline_seconds, bool)
            or not math.isfinite(self.deadline_seconds)
            or not 0 < self.deadline_seconds <= 60
        ):
            raise ValueError("Bailian research deadline is invalid")
        if (
            type(self.max_response_bytes) is not int
            or not 256 <= self.max_response_bytes <= 4_194_304
        ):
            raise ValueError("Bailian research response bound is invalid")


class BailianEmbeddingAdapter:
    """Implement ModelPort embedding through one direct OpenAI-compatible route."""

    def __init__(
        self,
        config: BailianEmbeddingConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.deadline_seconds),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    @property
    def embedding_model_id(self) -> str:
        return EMBEDDING_ALIAS

    @property
    def embedding_dimension(self) -> int:
        return EMBEDDING_DIMENSION

    async def embed(self, query: str) -> Embedding:
        if not isinstance(query, str) or not query or len(query) > 8_000:
            raise BailianEmbeddingUnavailable("Bailian research input is invalid")
        payload = {
            "model": PROVIDER_MODEL,
            "input": query,
            "dimensions": EMBEDDING_DIMENSION,
            "encoding_format": "float",
        }
        try:
            with _redact_http_client_endpoint_logs(self._config.api_base):
                async with asyncio.timeout(self._config.deadline_seconds):
                    async with self._client.stream(
                        "POST",
                        self._config.api_base + "/embeddings",
                        headers={
                            "authorization": f"Bearer {self._config.api_key}",
                            "content-type": "application/json",
                        },
                        content=json.dumps(
                            payload,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                        ).encode("utf-8"),
                    ) as response:
                        if response.status_code != 200:
                            raise BailianEmbeddingUnavailable(
                                "Bailian research provider rejected the request"
                            )
                        raw = await _read_bounded(response, self._config.max_response_bytes)
                        request_id = response.headers.get("x-request-id")
                    return _parse_response(raw, request_id)
        except TimeoutError:
            raise BailianEmbeddingUnavailable("Bailian research exceeded the deadline") from None
        except httpx.TransportError:
            raise BailianEmbeddingUnavailable("Bailian research transport failed") from None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > maximum:
            raise BailianEmbeddingUnavailable("Bailian research response exceeds the bound")
        body.extend(chunk)
    return bytes(body)


def _parse_response(raw: bytes, request_id: str | None) -> Embedding:
    try:
        body = json.loads(
            raw,
            parse_constant=_reject_constant,
            object_pairs_hook=_closed_pairs,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise BailianEmbeddingUnavailable("Bailian research response is malformed") from None
    if (
        not isinstance(body, dict)
        or set(body) != {"id", "object", "data", "model", "usage"}
        or body["object"] != "list"
        or body["model"] != PROVIDER_MODEL
        or not isinstance(request_id, str)
        or _REQUEST_ID.fullmatch(request_id) is None
        or body["id"] != request_id
    ):
        raise BailianEmbeddingUnavailable("Bailian research response is malformed")
    data = body["data"]
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise BailianEmbeddingUnavailable("Bailian research response is malformed")
    record = data[0]
    if (
        set(record) != {"object", "index", "embedding"}
        or record["object"] != "embedding"
        or record["index"] != 0
    ):
        raise BailianEmbeddingUnavailable("Bailian research response is malformed")
    raw_vector = record["embedding"]
    if (
        not isinstance(raw_vector, list)
        or len(raw_vector) != EMBEDDING_DIMENSION
        or any(type(value) is not float or not math.isfinite(value) for value in raw_vector)
    ):
        raise BailianEmbeddingUnavailable("Bailian research vector is malformed")
    usage = body["usage"]
    if not isinstance(usage, dict) or set(usage) != {"prompt_tokens", "total_tokens"}:
        raise BailianEmbeddingUnavailable("Bailian research usage is malformed")
    input_tokens = usage["prompt_tokens"]
    total_tokens = usage["total_tokens"]
    if (
        type(input_tokens) is not int
        or type(total_tokens) is not int
        or not 0 <= input_tokens <= 1_000_000
        or total_tokens != input_tokens
    ):
        raise BailianEmbeddingUnavailable("Bailian research usage is malformed")
    calculated_cost = Decimal(input_tokens) * UNIT_PRICE_CNY_PER_1000_INPUT_TOKENS / 1000
    return Embedding(
        vector=tuple(raw_vector),
        model_id=EMBEDDING_ALIAS,
        provider_request_id=request_id,
        provider_model_id=PROVIDER_MODEL,
        usage=EmbeddingUsage(
            input_tokens=input_tokens,
            total_tokens=total_tokens,
            response_cost_usd=None,
            calculated_cost_cny=calculated_cost,
        ),
    )


def _closed_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


class _EndpointRedactionFilter(logging.Filter):
    def __init__(self, api_base: str) -> None:
        super().__init__()
        hostname = urlsplit(api_base).hostname
        if hostname is None:
            raise ValueError("Bailian research API base is invalid")
        self._sensitive_values = (api_base, hostname)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            return True
        redacted = message
        for sensitive in self._sensitive_values:
            redacted = redacted.replace(sensitive, "[redacted-bailian-endpoint]")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


@contextmanager
def _redact_http_client_endpoint_logs(api_base: str) -> Iterator[None]:
    loggers = tuple(logging.getLogger(name) for name in _HTTP_CLIENT_LOGGER_NAMES)
    redaction_filter = _EndpointRedactionFilter(api_base)
    for logger in loggers:
        logger.addFilter(redaction_filter)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(redaction_filter)


def _valid_api_base(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    hostname = parsed.hostname
    return (
        parsed.scheme == "https"
        and hostname is not None
        and _BEIJING_WORKSPACE_HOST.fullmatch(hostname) is not None
        and parsed.netloc == hostname
        and parsed.username is None
        and parsed.password is None
        and port is None
        and parsed.path == "/compatible-mode/v1"
        and parsed.query == ""
        and parsed.fragment == ""
    )
