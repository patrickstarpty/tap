"""Finite, fixed-route LiteLLM HTTP adapter."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field

import httpx

from tap.modules.knowledge.domain.models import Evidence
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
)


class ModelUnavailable(Exception):
    """The fixed LiteLLM route did not return a bounded valid response."""


@dataclass(frozen=True, slots=True)
class LiteLLMConfig:
    base_url: str
    api_key: str = field(repr=False)
    embedding_model_id: str
    answer_model_id: str
    answer_profile_id: str
    deadline_seconds: float = 15
    max_retries: int = 1
    max_connections: int = 4
    connect_timeout_seconds: float = 2
    read_timeout_seconds: float = 10

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.startswith("https://"):
            raise ValueError("LiteLLM URL must use HTTPS")
        if not all(
            (
                self.api_key,
                self.embedding_model_id,
                self.answer_model_id,
                self.answer_profile_id,
            )
        ):
            raise ValueError("LiteLLM fixed route identifiers must not be empty")
        _strict_int("max_retries", self.max_retries, minimum=0, maximum=2)
        _strict_int("max_connections", self.max_connections, minimum=1, maximum=16)
        _finite_duration("deadline_seconds", self.deadline_seconds, maximum=60)
        _finite_duration("connect_timeout_seconds", self.connect_timeout_seconds, maximum=10)
        _finite_duration("read_timeout_seconds", self.read_timeout_seconds, maximum=60)


class LiteLLMAdapter:
    """Normalize LiteLLM responses without leaking HTTP/provider types to the port."""

    def __init__(
        self,
        config: LiteLLMConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers={"authorization": f"Bearer {config.api_key}"},
            timeout=httpx.Timeout(
                connect=min(config.connect_timeout_seconds, config.deadline_seconds),
                read=min(config.read_timeout_seconds, config.deadline_seconds),
                write=min(config.read_timeout_seconds, config.deadline_seconds),
                pool=min(config.connect_timeout_seconds, config.deadline_seconds),
            ),
            limits=httpx.Limits(
                max_connections=config.max_connections,
                max_keepalive_connections=config.max_connections,
            ),
            transport=httpx.AsyncHTTPTransport(retries=0),
        )
        self._connection_slots = asyncio.Semaphore(config.max_connections)

    async def embed(self, query: str) -> Embedding:
        response = await self._post(
            "v1/embeddings",
            {"model": self._config.embedding_model_id, "input": query},
        )
        try:
            body = response.json()
            vector = tuple(float(value) for value in body["data"][0]["embedding"])
            if not vector:
                raise ValueError("embedding is empty")
            return Embedding(
                vector=vector,
                model_id=self._config.embedding_model_id,
                provider_request_id=_provider_request_id(response),
                gateway_call_id=response.headers.get("x-litellm-call-id"),
                gateway_model_id=response.headers.get("x-litellm-model-id"),
                provider_model_id=_optional_string(body.get("model")),
                completion_id=_optional_string(body.get("id")),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelUnavailable("LiteLLM returned a malformed embedding") from error

    async def answer(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        profile_id: str,
    ) -> AnswerGeneration:
        del profile_id
        evidence_payload = [
            {
                "label": item.evidence_label,
                "content": item.content,
                "sourceRevision": item.source.revision,
                "chunkContentHash": item.chunk_content_hash,
            }
            for item in evidence
        ]
        response = await self._post(
            "v1/chat/completions",
            {
                "model": self._config.answer_model_id,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Answer only from the supplied evidence. Return JSON with answer and "
                            "claims; every claim must contain one or more supplied evidenceLabels."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"query": query, "evidence": evidence_payload},
                            separators=(",", ":"),
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
            },
        )
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            generated = json.loads(content)
            claims_value = generated["claims"]
            if not isinstance(claims_value, list):
                raise TypeError("claims must be a list")
            claims = tuple(
                GeneratedClaim(
                    text=str(item["text"]),
                    evidence_labels=tuple(str(label) for label in item["evidenceLabels"]),
                )
                for item in claims_value
            )
            return AnswerGeneration(
                text=str(generated["answer"]),
                claims=claims,
                model_id=self._config.answer_model_id,
                profile_id=self._config.answer_profile_id,
                provider_request_id=_provider_request_id(response),
                gateway_call_id=response.headers.get("x-litellm-call-id"),
                gateway_model_id=response.headers.get("x-litellm-model-id"),
                provider_model_id=_optional_string(body.get("model")),
                completion_id=_optional_string(body.get("id")),
            )
        except (
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as error:
            raise ModelUnavailable("LiteLLM returned a malformed grounded answer") from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        try:
            async with asyncio.timeout(self._config.deadline_seconds):
                for attempt in range(self._config.max_retries + 1):
                    try:
                        async with self._connection_slots:
                            response = await self._client.post(
                                self._config.base_url.rstrip("/") + "/" + path,
                                json=payload,
                            )
                    except httpx.TransportError as error:
                        if attempt == self._config.max_retries:
                            raise ModelUnavailable(
                                "LiteLLM transport retry budget exhausted"
                            ) from error
                        continue
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        if attempt == self._config.max_retries:
                            raise ModelUnavailable("LiteLLM status retry budget exhausted")
                        continue
                    if response.is_error:
                        raise ModelUnavailable(
                            f"LiteLLM rejected the fixed route with HTTP {response.status_code}"
                        )
                    return response
        except TimeoutError as error:
            raise ModelUnavailable("LiteLLM exceeded the outer deadline") from error
        raise ModelUnavailable("LiteLLM retry budget exhausted")


def _provider_request_id(response: httpx.Response) -> str | None:
    for name in ("x-request-id", "x-provider-request-id", "x-openai-request-id"):
        value = _optional_string(response.headers.get(name))
        if value:
            return value
    return None


def _strict_int(name: str, value: object, *, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")


def _finite_duration(name: str, value: object, *, maximum: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= maximum
    ):
        raise ValueError(f"{name} must be finite, positive, and at most {maximum} seconds")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
