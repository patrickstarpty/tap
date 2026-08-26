"""Bounded, content-addressed embedding research for sanitized Milvus fixtures."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import tempfile
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol

from tap.modules.knowledge.ports.models import Embedding, EmbeddingUsage
from tap.operations.milvus.bailian import BailianEmbeddingConfig
from tap.operations.milvus.fixtures import content_hash, load_doc_fixture, load_query_cases

EMBEDDING_ALIAS: Literal["research-embedding-v1"] = "research-embedding-v1"
EMBEDDING_DIMENSION: Literal[1536] = 1536
EMBEDDING_PROVIDER_MODEL = "text-embedding-v4"
DEFAULT_MAX_CHUNKS = 100
DEFAULT_MAX_QUERIES = 20
HARD_MAX_CHUNKS = 500
HARD_MAX_QUERIES = 100

MAX_AGGREGATE_INPUT_TOKENS = 10_000_000
MAX_AGGREGATE_COST_CNY = Decimal("100")
MAX_PROVIDER_REQUEST_IDS = HARD_MAX_CHUNKS + HARD_MAX_QUERIES
RESEARCH_COST_CURRENCY = "CNY"
RESEARCH_UNIT_PRICE_PER_1000_INPUT_TOKENS = Decimal("0.0005")
RESEARCH_PRICING_SOURCE = "official_rate_2026-08-27"

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CACHE_KEY = re.compile(r"h_[0-9a-f]{64}\Z")
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_PROVIDER_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z")
_CACHE_KEYS = frozenset({"cacheKey", "dimension", "modelId", "vector"})
_OUTPUT_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_ATOMIC_TEMP_ATTEMPTS = 8


class EmbeddingResearchRejected(Exception):
    """The requested paid embedding run violates the bounded research profile."""


@dataclass(frozen=True, slots=True)
class EmbeddingInput:
    item_id: str
    text: str = field(repr=False)
    content_hash: str


@dataclass(frozen=True, slots=True)
class VectorRecord:
    input_hash: str
    vector: tuple[float, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class VectorSnapshot:
    model_id: Literal["research-embedding-v1"]
    dimension: Literal[1536]
    chunks: Mapping[str, VectorRecord] = field(repr=False)
    queries: Mapping[str, VectorRecord] = field(repr=False)


@dataclass(frozen=True, slots=True)
class EmbeddingResearchReport:
    model_id: str
    dimension: int
    chunk_count: int
    query_count: int
    cache_hits: int
    cache_misses: int
    input_tokens: int
    currency: str
    unit_price_per_1000_input_tokens: Decimal
    calculated_cost_cny: Decimal
    pricing_source: str
    provider_request_ids: tuple[str, ...]
    started_at: str
    finished_at: str


class EmbeddingCache(Protocol):
    def get(self, key: str) -> tuple[float, ...] | None: ...

    def put(self, key: str, vector: tuple[float, ...]) -> None: ...


class EmbeddingModelPort(Protocol):
    @property
    def embedding_model_id(self) -> str: ...

    @property
    def embedding_dimension(self) -> int: ...

    async def embed(self, query: str) -> Embedding: ...


@dataclass(frozen=True, slots=True)
class FileEmbeddingCache:
    root: Path
    model_id: str = EMBEDDING_ALIAS
    dimension: int = EMBEDDING_DIMENSION

    def __post_init__(self) -> None:
        if self.model_id != EMBEDDING_ALIAS or self.dimension != EMBEDDING_DIMENSION:
            raise EmbeddingResearchRejected("cache vector space does not match the research route")

    def get(self, key: str) -> tuple[float, ...] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_closed_pairs)
            if not isinstance(raw, dict) or set(raw) != _CACHE_KEYS:
                raise ValueError("widened cache schema")
            if (
                raw["cacheKey"] != key
                or raw["modelId"] != self.model_id
                or type(raw["dimension"]) is not int
                or raw["dimension"] != self.dimension
            ):
                raise ValueError("cache identity mismatch")
            raw_vector = raw["vector"]
            if not isinstance(raw_vector, list):
                raise ValueError("cache vector is not a list")
            vector = tuple(raw_vector)
            _validate_vector(vector)
            return vector
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise EmbeddingResearchRejected("embedding cache entry is malformed") from error

    def put(self, key: str, vector: tuple[float, ...]) -> None:
        self._path(key)
        _validate_vector(vector)
        payload = {
            "cacheKey": key,
            "dimension": self.dimension,
            "modelId": self.model_id,
            "vector": list(vector),
        }
        _atomic_json_write(self._path(key), payload)

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or _CACHE_KEY.fullmatch(key) is None:
            raise EmbeddingResearchRejected("embedding cache key is malformed")
        return self.root / f"{key}.json"


@dataclass(frozen=True, slots=True)
class DirectoryFdEmbeddingCache:
    directory_fd: int = field(repr=False)
    model_id: str = EMBEDDING_ALIAS
    dimension: int = EMBEDDING_DIMENSION

    def __post_init__(self) -> None:
        if (
            type(self.directory_fd) is not int
            or self.directory_fd < 0
            or self.model_id != EMBEDDING_ALIAS
            or self.dimension != EMBEDDING_DIMENSION
        ):
            raise EmbeddingResearchRejected(
                "cache directory capability does not match the research route"
            )
        try:
            status = os.fstat(self.directory_fd)
        except OSError as error:
            raise EmbeddingResearchRejected("cache directory capability is unavailable") from error
        if not stat.S_ISDIR(status.st_mode):
            raise EmbeddingResearchRejected("cache directory capability is not a directory")

    def get(self, key: str) -> tuple[float, ...] | None:
        name = self._name(key)
        try:
            raw = _read_json_at(self.directory_fd, name)
            if raw is None:
                return None
            if not isinstance(raw, dict) or set(raw) != _CACHE_KEYS:
                raise ValueError("widened cache schema")
            if (
                raw["cacheKey"] != key
                or raw["modelId"] != self.model_id
                or type(raw["dimension"]) is not int
                or raw["dimension"] != self.dimension
            ):
                raise ValueError("cache identity mismatch")
            raw_vector = raw["vector"]
            if not isinstance(raw_vector, list):
                raise ValueError("cache vector is not a list")
            vector = tuple(raw_vector)
            _validate_vector(vector)
            return vector
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise EmbeddingResearchRejected("embedding cache entry is malformed") from error

    def put(self, key: str, vector: tuple[float, ...]) -> None:
        name = self._name(key)
        _validate_vector(vector)
        _atomic_json_write_at(
            self.directory_fd,
            name,
            {
                "cacheKey": key,
                "dimension": self.dimension,
                "modelId": self.model_id,
                "vector": list(vector),
            },
        )

    @staticmethod
    def _name(key: str) -> str:
        if not isinstance(key, str) or _CACHE_KEY.fullmatch(key) is None:
            raise EmbeddingResearchRejected("embedding cache key is malformed")
        return f"{key}.json"


def embedding_cache_key(model_id: str, dimension: int, input_hash: str) -> str:
    if (
        not isinstance(model_id, str)
        or not model_id
        or len(model_id) > 256
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in model_id)
        or type(dimension) is not int
        or not 1 <= dimension <= 4096
        or not isinstance(input_hash, str)
        or _DIGEST.fullmatch(input_hash) is None
    ):
        raise EmbeddingResearchRejected("embedding cache identity is malformed")
    payload = f"{model_id}\x00{dimension}\x00{input_hash}".encode("utf-8")
    return "h_" + hashlib.sha256(payload).hexdigest()


async def generate_snapshot(
    model: EmbeddingModelPort,
    chunks: tuple[EmbeddingInput, ...],
    queries: tuple[EmbeddingInput, ...],
    cache: EmbeddingCache,
    *,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    max_queries: int = DEFAULT_MAX_QUERIES,
    clock: Callable[[], str] | None = None,
) -> tuple[VectorSnapshot, EmbeddingResearchReport]:
    """Validate the entire run before the first paid call, then generate exact vectors."""

    started_at = (clock or _utc_timestamp)()
    items = _preflight(model, chunks, queries, max_chunks=max_chunks, max_queries=max_queries)

    unique_inputs: dict[str, EmbeddingInput] = {}
    for _kind, item in items:
        unique_inputs.setdefault(
            item.content_hash,
            EmbeddingInput(
                item_id=item.item_id,
                text=_normalize_text(item.text),
                content_hash=item.content_hash,
            ),
        )

    vectors_by_hash: dict[str, tuple[float, ...]] = {}
    misses: list[tuple[str, EmbeddingInput]] = []
    for item in unique_inputs.values():
        key = embedding_cache_key(EMBEDDING_ALIAS, EMBEDDING_DIMENSION, item.content_hash)
        cached = cache.get(key)
        if cached is None:
            misses.append((key, item))
            continue
        _validate_vector(cached)
        vectors_by_hash[item.content_hash] = cached

    generated: list[tuple[str, EmbeddingInput, tuple[float, ...]]] = []
    input_tokens = 0
    calculated_cost_cny = Decimal("0")
    request_ids: list[str] = []
    seen_request_ids: set[str] = set()
    for key, item in misses:
        embedding = await model.embed(item.text)
        vector, usage, request_id = _validate_embedding(embedding)
        input_tokens += usage.input_tokens
        if input_tokens > MAX_AGGREGATE_INPUT_TOKENS:
            raise EmbeddingResearchRejected("aggregate embedding token usage exceeds the bound")
        assert usage.calculated_cost_cny is not None
        calculated_cost_cny += usage.calculated_cost_cny
        if not calculated_cost_cny.is_finite() or calculated_cost_cny > MAX_AGGREGATE_COST_CNY:
            raise EmbeddingResearchRejected("aggregate cost exceeds the bounded research profile")
        if request_id not in seen_request_ids:
            seen_request_ids.add(request_id)
            request_ids.append(request_id)
        if len(request_ids) > MAX_PROVIDER_REQUEST_IDS:
            raise EmbeddingResearchRejected("provider request identity count exceeds the bound")
        generated.append((key, item, vector))

    for key, item, vector in generated:
        cache.put(key, vector)
        vectors_by_hash[item.content_hash] = vector

    chunk_records = {
        item.item_id: VectorRecord(
            input_hash=item.content_hash,
            vector=vectors_by_hash[item.content_hash],
        )
        for item in sorted(chunks, key=lambda value: value.item_id)
    }
    query_records = {
        item.item_id: VectorRecord(
            input_hash=item.content_hash,
            vector=vectors_by_hash[item.content_hash],
        )
        for item in sorted(queries, key=lambda value: value.item_id)
    }
    finished_at = (clock or _utc_timestamp)()
    snapshot = VectorSnapshot(
        model_id=EMBEDDING_ALIAS,
        dimension=EMBEDDING_DIMENSION,
        chunks=MappingProxyType(chunk_records),
        queries=MappingProxyType(query_records),
    )
    report = EmbeddingResearchReport(
        model_id=EMBEDDING_ALIAS,
        dimension=EMBEDDING_DIMENSION,
        chunk_count=len(chunks),
        query_count=len(queries),
        cache_hits=len(unique_inputs) - len(misses),
        cache_misses=len(misses),
        input_tokens=input_tokens,
        currency=RESEARCH_COST_CURRENCY,
        unit_price_per_1000_input_tokens=RESEARCH_UNIT_PRICE_PER_1000_INPUT_TOKENS,
        calculated_cost_cny=calculated_cost_cny,
        pricing_source=RESEARCH_PRICING_SOURCE,
        provider_request_ids=tuple(request_ids),
        started_at=started_at,
        finished_at=finished_at,
    )
    return snapshot, report


def load_fixture_inputs(
    doc_fixture: Path,
    query_fixture: Path,
) -> tuple[tuple[EmbeddingInput, ...], tuple[EmbeddingInput, ...]]:
    manifest = load_doc_fixture(doc_fixture)
    cases = load_query_cases(query_fixture)
    chunks = tuple(
        EmbeddingInput(
            item_id=chunk.chunk_id,
            text=chunk.content,
            content_hash=chunk.chunk_content_hash,
        )
        for chunk in manifest.chunks
    )
    queries = tuple(
        EmbeddingInput(
            item_id=case.case_id,
            text=case.query,
            content_hash=content_hash(case.query),
        )
        for case in cases
    )
    return chunks, queries


def research_bailian_config(settings: Mapping[str, str]) -> BailianEmbeddingConfig:
    """Build the one fixed direct research route without exposing its secrets."""

    provider_model = _required_setting(settings, "LITELLM_EMBEDDING_MODEL", maximum=256)
    provider_key = _required_setting(settings, "LITELLM_EMBEDDING_API_KEY", maximum=256)
    provider_api_base = _required_setting(
        settings,
        "LITELLM_EMBEDDING_API_BASE",
        maximum=2048,
    )
    if provider_model != EMBEDDING_PROVIDER_MODEL:
        raise EmbeddingResearchRejected("embedding research provider route is invalid")
    try:
        return BailianEmbeddingConfig(api_base=provider_api_base, api_key=provider_key)
    except ValueError as error:
        raise EmbeddingResearchRejected("embedding research provider route is invalid") from error


def write_research_report(path: Path, report: EmbeddingResearchReport) -> None:
    _validate_report(report)
    _atomic_json_write(path, _report_payload(report))


def write_research_report_at(
    directory_fd: int,
    name: str,
    report: EmbeddingResearchReport,
) -> None:
    _validate_report(report)
    _atomic_json_write_at(directory_fd, name, _report_payload(report))


def write_vector_snapshot(path: Path, snapshot: VectorSnapshot) -> None:
    if snapshot.model_id != EMBEDDING_ALIAS or snapshot.dimension != EMBEDDING_DIMENSION:
        raise EmbeddingResearchRejected("vector snapshot space is malformed")
    _atomic_json_write(path, _snapshot_payload(snapshot))


def write_vector_snapshot_at(
    directory_fd: int,
    name: str,
    snapshot: VectorSnapshot,
) -> None:
    if snapshot.model_id != EMBEDDING_ALIAS or snapshot.dimension != EMBEDDING_DIMENSION:
        raise EmbeddingResearchRejected("vector snapshot space is malformed")
    _atomic_json_write_at(directory_fd, name, _snapshot_payload(snapshot))


def _preflight(
    model: EmbeddingModelPort,
    chunks: tuple[EmbeddingInput, ...],
    queries: tuple[EmbeddingInput, ...],
    *,
    max_chunks: int,
    max_queries: int,
) -> tuple[tuple[str, EmbeddingInput], ...]:
    if (
        model.embedding_model_id != EMBEDDING_ALIAS
        or model.embedding_dimension != EMBEDDING_DIMENSION
    ):
        raise EmbeddingResearchRejected("model route does not match the fixed vector space")
    if (
        type(max_chunks) is not int
        or type(max_queries) is not int
        or not 0 <= max_chunks <= HARD_MAX_CHUNKS
        or not 0 <= max_queries <= HARD_MAX_QUERIES
        or len(chunks) > max_chunks
        or len(queries) > max_queries
        or not chunks
        and not queries
    ):
        raise EmbeddingResearchRejected("input count exceeds the bounded research profile")
    all_items = tuple(("chunk", item) for item in chunks) + tuple(
        ("query", item) for item in queries
    )
    seen_ids: set[str] = set()
    normalized_by_hash: dict[str, str] = {}
    for _kind, item in all_items:
        normalized = (
            _normalize_text(item.text)
            if isinstance(item, EmbeddingInput) and isinstance(item.text, str)
            else ""
        )
        if (
            not isinstance(item, EmbeddingInput)
            or _ITEM_ID.fullmatch(item.item_id) is None
            or not isinstance(item.text, str)
            or not 1 <= len(item.text) <= 8_000
            or not 1 <= len(normalized) <= 8_000
            or any(character == "\x00" for character in item.text)
            or _DIGEST.fullmatch(item.content_hash) is None
            or content_hash(item.text) != item.content_hash
            or item.item_id in seen_ids
        ):
            raise EmbeddingResearchRejected("embedding input identity, text, or hash is malformed")
        if (
            item.content_hash in normalized_by_hash
            and normalized_by_hash[item.content_hash] != normalized
        ):
            raise EmbeddingResearchRejected("verified embedding hash collision")
        seen_ids.add(item.item_id)
        normalized_by_hash[item.content_hash] = normalized
    return all_items


def _validate_embedding(
    embedding: Embedding,
) -> tuple[tuple[float, ...], EmbeddingUsage, str]:
    if not isinstance(embedding, Embedding) or embedding.model_id != EMBEDDING_ALIAS:
        raise EmbeddingResearchRejected("provider embedding does not match the fixed model")
    _validate_vector(embedding.vector)
    usage = embedding.usage
    if type(usage) is not EmbeddingUsage:
        raise EmbeddingResearchRejected("embedding usage and cost are required")
    _validate_usage(usage)
    request_id = embedding.provider_request_id
    if not isinstance(request_id, str) or _PROVIDER_REQUEST_ID.fullmatch(request_id) is None:
        raise EmbeddingResearchRejected("provider request identity is malformed")
    return embedding.vector, usage, request_id


def _validate_usage(usage: EmbeddingUsage) -> None:
    cost = usage.calculated_cost_cny
    exponent = cost.as_tuple().exponent if type(cost) is Decimal and cost.is_finite() else None
    expected_cost = (
        Decimal(usage.input_tokens) * RESEARCH_UNIT_PRICE_PER_1000_INPUT_TOKENS / 1000
        if type(usage.input_tokens) is int
        else None
    )
    if (
        type(usage.input_tokens) is not int
        or type(usage.total_tokens) is not int
        or not 0 <= usage.input_tokens <= 1_000_000
        or not usage.input_tokens <= usage.total_tokens <= 1_000_000
        or usage.response_cost_usd is not None
        or type(cost) is not Decimal
        or not cost.is_finite()
        or not 0 <= cost <= Decimal("100")
        or type(exponent) is not int
        or not -18 <= exponent <= 0
        or len(cost.as_tuple().digits) > 21
        or cost != expected_cost
    ):
        raise EmbeddingResearchRejected("embedding usage and cost are malformed")


def _validate_vector(vector: tuple[float, ...]) -> None:
    if (
        not isinstance(vector, tuple)
        or len(vector) != EMBEDDING_DIMENSION
        or any(type(value) is not float or not math.isfinite(value) for value in vector)
    ):
        raise EmbeddingResearchRejected("embedding vector is outside the fixed vector space")


def _snapshot_records(records: Mapping[str, VectorRecord]) -> dict[str, object]:
    result: dict[str, object] = {}
    for item_id in sorted(records):
        record = records[item_id]
        if _ITEM_ID.fullmatch(item_id) is None or _DIGEST.fullmatch(record.input_hash) is None:
            raise EmbeddingResearchRejected("vector snapshot identity is malformed")
        _validate_vector(record.vector)
        result[item_id] = {"inputHash": record.input_hash, "vector": list(record.vector)}
    return result


def _validate_report(report: EmbeddingResearchReport) -> None:
    counts = (
        report.chunk_count,
        report.query_count,
        report.cache_hits,
        report.cache_misses,
        report.input_tokens,
    )
    request_ids = report.provider_request_ids
    cost = report.calculated_cost_cny
    cost_exponent = cost.as_tuple().exponent if type(cost) is Decimal and cost.is_finite() else None
    if (
        report.model_id != EMBEDDING_ALIAS
        or report.dimension != EMBEDDING_DIMENSION
        or any(type(value) is not int for value in counts)
        or not 0 <= report.chunk_count <= HARD_MAX_CHUNKS
        or not 0 <= report.query_count <= HARD_MAX_QUERIES
        or report.chunk_count + report.query_count < 1
        or not 0 <= report.cache_hits <= MAX_PROVIDER_REQUEST_IDS
        or not 0 <= report.cache_misses <= MAX_PROVIDER_REQUEST_IDS
        or not 1
        <= report.cache_hits + report.cache_misses
        <= (report.chunk_count + report.query_count)
        or not 0 <= report.input_tokens <= MAX_AGGREGATE_INPUT_TOKENS
        or report.currency != RESEARCH_COST_CURRENCY
        or report.unit_price_per_1000_input_tokens != RESEARCH_UNIT_PRICE_PER_1000_INPUT_TOKENS
        or report.pricing_source != RESEARCH_PRICING_SOURCE
        or type(cost) is not Decimal
        or not cost.is_finite()
        or not 0 <= cost <= MAX_AGGREGATE_COST_CNY
        or cost != Decimal(report.input_tokens) * RESEARCH_UNIT_PRICE_PER_1000_INPUT_TOKENS / 1000
        or type(cost_exponent) is not int
        or not -18 <= cost_exponent <= 0
        or len(cost.as_tuple().digits) > 21
        or not isinstance(request_ids, tuple)
        or len(request_ids) > min(report.cache_misses, MAX_PROVIDER_REQUEST_IDS)
        or (report.cache_misses > 0 and not request_ids)
        or len(set(request_ids)) != len(request_ids)
        or any(
            not isinstance(request_id, str) or _PROVIDER_REQUEST_ID.fullmatch(request_id) is None
            for request_id in request_ids
        )
        or not isinstance(report.started_at, str)
        or _TIMESTAMP.fullmatch(report.started_at) is None
        or not isinstance(report.finished_at, str)
        or _TIMESTAMP.fullmatch(report.finished_at) is None
        or report.finished_at < report.started_at
    ):
        raise EmbeddingResearchRejected("embedding research report is malformed")


def _report_payload(report: EmbeddingResearchReport) -> dict[str, object]:
    return {
        "cacheHits": report.cache_hits,
        "cacheMisses": report.cache_misses,
        "chunkCount": report.chunk_count,
        "dimension": report.dimension,
        "finishedAt": report.finished_at,
        "inputTokens": report.input_tokens,
        "modelId": report.model_id,
        "currency": report.currency,
        "unitPricePer1000InputTokens": format(
            report.unit_price_per_1000_input_tokens,
            "f",
        ),
        "calculatedCostCny": format(report.calculated_cost_cny, "f"),
        "pricingSource": report.pricing_source,
        "providerRequestIds": list(report.provider_request_ids),
        "queryCount": report.query_count,
        "startedAt": report.started_at,
    }


def _snapshot_payload(snapshot: VectorSnapshot) -> dict[str, object]:
    return {
        "chunks": _snapshot_records(snapshot.chunks),
        "dimension": snapshot.dimension,
        "modelId": snapshot.model_id,
        "queries": _snapshot_records(snapshot.queries),
    }


def _atomic_json_write(path: Path, payload: object) -> None:
    encoded = _encode_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(raw_path)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _atomic_json_write_at(directory_fd: int, name: str, payload: object) -> None:
    if (
        type(directory_fd) is not int
        or directory_fd < 0
        or not isinstance(name, str)
        or _OUTPUT_NAME.fullmatch(name) is None
    ):
        raise EmbeddingResearchRejected("research output capability is malformed")
    encoded = _encode_json(payload)
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        for _attempt in range(_ATOMIC_TEMP_ATTEMPTS):
            candidate = f".{name}.{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if descriptor is None or temporary_name is None:
            raise EmbeddingResearchRejected("research output temporary name is unavailable")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        os.fsync(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass


def _read_json_at(directory_fd: int, name: str) -> object | None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None
    try:
        stream = os.fdopen(descriptor, "r", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        value: object = json.load(stream, object_pairs_hook=_closed_pairs)
        return value


def _encode_json(payload: object) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise EmbeddingResearchRejected("research output is not safely serializable") from error


def _closed_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n")


def _required_setting(settings: Mapping[str, str], name: str, *, maximum: int) -> str:
    value = settings.get(name)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise EmbeddingResearchRejected("embedding research configuration is incomplete")
    return value
