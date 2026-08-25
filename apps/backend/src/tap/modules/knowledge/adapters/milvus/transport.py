"""The only Knowledge adapter boundary that imports the PyMilvus SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal, Protocol, cast

from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker  # type: ignore[import-untyped]

from tap.modules.knowledge.adapters.milvus.config import MilvusIndexTarget, MilvusSearchConfig
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.errors import SearchError, SearchUnavailable

MILVUS_OUTPUT_FIELDS = (
    "chunk_id",
    "logical_chunk_id",
    "root_id",
    "parent_id",
    "title",
    "content",
    "content_role",
    "index_family",
    "physical_collection",
    "schema_version",
    "corpus_version",
    "embedding_model_version",
    "source_id",
    "source_type",
    "revision_kind",
    "source_revision",
    "source_content_hash",
    "chunk_content_hash",
    "anchor_json",
    "derived_from_chunk_ids",
)

_OUTPUT_FIELD_SET = frozenset(MILVUS_OUTPUT_FIELDS)
_INDEX_FIELDS = (
    "dense_vector",
    "bm25_sparse",
    "tenant_id",
    "project_id",
    "allowed_group_ids",
    "classification_rank",
    "environment",
    "corpus_version",
    "deleted",
)
_SAFE_COLLECTION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}\Z")
_METADATA_PREFIX = "tap-collection-metadata-v1:"
_MAX_NORMALIZATION_DEPTH = 16
_MAX_NORMALIZATION_ITEMS = 20_000
_MAX_NORMALIZATION_BYTES = 4 * 1024 * 1024
_METADATA_FIELDS = frozenset(
    {
        "family",
        "schemaVersion",
        "schemaSha256",
        "corpusVersion",
        "embeddingModelVersion",
        "vectorDimension",
    }
)
_COLLECTION_FIELDS = frozenset(
    {
        "collection_name",
        "auto_id",
        "num_shards",
        "description",
        "fields",
        "functions",
        "aliases",
        "collection_id",
        "consistency_level",
        "consistency_level_name",
        "properties",
        "num_partitions",
        "enable_dynamic_field",
        "enable_namespace",
        "created_timestamp",
        "update_timestamp",
    }
)
_FIELD_DESCRIPTION_FIELDS = frozenset(
    {
        "field_id",
        "name",
        "description",
        "type",
        "params",
        "default_value",
        "element_type",
        "is_partition_key",
        "is_dynamic",
        "auto_id",
        "nullable",
        "is_primary",
        "is_clustering_key",
        "is_function_output",
    }
)
_FUNCTION_DESCRIPTION_FIELDS = frozenset(
    {
        "name",
        "id",
        "description",
        "type",
        "params",
        "input_field_names",
        "input_field_ids",
        "output_field_names",
        "output_field_ids",
    }
)
_INDEX_DESCRIPTION_FIELDS = frozenset(
    {
        "field_name",
        "index_name",
        "index_type",
        "metric_type",
        "params",
        "total_rows",
        "indexed_rows",
        "pending_index_rows",
        "state",
        "fail_reason",
    }
)


@dataclass(frozen=True, slots=True)
class MilvusChannelRequest:
    kind: Literal["bm25", "dense"]
    query: str | tuple[float, ...] = field(repr=False)
    filter_expression: str = field(repr=False)
    limit: int

    def __post_init__(self) -> None:
        _limit(self.limit)
        _filter_expression(self.filter_expression)
        if self.kind == "bm25":
            if not isinstance(self.query, str) or not self.query or len(self.query) > 8_000:
                raise ValueError("BM25 query must be a bounded non-empty string")
            return
        if self.kind == "dense":
            if (
                not isinstance(self.query, tuple)
                or not 1 <= len(self.query) <= 4_096
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in self.query
                )
            ):
                raise ValueError("dense query must be a bounded finite vector")
            return
        raise ValueError("Milvus channel kind is outside the closed set")


@dataclass(frozen=True, slots=True)
class MilvusHybridRequest:
    collection_name: str
    channels: tuple[MilvusChannelRequest, MilvusChannelRequest]
    output_fields: tuple[str, ...]
    limit: int

    def __post_init__(self) -> None:
        _collection_name(self.collection_name)
        _limit(self.limit)
        _output_fields(self.output_fields)
        if (
            not isinstance(self.channels, tuple)
            or len(self.channels) != 2
            or not all(isinstance(channel, MilvusChannelRequest) for channel in self.channels)
            or tuple(channel.kind for channel in self.channels) != ("bm25", "dense")
            or self.channels[0].filter_expression != self.channels[1].filter_expression
            or any(channel.limit != self.limit for channel in self.channels)
        ):
            raise ValueError("hybrid channels must be one equally bounded BM25/dense pair")


@dataclass(frozen=True, slots=True)
class MilvusQueryRequest:
    collection_name: str
    filter_expression: str = field(repr=False)
    output_fields: tuple[str, ...]
    limit: int

    def __post_init__(self) -> None:
        _collection_name(self.collection_name)
        _filter_expression(self.filter_expression)
        _output_fields(self.output_fields)
        _limit(self.limit)


@dataclass(frozen=True, slots=True)
class MilvusCollectionDescriptor:
    collection_name: str
    family: SourceFamily
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int
    dynamic_fields_enabled: bool
    consistency_level: str


class MilvusReader(Protocol):
    async def describe_alias(self, alias: str) -> str: ...

    async def describe_collection(
        self,
        collection_name: str,
    ) -> MilvusCollectionDescriptor: ...

    async def hybrid_search(
        self,
        request: MilvusHybridRequest,
    ) -> tuple[Mapping[str, object], ...]: ...

    async def query(
        self,
        request: MilvusQueryRequest,
    ) -> tuple[Mapping[str, object], ...]: ...

    async def close(self) -> None: ...


class _SyncMilvusClient(Protocol):
    def describe_alias(self, alias: str, **kwargs: object) -> object: ...

    def describe_collection(self, collection_name: str, **kwargs: object) -> object: ...

    def describe_index(
        self,
        collection_name: str,
        index_name: str,
        **kwargs: object,
    ) -> object: ...

    def hybrid_search(self, **kwargs: object) -> object: ...

    def query(self, **kwargs: object) -> object: ...

    def close(self) -> None: ...


class _ResolvedCollectionName(str):
    """Opaque reader-owned capability for one resolved physical collection."""


class PyMilvusReader:
    """Async, deadline-bounded wrapper around the synchronous PyMilvus client."""

    def __init__(
        self,
        config: MilvusSearchConfig,
        *,
        client: object | None = None,
    ) -> None:
        if not isinstance(config, MilvusSearchConfig):
            raise TypeError("Milvus reader requires validated configuration")
        self._config = config
        self._client = cast(_SyncMilvusClient | None, client)
        self._client_lock = threading.Lock()
        self._binding_lock = threading.Lock()
        self._resolved_collections: dict[
            int,
            tuple[_ResolvedCollectionName, MilvusIndexTarget],
        ] = {}
        self._validated_collections: dict[int, _ResolvedCollectionName] = {}

    async def describe_alias(self, alias: str) -> str:
        target = self._target_for_alias(alias)
        raw = await self._sdk_call(
            lambda: self._sync_client().describe_alias(
                alias,
                timeout=self._config.timeout_seconds,
            )
        )
        try:
            value = _mapping(raw)
            if set(value) - {"alias", "collection_name", "db_name"}:
                raise ValueError("alias description is widened")
            if value.get("alias") != alias:
                raise ValueError("alias identity does not match")
            physical_collection = value.get("collection_name")
            _collection_name(physical_collection)
            if not cast(str, physical_collection).startswith(target.physical_name_prefix):
                raise ValueError("alias target is outside the configured prefix")
            resolved = _ResolvedCollectionName(cast(str, physical_collection))
            with self._binding_lock:
                self._resolved_collections[id(resolved)] = (resolved, target)
            return resolved
        except SearchError:
            raise
        except Exception:
            raise SearchUnavailable("search provider returned an invalid alias") from None

    async def describe_collection(
        self,
        collection_name: str,
    ) -> MilvusCollectionDescriptor:
        resolved, target = self._resolved_target(collection_name)
        raw_collection = await self._sdk_call(
            lambda: self._sync_client().describe_collection(
                resolved,
                timeout=self._config.timeout_seconds,
            )
        )
        raw_indexes = []
        for index_name in _INDEX_FIELDS:

            def describe_index(index_name: str = index_name) -> object:
                return self._sync_client().describe_index(
                    resolved,
                    index_name,
                    timeout=self._config.timeout_seconds,
                )

            raw_indexes.append(await self._sdk_call(describe_index))
        try:
            descriptor = _collection_descriptor(
                raw_collection,
                tuple(raw_indexes),
                expected_collection=resolved,
            )
        except SearchError:
            raise
        except Exception:
            self._discard_resolved(resolved)
            raise SearchUnavailable("search provider returned an invalid collection") from None
        if not _descriptor_matches_target(descriptor, target):
            self._discard_resolved(resolved)
            raise SearchUnavailable("Milvus collection does not match configured target")
        with self._binding_lock:
            self._validated_collections[id(resolved)] = resolved
        return descriptor

    async def hybrid_search(
        self,
        request: MilvusHybridRequest,
    ) -> tuple[Mapping[str, object], ...]:
        if not isinstance(request, MilvusHybridRequest):
            raise SearchUnavailable("search provider request is invalid")
        physical_collection = self._validated_collection(request.collection_name)

        def call() -> object:
            sdk_requests = [
                AnnSearchRequest(
                    data=[request.channels[0].query],
                    anns_field="bm25_sparse",
                    param={"metric_type": "BM25", "params": {}},
                    limit=request.channels[0].limit,
                    expr=request.channels[0].filter_expression,
                ),
                AnnSearchRequest(
                    data=[list(cast(tuple[float, ...], request.channels[1].query))],
                    anns_field="dense_vector",
                    param={"metric_type": "COSINE", "params": {}},
                    limit=request.channels[1].limit,
                    expr=request.channels[1].filter_expression,
                ),
            ]
            return self._sync_client().hybrid_search(
                collection_name=physical_collection,
                reqs=sdk_requests,
                ranker=RRFRanker(),
                limit=request.limit,
                output_fields=list(request.output_fields),
                timeout=self._config.timeout_seconds,
                consistency_level="Strong",
            )

        raw = await self._sdk_call(call)
        try:
            return _hybrid_rows(
                raw,
                allowed_fields=frozenset(request.output_fields),
                limit=request.limit,
            )
        except SearchError:
            raise
        except Exception:
            raise SearchUnavailable("search provider returned invalid hybrid rows") from None

    async def query(
        self,
        request: MilvusQueryRequest,
    ) -> tuple[Mapping[str, object], ...]:
        if not isinstance(request, MilvusQueryRequest):
            raise SearchUnavailable("search provider request is invalid")
        physical_collection = self._validated_collection(request.collection_name)
        raw = await self._sdk_call(
            lambda: self._sync_client().query(
                collection_name=physical_collection,
                filter=request.filter_expression,
                output_fields=list(request.output_fields),
                limit=request.limit,
                timeout=self._config.timeout_seconds,
                consistency_level="Strong",
            )
        )
        try:
            return _query_rows(
                raw,
                allowed_fields=frozenset(request.output_fields),
                limit=request.limit,
            )
        except SearchError:
            raise
        except Exception:
            raise SearchUnavailable("search provider returned invalid query rows") from None

    async def close(self) -> None:
        if self._client is None:
            return

        def call() -> None:
            with self._client_lock:
                client = self._client
                if client is None:
                    return
                client.close()
                self._client = None

        await self._sdk_call(call)
        with self._binding_lock:
            self._resolved_collections.clear()
            self._validated_collections.clear()

    async def _sdk_call[T](self, call: Callable[[], T]) -> T:
        return await _bounded_call(self._config.timeout_seconds, call)

    def _sync_client(self) -> _SyncMilvusClient:
        with self._client_lock:
            if self._client is None:
                self._client = cast(
                    _SyncMilvusClient,
                    MilvusClient(
                        uri=self._config.uri,
                        user=self._config.username,
                        password=self._config.password.get_secret_value(),
                        db_name=self._config.database,
                        timeout=self._config.timeout_seconds,
                    ),
                )
            return self._client

    def _target_for_alias(self, alias: str) -> MilvusIndexTarget:
        targets = tuple(target for target in self._config.targets.values() if target.alias == alias)
        if len(targets) != 1:
            raise SearchUnavailable("search provider alias is not configured")
        return targets[0]

    def _resolved_target(
        self,
        collection_name: str,
    ) -> tuple[_ResolvedCollectionName, MilvusIndexTarget]:
        with self._binding_lock:
            resolved = self._resolved_collections.get(id(collection_name))
        if resolved is None or resolved[0] is not collection_name:
            raise SearchUnavailable("Milvus physical collection is not resolved")
        return resolved

    def _validated_collection(self, collection_name: str) -> _ResolvedCollectionName:
        with self._binding_lock:
            validated = self._validated_collections.get(id(collection_name))
        if validated is None or validated is not collection_name:
            raise SearchUnavailable("Milvus physical collection is not bound")
        return validated

    def _discard_resolved(self, collection_name: _ResolvedCollectionName) -> None:
        with self._binding_lock:
            self._resolved_collections.pop(id(collection_name), None)
            self._validated_collections.pop(id(collection_name), None)


async def _bounded_call[T](timeout_seconds: float, call: Callable[[], T]) -> T:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await asyncio.to_thread(call)
    except TimeoutError:
        raise SearchUnavailable("search provider deadline exceeded") from None
    except SearchError:
        raise
    except Exception:
        raise SearchUnavailable("search provider call failed") from None


def _descriptor_matches_target(
    descriptor: MilvusCollectionDescriptor,
    target: MilvusIndexTarget,
) -> bool:
    return (
        descriptor.family is target.family
        and descriptor.schema_version == target.schema_version
        and descriptor.schema_sha256 == target.schema_sha256
        and descriptor.corpus_version == target.corpus_version
        and descriptor.embedding_model_version == target.embedding_model_version
        and descriptor.vector_dimension == target.vector_dimension
        and descriptor.dynamic_fields_enabled is False
        and descriptor.consistency_level == "Strong"
    )


def _collection_descriptor(
    raw_collection: object,
    raw_indexes: tuple[object, ...],
    *,
    expected_collection: str,
) -> MilvusCollectionDescriptor:
    collection = _mapping(raw_collection)
    if set(collection) - _COLLECTION_FIELDS:
        raise ValueError("collection description is widened")
    if collection.get("collection_name") != expected_collection:
        raise ValueError("collection identity does not match")
    if (
        collection.get("auto_id") is not False
        or collection.get("enable_dynamic_field") is not False
        or collection.get("enable_namespace") is not False
    ):
        raise ValueError("collection widening must be disabled")
    consistency_level = collection.get("consistency_level_name")
    if consistency_level != "Strong":
        raise ValueError("collection consistency must be Strong")

    metadata = _collection_metadata(collection.get("description"))
    fields = _canonical_fields(collection.get("fields"))
    functions = _canonical_functions(collection.get("functions"))
    indexes = _canonical_indexes(raw_indexes)
    canonical_schema = {
        "consistency_level": consistency_level,
        "fields": fields,
        "functions": functions,
        "indexes": indexes,
    }
    encoded = json.dumps(
        canonical_schema,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    computed_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    declared_digest = metadata["schemaSha256"]
    if declared_digest != computed_digest:
        raise SearchUnavailable("Milvus schema declaration does not match description")

    vector_dimension = metadata["vectorDimension"]
    dense_fields = [field for field in fields if field["name"] == "dense_vector"]
    dense_params = dense_fields[0]["params"] if len(dense_fields) == 1 else None
    if (
        type(vector_dimension) is not int
        or vector_dimension < 1
        or len(dense_fields) != 1
        or not isinstance(dense_params, Mapping)
        or dense_params.get("dim") != vector_dimension
    ):
        raise ValueError("vector dimension does not match dense field")

    return MilvusCollectionDescriptor(
        collection_name=expected_collection,
        family=SourceFamily(cast(str, metadata["family"])),
        schema_version=_metadata_string(metadata, "schemaVersion"),
        schema_sha256=cast(str, declared_digest),
        corpus_version=_metadata_string(metadata, "corpusVersion"),
        embedding_model_version=_metadata_string(metadata, "embeddingModelVersion"),
        vector_dimension=vector_dimension,
        dynamic_fields_enabled=False,
        consistency_level="Strong",
    )


def _collection_metadata(raw: object) -> dict[str, object]:
    if (
        not isinstance(raw, str)
        or not raw.startswith(_METADATA_PREFIX)
        or len(raw.encode("utf-8")) > 2_048
    ):
        raise ValueError("collection metadata description is malformed")
    value = json.loads(
        raw.removeprefix(_METADATA_PREFIX),
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict) or set(value) != _METADATA_FIELDS:
        raise ValueError("collection metadata fields are not closed")
    if value.get("family") != SourceFamily.DOC.value:
        raise ValueError("collection family is not doc")
    _metadata_string(value, "schemaVersion")
    _metadata_digest(value, "schemaSha256")
    _metadata_string(value, "corpusVersion")
    _metadata_string(value, "embeddingModelVersion")
    return cast(dict[str, object], value)


def _canonical_fields(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("collection fields are missing")
    canonical: list[dict[str, object]] = []
    names: set[str] = set()
    for item in raw:
        value = _mapping(item)
        if set(value) - _FIELD_DESCRIPTION_FIELDS:
            raise ValueError("field description is widened")
        if "default_value" in value or any(
            _optional_bool(value, flag, default=False)
            for flag in ("is_partition_key", "is_dynamic", "is_clustering_key")
        ):
            raise ValueError("field semantics are outside the closed schema")
        name = _description_name(value, "name")
        if name in names:
            raise ValueError("field names must be unique")
        names.add(name)
        canonical.append(
            {
                "auto_id": _optional_bool(value, "auto_id", default=False),
                "element_type": _optional_enum_number(value.get("element_type")),
                "is_function_output": _optional_bool(
                    value,
                    "is_function_output",
                    default=False,
                ),
                "is_primary": _optional_bool(value, "is_primary", default=False),
                "name": name,
                "nullable": _optional_bool(value, "nullable", default=False),
                "params": _canonical_params(value.get("params", {})),
                "type": _enum_number(value.get("type")),
            }
        )
    return canonical


def _canonical_functions(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        raise ValueError("collection functions are malformed")
    canonical: list[dict[str, object]] = []
    names: set[str] = set()
    for item in raw:
        value = _mapping(item)
        if set(value) - _FUNCTION_DESCRIPTION_FIELDS:
            raise ValueError("function description is widened")
        name = _description_name(value, "name")
        if name in names:
            raise ValueError("function names must be unique")
        names.add(name)
        canonical.append(
            {
                "input_field_names": _name_list(value.get("input_field_names")),
                "name": name,
                "output_field_names": _name_list(value.get("output_field_names")),
                "params": _canonical_params(value.get("params", {})),
                "type": _enum_number(value.get("type")),
            }
        )
    return sorted(canonical, key=lambda item: cast(str, item["name"]))


def _canonical_indexes(raw: tuple[object, ...]) -> list[dict[str, object]]:
    if len(raw) != len(_INDEX_FIELDS):
        raise ValueError("index descriptions are incomplete")
    canonical: list[dict[str, object]] = []
    for item in raw:
        value = _mapping(item)
        if set(value) - _INDEX_DESCRIPTION_FIELDS:
            raise ValueError("index description is widened")
        field_name = _description_name(value, "field_name")
        index_name = _description_name(value, "index_name")
        if field_name != index_name or field_name not in _INDEX_FIELDS:
            raise ValueError("index identity is outside the closed schema")
        canonical.append(
            {
                "field_name": field_name,
                "index_name": index_name,
                "index_type": _description_name(value, "index_type"),
                "metric_type": _optional_description_name(value.get("metric_type")),
                "params": _canonical_params(value.get("params", {})),
            }
        )
    if {index["field_name"] for index in canonical} != set(_INDEX_FIELDS):
        raise ValueError("index descriptions are incomplete")
    return sorted(canonical, key=lambda item: cast(str, item["field_name"]))


def _hybrid_rows(
    raw: object,
    *,
    allowed_fields: frozenset[str],
    limit: int,
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], list):
        raise ValueError("hybrid result must contain one query result")
    if len(raw[0]) > limit:
        raise ValueError("hybrid result exceeds the request limit")
    normalizer = _PlainNormalizer()
    rows = []
    for raw_hit in raw[0]:
        hit = _plain_mapping(raw_hit, normalizer=normalizer)
        if set(hit) - {"id", "distance", "entity"}:
            raise ValueError("hybrid hit is widened")
        entity = hit.get("entity")
        if not isinstance(entity, dict):
            raise ValueError("hybrid hit entity is malformed")
        if not set(entity) <= allowed_fields:
            raise ValueError("hybrid entity returned unrequested fields")
        if entity.get("chunk_id") != hit.get("id"):
            raise ValueError("hybrid primary identity does not match entity")
        score = hit.get("distance")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValueError("hybrid score is malformed")
        rows.append(
            {
                **entity,
                "score": float(score),
                "provider_request_id": None,
            }
        )
    return tuple(rows)


def _query_rows(
    raw: object,
    *,
    allowed_fields: frozenset[str],
    limit: int,
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, list):
        raise ValueError("query result must be a list")
    if len(raw) > limit:
        raise ValueError("query result exceeds the request limit")
    normalizer = _PlainNormalizer()
    rows = tuple(_plain_mapping(row, normalizer=normalizer) for row in raw)
    if any(not set(row) <= allowed_fields for row in rows):
        raise ValueError("query returned unrequested fields")
    return rows


class _PlainNormalizer:
    def __init__(self) -> None:
        self._items = 0
        self._bytes = 0
        self._active_containers: set[int] = set()

    def value(self, value: object, *, depth: int = 0) -> object:
        self._consume_item()
        if depth > _MAX_NORMALIZATION_DEPTH:
            raise ValueError("provider value exceeds the nesting bound")
        if value is None or isinstance(value, bool):
            self._consume_bytes(1)
            return value
        if isinstance(value, int):
            self._consume_bytes(8)
            return value
        if isinstance(value, str):
            self._consume_bytes(len(value.encode("utf-8")))
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("provider value is not finite")
            self._consume_bytes(8)
            return value
        if isinstance(value, list):
            return self._sequence(value, depth=depth, as_tuple=False)
        if isinstance(value, tuple):
            return self._sequence(value, depth=depth, as_tuple=True)
        if isinstance(value, Mapping):
            return self.mapping(value, depth=depth)
        raise ValueError("provider returned an SDK-specific value")

    def mapping(self, raw: object, *, depth: int = 0) -> dict[str, object]:
        value = _mapping(raw, validate_keys=False)
        identity = self._enter_container(value)
        try:
            normalized = {}
            for key, item in value.items():
                plain_key = self.key(key)
                normalized[plain_key] = self.value(item, depth=depth + 1)
            return normalized
        finally:
            self._active_containers.remove(identity)

    def key(self, value: object) -> str:
        key = _plain_key(value)
        self._consume_item()
        self._consume_bytes(len(key.encode("utf-8")))
        return key

    def _sequence(
        self,
        value: list[object] | tuple[object, ...],
        *,
        depth: int,
        as_tuple: bool,
    ) -> object:
        identity = self._enter_container(value)
        try:
            normalized = [self.value(item, depth=depth + 1) for item in value]
            return tuple(normalized) if as_tuple else normalized
        finally:
            self._active_containers.remove(identity)

    def _enter_container(self, value: object) -> int:
        identity = id(value)
        if identity in self._active_containers:
            raise ValueError("provider value contains a cycle")
        self._active_containers.add(identity)
        return identity

    def _consume_item(self) -> None:
        self._items += 1
        if self._items > _MAX_NORMALIZATION_ITEMS:
            raise ValueError("provider value exceeds the item bound")

    def _consume_bytes(self, size: int) -> None:
        self._bytes += size
        if self._bytes > _MAX_NORMALIZATION_BYTES:
            raise ValueError("provider value exceeds the byte bound")


def _plain_mapping(
    raw: object,
    *,
    normalizer: _PlainNormalizer | None = None,
) -> dict[str, object]:
    bounded = normalizer or _PlainNormalizer()
    bounded._consume_item()
    return bounded.mapping(raw)


def _plain_value(value: object) -> object:
    return _PlainNormalizer().value(value)


def _mapping(
    raw: object,
    *,
    validate_keys: bool = True,
) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise TypeError("provider value must be a string-keyed mapping")
    if validate_keys and any(not isinstance(key, str) for key in raw):
        raise TypeError("provider value must be a string-keyed mapping")
    return cast(Mapping[str, object], raw)


def _plain_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("provider mapping key is not a string")
    return value


def _canonical_params(raw: object) -> dict[str, object]:
    value = _mapping(raw)
    normalized = {}
    for key, item in value.items():
        if not key or len(key) > 128:
            raise ValueError("schema parameter name is malformed")
        if key == "analyzer_params" and isinstance(item, str):
            item = json.loads(item, parse_constant=_reject_json_constant)
        normalized[key] = _plain_value(item)
    return dict(sorted(normalized.items()))


def _metadata_string(value: Mapping[str, object], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item or len(item) > 256:
        raise ValueError(f"{name} must be a bounded metadata string")
    return item


def _metadata_digest(value: Mapping[str, object], name: str) -> str:
    item = _metadata_string(value, name)
    if (
        len(item) != 71
        or not item.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in item[7:])
    ):
        raise ValueError(f"{name} must be a canonical digest")
    return item


def _description_name(value: Mapping[str, object], name: str) -> str:
    return _required_name(value.get(name))


def _optional_description_name(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _required_name(value)


def _required_name(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ValueError("schema name is malformed")
    return value


def _name_list(value: object) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ValueError("schema field name list is malformed")
    return [_required_name(item) for item in value]


def _enum_number(value: object) -> int:
    if isinstance(value, IntEnum):
        return int(value)
    if type(value) is int and value >= 0:
        return value
    raise ValueError("schema enum value is malformed")


def _optional_enum_number(value: object) -> int | None:
    if value is None or value == 0:
        return None
    return _enum_number(value)


def _optional_bool(
    value: Mapping[str, object],
    name: str,
    *,
    default: bool,
) -> bool:
    item = value.get(name, default)
    if type(item) is not bool:
        raise ValueError(f"{name} must be a strict boolean")
    return item


def _collection_name(value: object) -> None:
    if not isinstance(value, str) or _SAFE_COLLECTION_NAME.fullmatch(value) is None:
        raise ValueError("collection name must be a safe Milvus identifier")


def _filter_expression(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 32_768
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError("filter expression must be bounded")


def _output_fields(value: object) -> None:
    if (
        not isinstance(value, tuple)
        or not value
        or len(set(value)) != len(value)
        or any(not isinstance(item, str) for item in value)
        or not set(value) <= _OUTPUT_FIELD_SET
    ):
        raise ValueError("output fields must be a closed safe subset")


def _limit(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 50:
        raise ValueError("Milvus request limit must be an integer from one through 50")


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")
