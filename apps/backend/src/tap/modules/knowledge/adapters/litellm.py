"""Finite, fixed-route LiteLLM HTTP adapter."""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from tap.modules.knowledge.domain.models import Evidence, RevisionKind, SourceRevisionRef
from tap.modules.knowledge.ports.documents import EmbeddingArtifact
from tap.modules.knowledge.ports.errors import ModelUnavailable
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    EmbeddingUsage,
    GeneratedClaim,
)

_CANONICAL_COST = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,18})?\Z")
_ATHENA_EMBEDDING_ALIAS = "athena-embedding"
_MAX_EMBEDDING_BATCH = 32
_MAX_EMBEDDING_REQUEST_BYTES = 262_144


@dataclass(frozen=True, slots=True)
class LiteLLMConfig:
    base_url: str
    api_key: str = field(repr=False)
    embedding_model_id: str
    answer_model_id: str
    answer_profile_id: str
    embedding_dimension: int
    allowed_embedding_model_labels: frozenset[str]
    allowed_answer_model_labels: frozenset[str]
    allowed_retrieval_profile_ids: frozenset[str]
    deadline_seconds: float = 15
    max_retries: int = 1
    max_connections: int = 4
    connect_timeout_seconds: float = 2
    read_timeout_seconds: float = 10
    max_request_bytes: int = 262_144
    max_response_bytes: int = 1_048_576
    max_evidence_count: int = 20
    max_evidence_content_chars: int = 100_000
    max_total_evidence_chars: int = 500_000
    max_output_tokens: int = 2_048
    max_answer_chars: int = 16_000
    max_claims: int = 64
    max_claim_chars: int = 4_000
    max_labels_per_claim: int = 16

    def __post_init__(self) -> None:
        if not _valid_litellm_url(self.base_url):
            raise ValueError("LiteLLM URL must use HTTPS or exact loopback HTTP")
        for name in (
            "api_key",
            "embedding_model_id",
            "answer_model_id",
            "answer_profile_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"{name} must be a bounded fixed-route identifier")
        _strict_string_set(
            "allowed_embedding_model_labels",
            self.allowed_embedding_model_labels,
            maximum_items=32,
        )
        _strict_string_set(
            "allowed_answer_model_labels",
            self.allowed_answer_model_labels,
            maximum_items=32,
        )
        _strict_string_set(
            "allowed_retrieval_profile_ids",
            self.allowed_retrieval_profile_ids,
            maximum_items=8,
        )
        if self.embedding_model_id not in self.allowed_embedding_model_labels:
            raise ValueError("fixed embedding alias must be included in its route allowlist")
        if self.answer_model_id not in self.allowed_answer_model_labels:
            raise ValueError("fixed answer alias must be included in its route allowlist")
        if self.allowed_embedding_model_labels & self.allowed_answer_model_labels:
            raise ValueError("embedding and answer model-label allowlists must be disjoint")
        _strict_int("embedding_dimension", self.embedding_dimension, minimum=1, maximum=4_096)
        _strict_int("max_retries", self.max_retries, minimum=0, maximum=2)
        _strict_int("max_connections", self.max_connections, minimum=1, maximum=16)
        _strict_int("max_request_bytes", self.max_request_bytes, minimum=256, maximum=2_097_152)
        _strict_int(
            "max_response_bytes",
            self.max_response_bytes,
            minimum=256,
            maximum=16_777_216,
        )
        _strict_int("max_evidence_count", self.max_evidence_count, minimum=1, maximum=100)
        _strict_int(
            "max_evidence_content_chars",
            self.max_evidence_content_chars,
            minimum=1,
            maximum=1_000_000,
        )
        _strict_int(
            "max_total_evidence_chars",
            self.max_total_evidence_chars,
            minimum=1,
            maximum=2_000_000,
        )
        _strict_int("max_output_tokens", self.max_output_tokens, minimum=1, maximum=16_384)
        _strict_int("max_answer_chars", self.max_answer_chars, minimum=1, maximum=100_000)
        _strict_int("max_claims", self.max_claims, minimum=1, maximum=256)
        _strict_int("max_claim_chars", self.max_claim_chars, minimum=1, maximum=16_000)
        _strict_int(
            "max_labels_per_claim",
            self.max_labels_per_claim,
            minimum=1,
            maximum=64,
        )
        _finite_duration("deadline_seconds", self.deadline_seconds, maximum=60)
        _finite_duration("connect_timeout_seconds", self.connect_timeout_seconds, maximum=10)
        _finite_duration("read_timeout_seconds", self.read_timeout_seconds, maximum=60)


@dataclass(frozen=True, slots=True)
class _GatewayResponse:
    body: dict[str, Any]
    provider_request_id: str | None
    gateway_call_id: str | None
    gateway_model_id: str | None
    response_cost_header: str | None


class LiteLLMAdapter:
    """Normalize bounded LiteLLM responses without leaking provider transport types."""

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

    @property
    def embedding_model_id(self) -> str:
        return self._config.embedding_model_id

    @property
    def embedding_dimension(self) -> int:
        return self._config.embedding_dimension

    async def embed(self, query: str) -> Embedding:
        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self._config.deadline_seconds
        try:
            async with asyncio.timeout_at(deadline_at):
                _bounded_string("embedding query", query, maximum=8_000)
                response = await self._post(
                    "v1/embeddings",
                    {
                        "model": self._config.embedding_model_id,
                        "input": query,
                        "dimensions": self._config.embedding_dimension,
                    },
                    deadline_at=deadline_at,
                    allowed_model_labels=self._config.allowed_embedding_model_labels,
                )
                result = self._parse_embedding(response)
                if loop.time() >= deadline_at:
                    raise TimeoutError
                return result
        except TimeoutError as error:
            raise ModelUnavailable("LiteLLM exceeded the outer deadline") from error

    async def embed_many(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        """Embed one bounded chunk batch on Athena's fixed ingestion route."""

        if self.embedding_model_id != _ATHENA_EMBEDDING_ALIAS:
            raise ModelUnavailable("the ingestion embedding route is not configured")
        if (
            not isinstance(texts, tuple)
            or not 1 <= len(texts) <= _MAX_EMBEDDING_BATCH
            or any(not isinstance(text, str) or not text for text in texts)
        ):
            raise ModelUnavailable("embedding batch is outside the fixed bounds")
        if self._embedding_request_size(texts) > min(
            self._config.max_request_bytes, _MAX_EMBEDDING_REQUEST_BYTES
        ):
            raise ModelUnavailable("embedding batch exceeds the byte bound")

        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self._config.deadline_seconds
        try:
            async with asyncio.timeout_at(deadline_at):
                response = await self._post(
                    "v1/embeddings",
                    {
                        "model": _ATHENA_EMBEDDING_ALIAS,
                        "input": list(texts),
                        "dimensions": self._config.embedding_dimension,
                    },
                    deadline_at=deadline_at,
                    allowed_model_labels=self._config.allowed_embedding_model_labels,
                    maximum_request_bytes=_MAX_EMBEDDING_REQUEST_BYTES,
                )
                result = self._parse_embedding_batch(response, expected_count=len(texts))
                if loop.time() >= deadline_at:
                    raise TimeoutError
                return result
        except TimeoutError as error:
            raise ModelUnavailable("LiteLLM exceeded the outer deadline") from error

    async def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        model_alias: str,
        chunk_ids: tuple[str, ...],
    ) -> EmbeddingArtifact:
        """Implement the ingestion port with exact request-size and identity batching."""

        if model_alias != _ATHENA_EMBEDDING_ALIAS or model_alias != self.embedding_model_id:
            raise ModelUnavailable("the ingestion embedding route is not configured")
        if (
            not isinstance(texts, tuple)
            or not 1 <= len(texts) <= 10_000
            or any(not isinstance(text, str) or not text for text in texts)
            or not isinstance(chunk_ids, tuple)
            or len(chunk_ids) != len(texts)
            or len(set(chunk_ids)) != len(chunk_ids)
            or any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in chunk_ids)
        ):
            raise ModelUnavailable("embedding document input is outside the fixed bounds")

        request_bound = min(self._config.max_request_bytes, _MAX_EMBEDDING_REQUEST_BYTES)
        batches: list[tuple[str, ...]] = []
        pending: list[str] = []
        for text in texts:
            candidate = tuple((*pending, text))
            if (
                len(candidate) <= _MAX_EMBEDDING_BATCH
                and self._embedding_request_size(candidate) <= request_bound
            ):
                pending.append(text)
                continue
            if not pending or self._embedding_request_size((text,)) > request_bound:
                raise ModelUnavailable("one embedding input exceeds the byte bound")
            batches.append(tuple(pending))
            pending = [text]
        if pending:
            batches.append(tuple(pending))

        vectors: list[tuple[float, ...]] = []
        for batch in batches:
            vectors.extend(item.vector for item in await self.embed_many(batch))
        return EmbeddingArtifact(
            model_alias=model_alias,
            dimension=self.embedding_dimension,
            vectors=tuple(vectors),
            chunk_ids=chunk_ids,
        )

    def _embedding_request_size(self, texts: tuple[str, ...]) -> int:
        try:
            return len(
                json.dumps(
                    {
                        "model": _ATHENA_EMBEDDING_ALIAS,
                        "input": list(texts),
                        "dimensions": self._config.embedding_dimension,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, UnicodeEncodeError, ValueError) as error:
            raise ModelUnavailable("embedding batch is not valid UTF-8") from error

    async def answer(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        profile_id: str,
    ) -> AnswerGeneration:
        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self._config.deadline_seconds
        try:
            async with asyncio.timeout_at(deadline_at):
                _bounded_string("answer query", query, maximum=8_000)
                _bounded_string("retrieval profile", profile_id, maximum=128)
                if profile_id not in self._config.allowed_retrieval_profile_ids:
                    raise ModelUnavailable("retrieval profile is not allowed on the fixed route")
                evidence_payload = self._bounded_evidence(evidence)
                response = await self._post(
                    "v1/chat/completions",
                    {
                        "model": self._config.answer_model_id,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Answer only from supplied evidence. Return JSON with exactly "
                                    "answer and claims; every claim must contain current "
                                    "evidenceLabels, and every claim text must be copied exactly "
                                    "as one complete paragraph in answer."
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {"query": query, "evidence": evidence_payload},
                                    ensure_ascii=False,
                                    allow_nan=False,
                                    separators=(",", ":"),
                                ),
                            },
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": self._config.max_output_tokens,
                        "metadata": {
                            "tapAnswerProfile": self._config.answer_profile_id,
                        },
                    },
                    deadline_at=deadline_at,
                    allowed_model_labels=self._config.allowed_answer_model_labels,
                )
                result = self._parse_answer(response, evidence)
                if loop.time() >= deadline_at:
                    raise TimeoutError
                return result
        except TimeoutError as error:
            raise ModelUnavailable("LiteLLM exceeded the outer deadline") from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _bounded_evidence(self, evidence: tuple[Evidence, ...]) -> list[dict[str, str]]:
        if (
            not isinstance(evidence, tuple)
            or not 1 <= len(evidence) <= self._config.max_evidence_count
            or not all(isinstance(item, Evidence) for item in evidence)
        ):
            raise ModelUnavailable("evidence count exceeds the fixed-route bound")
        total = 0
        labels: set[str] = set()
        result: list[dict[str, str]] = []
        for item in evidence:
            label = _bounded_string("evidence label", item.evidence_label, maximum=64)
            if label in labels:
                raise ModelUnavailable("evidence labels must be unique")
            labels.add(label)
            content = _bounded_string(
                "evidence content",
                item.content,
                maximum=self._config.max_evidence_content_chars,
            )
            if not isinstance(item.source, SourceRevisionRef):
                raise ModelUnavailable("evidence source provenance is malformed")
            source_revision = _canonical_revision(
                item.source.revision_kind,
                item.source.revision,
            )
            source_hash = _canonical_sha256(
                "evidence source content hash",
                item.source.source_content_hash,
            )
            chunk_hash = _canonical_sha256(
                "evidence chunk content hash",
                item.chunk_content_hash,
            )
            total += len(content)
            if total > self._config.max_total_evidence_chars:
                raise ModelUnavailable("total evidence exceeds the content bound")
            result.append(
                {
                    "label": label,
                    "content": content,
                    "sourceRevision": source_revision,
                    "sourceContentHash": source_hash,
                    "chunkContentHash": chunk_hash,
                }
            )
        return result

    async def _post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        deadline_at: float,
        allowed_model_labels: frozenset[str],
        maximum_request_bytes: int | None = None,
    ) -> _GatewayResponse:
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ModelUnavailable("LiteLLM request is not safely serializable") from error
        request_bound = min(
            self._config.max_request_bytes,
            maximum_request_bytes
            if maximum_request_bytes is not None
            else self._config.max_request_bytes,
        )
        if len(encoded) > request_bound:
            raise ModelUnavailable("LiteLLM request exceeds the byte bound")
        loop = asyncio.get_running_loop()
        if loop.time() >= deadline_at:
            raise TimeoutError

        request_id = str(uuid4())
        for attempt in range(self._config.max_retries + 1):
            if loop.time() >= deadline_at:
                raise TimeoutError
            try:
                async with self._connection_slots:
                    if loop.time() >= deadline_at:
                        raise TimeoutError
                    async with self._client.stream(
                        "POST",
                        self._config.base_url.rstrip("/") + "/" + path,
                        headers={
                            "authorization": f"Bearer {self._config.api_key}",
                            "content-type": "application/json",
                            "x-tap-request-id": request_id,
                        },
                        content=encoded,
                    ) as response:
                        if response.status_code in {408, 429} or response.status_code >= 500:
                            if attempt == self._config.max_retries:
                                raise ModelUnavailable("LiteLLM status retry budget exhausted")
                            continue
                        if response.is_error:
                            raise ModelUnavailable(
                                f"LiteLLM rejected the fixed route with HTTP {response.status_code}"
                            )
                        raw = await _read_bounded(response, self._config.max_response_bytes)
                        provider_request_id = _first_header(
                            response,
                            ("x-request-id", "x-provider-request-id", "x-openai-request-id"),
                        )
                        gateway_call_id = _first_header(response, ("x-litellm-call-id",))
                        gateway_model_id = _bounded_model_header(response)
                        response_cost_header = response.headers.get("x-litellm-response-cost")
            except httpx.TransportError as error:
                if attempt == self._config.max_retries:
                    raise ModelUnavailable("LiteLLM transport retry budget exhausted") from error
                continue
            try:
                body = json.loads(
                    raw,
                    parse_constant=_reject_json_constant,
                    object_pairs_hook=_closed_pairs,
                )
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                raise ModelUnavailable("LiteLLM returned malformed JSON") from error
            if not isinstance(body, dict):
                raise ModelUnavailable("LiteLLM response must be a JSON object")
            if loop.time() >= deadline_at:
                raise TimeoutError
            if gateway_model_id is not None:
                self._validate_model_label(gateway_model_id, allowed_model_labels)
            return _GatewayResponse(
                body=body,
                provider_request_id=provider_request_id,
                gateway_call_id=gateway_call_id,
                gateway_model_id=gateway_model_id,
                response_cost_header=response_cost_header,
            )
        raise ModelUnavailable("LiteLLM retry budget exhausted")

    def _parse_embedding(self, response: _GatewayResponse) -> Embedding:
        try:
            body = response.body
            model = _required_body_string(body, "model", maximum=256)
            self._validate_model_label(model, self._config.allowed_embedding_model_labels)
            data = body.get("data")
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
                raise ValueError("embedding data must contain exactly one item")
            raw_vector = data[0].get("embedding")
            if not isinstance(raw_vector, list) or len(raw_vector) != self.embedding_dimension:
                raise ValueError("embedding dimension does not match the fixed route")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in raw_vector
            ):
                raise ValueError("embedding values must be finite numbers")
            usage = _embedding_usage(body, response.response_cost_header)
            return Embedding(
                vector=tuple(float(value) for value in raw_vector),
                model_id=self.embedding_model_id,
                provider_request_id=response.provider_request_id,
                gateway_call_id=response.gateway_call_id,
                gateway_model_id=response.gateway_model_id,
                provider_model_id=model,
                completion_id=_optional_body_string(body, "id", maximum=256),
                usage=usage,
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelUnavailable("LiteLLM returned a malformed embedding") from error

    def _parse_embedding_batch(
        self,
        response: _GatewayResponse,
        *,
        expected_count: int,
    ) -> tuple[Embedding, ...]:
        try:
            body = response.body
            if not {"model", "data"} <= set(body) <= {"id", "model", "data", "usage"}:
                raise ValueError("embedding response fields are widened")
            model = _required_body_string(body, "model", maximum=256)
            self._validate_model_label(model, self._config.allowed_embedding_model_labels)
            data = body["data"]
            if not isinstance(data, list) or len(data) != expected_count:
                raise ValueError("embedding batch count is not exact")
            ordered: list[Embedding | None] = [None] * expected_count
            usage = _embedding_usage(body, response.response_cost_header)
            for raw in data:
                if not isinstance(raw, dict) or set(raw) != {"embedding", "index"}:
                    raise ValueError("embedding row fields are widened")
                index = raw["index"]
                vector = raw["embedding"]
                if (
                    type(index) is not int
                    or not 0 <= index < expected_count
                    or ordered[index] is not None
                    or not isinstance(vector, list)
                    or len(vector) != self.embedding_dimension
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        for value in vector
                    )
                ):
                    raise ValueError("embedding row identity or vector is malformed")
                ordered[index] = Embedding(
                    vector=tuple(float(value) for value in vector),
                    model_id=self.embedding_model_id,
                    provider_request_id=response.provider_request_id,
                    gateway_call_id=response.gateway_call_id,
                    gateway_model_id=response.gateway_model_id,
                    provider_model_id=model,
                    completion_id=_optional_body_string(body, "id", maximum=256),
                    usage=usage,
                )
            if any(item is None for item in ordered):
                raise ValueError("embedding row identities are incomplete")
            return tuple(item for item in ordered if item is not None)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelUnavailable("LiteLLM returned a malformed embedding batch") from error

    def _parse_answer(
        self,
        response: _GatewayResponse,
        evidence: tuple[Evidence, ...],
    ) -> AnswerGeneration:
        try:
            body = response.body
            model = _required_body_string(body, "model", maximum=256)
            self._validate_model_label(model, self._config.allowed_answer_model_labels)
            choices = body.get("choices")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
                or not isinstance(choices[0].get("message"), dict)
            ):
                raise ValueError("answer response must contain exactly one choice")
            content = choices[0]["message"].get("content")
            if (
                not isinstance(content, str)
                or len(content.encode("utf-8")) > self._config.max_response_bytes
            ):
                raise ValueError("answer content is not a bounded JSON string")
            generated = json.loads(content, parse_constant=_reject_json_constant)
            if not isinstance(generated, dict) or set(generated) != {"answer", "claims"}:
                raise ValueError("grounded answer output must use the closed schema")
            answer = generated["answer"]
            if (
                not isinstance(answer, str)
                or not answer.strip()
                or len(answer) > self._config.max_answer_chars
            ):
                raise ValueError("non-abstained answer must be a bounded non-empty string")
            claims_value = generated["claims"]
            if (
                not isinstance(claims_value, list)
                or not 1 <= len(claims_value) <= self._config.max_claims
            ):
                raise ValueError("claim count exceeds the output bound")
            allowed_labels = {item.evidence_label for item in evidence}
            claims: list[GeneratedClaim] = []
            for item in claims_value:
                if not isinstance(item, dict) or set(item) != {"text", "evidenceLabels"}:
                    raise ValueError("claim must use the closed output schema")
                text = item["text"]
                labels = item["evidenceLabels"]
                if (
                    not isinstance(text, str)
                    or not text.strip()
                    or len(text) > self._config.max_claim_chars
                    or not isinstance(labels, list)
                    or not 1 <= len(labels) <= self._config.max_labels_per_claim
                    or any(
                        not isinstance(label, str)
                        or not label
                        or len(label) > 64
                        or label not in allowed_labels
                        for label in labels
                    )
                    or len(set(labels)) != len(labels)
                ):
                    raise ValueError("claim text or evidence labels exceed the output bound")
                claims.append(GeneratedClaim(text=text, evidence_labels=tuple(labels)))
            return AnswerGeneration(
                text=answer,
                claims=tuple(claims),
                model_id=self._config.answer_model_id,
                profile_id=self._config.answer_profile_id,
                provider_request_id=response.provider_request_id,
                gateway_call_id=response.gateway_call_id,
                gateway_model_id=response.gateway_model_id,
                provider_model_id=model,
                completion_id=_optional_body_string(body, "id", maximum=256),
            )
        except (
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as error:
            raise ModelUnavailable("LiteLLM returned a malformed grounded answer") from error

    @staticmethod
    def _validate_model_label(value: str, allowed: frozenset[str]) -> None:
        if value not in allowed:
            raise ModelUnavailable("LiteLLM returned model metadata outside the fixed route")


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > maximum:
            raise ModelUnavailable("LiteLLM response exceeds the byte bound")
        body.extend(chunk)
    return bytes(body)


def _first_header(response: httpx.Response, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = response.headers.get(name)
        if isinstance(value, str) and value and len(value) <= 256:
            return value
    return None


def _valid_litellm_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme == "https":
        return (
            parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
            and (port is None or 1 <= port <= 65_535)
        )
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.username is None
        and parsed.password is None
        and port is not None
        and 1 <= port <= 65_535
        and parsed.netloc == f"{parsed.hostname}:{port}"
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _embedding_usage(
    body: dict[str, Any],
    raw_cost: str | None,
) -> EmbeddingUsage:
    usage = body.get("usage")
    if not isinstance(usage, dict) or set(usage) != {"prompt_tokens", "total_tokens"}:
        raise ValueError("embedding usage must be an object")
    prompt_tokens = usage.get("prompt_tokens")
    total_tokens = usage.get("total_tokens")
    if (
        type(prompt_tokens) is not int
        or type(total_tokens) is not int
        or not 0 <= prompt_tokens <= 1_000_000
        or not prompt_tokens <= total_tokens <= 1_000_000
    ):
        raise ValueError("embedding usage tokens are malformed")
    cost = _response_cost(raw_cost)
    return EmbeddingUsage(
        input_tokens=prompt_tokens,
        total_tokens=total_tokens,
        response_cost_usd=cost,
    )


def _response_cost(value: str | None) -> Decimal | None:
    if value is None:
        return None
    if len(value) > 256 or _CANONICAL_COST.fullmatch(value) is None:
        raise ValueError("embedding response cost is malformed")
    try:
        cost = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("embedding response cost is malformed") from error
    if not cost.is_finite() or cost < 0 or cost > Decimal("100"):
        raise ValueError("embedding response cost is outside the bound")
    return cost


def _closed_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _bounded_model_header(response: httpx.Response) -> str | None:
    value = response.headers.get("x-litellm-model-id")
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ModelUnavailable("LiteLLM returned malformed fixed-route metadata")
    return value


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


def _strict_string_set(name: str, value: object, *, maximum_items: int) -> None:
    if (
        not isinstance(value, frozenset)
        or not value
        or len(value) > maximum_items
        or any(not isinstance(item, str) or not item or len(item) > 256 for item in value)
    ):
        raise ValueError(f"{name} must be a bounded frozenset of identifiers")


def _bounded_string(name: str, value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ModelUnavailable(f"{name} must be a bounded non-empty string")
    return value


def _canonical_revision(kind: object, value: object) -> str:
    if not isinstance(kind, RevisionKind):
        raise ModelUnavailable("evidence revision kind is outside the closed model")
    revision = _bounded_string("evidence source revision", value, maximum=512)
    if kind is RevisionKind.GIT_COMMIT and (
        len(revision) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in revision)
    ):
        raise ModelUnavailable("evidence source revision is not a canonical Git commit ID")
    return revision


def _canonical_sha256(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ModelUnavailable(f"{name} is not a canonical sha256 digest")
    return value


def _required_body_string(body: dict[str, Any], name: str, *, maximum: int) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"response {name} must be a bounded string")
    return value


def _optional_body_string(body: dict[str, Any], name: str, *, maximum: int) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    return _required_body_string(body, name, maximum=maximum)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")
