"""The only Knowledge adapter boundary that imports the PyMilvus SDK."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import threading
import weakref
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import IntEnum, StrEnum
from typing import Any, Literal, Protocol, cast

from pymilvus import (  # type: ignore[import-untyped]
    AnnSearchRequest,
    AsyncMilvusClient,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    MilvusException,
    RRFRanker,
)

from tap.modules.knowledge.adapters.milvus.config import (
    MilvusIndexTarget,
    MilvusSearchConfig,
    is_owned_physical_collection,
)
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


def create_operations_milvus_sdk() -> object:
    """Expose pinned SDK bindings without widening the sole pymilvus import boundary."""

    from tap.operations.milvus.client import MilvusSdk

    return MilvusSdk(
        client_factory=MilvusClient,
        create_schema=MilvusClient.create_schema,
        function_factory=Function,
        ann_search_request_factory=AnnSearchRequest,
        ranker_factory=RRFRanker,
        varchar_type=DataType.VARCHAR,
        sparse_vector_type=DataType.SPARSE_FLOAT_VECTOR,
        float_vector_type=DataType.FLOAT_VECTOR,
        array_type=DataType.ARRAY,
        int64_type=DataType.INT64,
        bool_type=DataType.BOOL,
        bm25_function_type=FunctionType.BM25,
        permission_error=MilvusException,
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
_JSON_NUMBER_STRING = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_METADATA_PREFIX = "tap-collection-metadata-v1:"
_MAX_NORMALIZATION_DEPTH = 16
_MAX_NORMALIZATION_ITEMS = 20_000
_MAX_NORMALIZATION_BYTES = 4 * 1024 * 1024
_MAX_COLLECTION_ALIASES = 64
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
        "bm25_b",
        "bm25_k1",
        "field_name",
        "index_name",
        "index_type",
        "inverted_index_algo",
        "metric_type",
        "params",
        "total_rows",
        "indexed_rows",
        "pending_index_rows",
        "state",
    }
)
_FLATTENED_BM25_FIELDS = frozenset({"bm25_b", "bm25_k1", "inverted_index_algo"})


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


class _CollectionBaseSchemaComponent(StrEnum):
    ENVELOPE = "envelope"
    PROPERTIES = "properties"
    ALIASES = "aliases"
    IDENTITY = "identity"
    METADATA = "metadata"
    FIELDS = "fields"
    FUNCTIONS = "functions"
    VECTOR = "vector"


class CollectionBaseSchemaMismatch(ValueError):
    """Closed provider-schema discriminator without raw provider detail."""

    __slots__ = ("component",)

    def __init__(self, component: _CollectionBaseSchemaComponent) -> None:
        if not isinstance(component, _CollectionBaseSchemaComponent):
            raise TypeError("Milvus base schema mismatch requires a closed component")
        self.component = component
        super().__init__("Milvus collection base schema does not match")


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


class _AsyncMilvusClient(Protocol):
    async def describe_alias(self, alias: str, **kwargs: object) -> object: ...

    async def describe_collection(self, collection_name: str, **kwargs: object) -> object: ...

    async def describe_index(
        self,
        collection_name: str,
        index_name: str,
        **kwargs: object,
    ) -> object: ...

    async def hybrid_search(self, **kwargs: object) -> object: ...

    async def query(self, **kwargs: object) -> object: ...

    async def close(self) -> None: ...


class _ResolvedCollectionName(str):
    """Opaque reader-owned capability for one resolved physical collection."""


@dataclass(frozen=True, slots=True)
class _CollectionCapability:
    reference: weakref.ReferenceType[_ResolvedCollectionName]
    target: MilvusIndexTarget
    generation: int
    validated: bool = False


class PyMilvusReader:
    """Deadline-bounded owner of one dedicated native-async PyMilvus channel."""

    def __init__(
        self,
        config: MilvusSearchConfig,
        *,
        client: object | None = None,
    ) -> None:
        if not isinstance(config, MilvusSearchConfig):
            raise TypeError("Milvus reader requires validated configuration")
        self._config = config
        self._client = _validated_async_client(client)
        self._binding_lock = threading.Lock()
        self._operation_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._active_calls: set[asyncio.Task[object]] = set()
        self._capabilities: dict[int, _CollectionCapability] = {}
        self._closed = False
        self._close_complete = False
        self._generation = 0

    async def describe_alias(self, alias: str) -> str:
        target = self._target_for_alias(alias)
        raw = await self._sdk_call(
            lambda: self._async_client().describe_alias(
                alias,
                timeout=self._config.timeout_seconds,
            )
        )
        try:
            value = _mapping(raw)
            if set(value) != {"alias", "collection_name", "db_name"}:
                raise ValueError("alias description is widened")
            if value.get("alias") != alias:
                raise ValueError("alias identity does not match")
            if value.get("db_name") != self._config.database:
                raise ValueError("alias database does not match")
            physical_collection = value.get("collection_name")
            if not is_owned_physical_collection(
                target.physical_name_prefix,
                physical_collection,
                exact_generation_names=target.exact_generation_names,
            ):
                raise ValueError("alias target is outside the configured prefix")
            return self._register_resolved(cast(str, physical_collection), target)
        except SearchError:
            raise
        except Exception:
            raise SearchUnavailable("search provider returned an invalid alias") from None

    async def describe_collection(
        self,
        collection_name: str,
    ) -> MilvusCollectionDescriptor:
        resolved, target = self._resolved_target(collection_name)
        physical_collection = str(resolved)
        raw_collection = await self._sdk_call(
            lambda: self._async_client().describe_collection(
                physical_collection,
                timeout=self._config.timeout_seconds,
            )
        )
        raw_indexes = []
        for index_name in _INDEX_FIELDS:

            def describe_index(index_name: str = index_name) -> Coroutine[Any, Any, object]:
                return self._async_client().describe_index(
                    physical_collection,
                    index_name,
                    timeout=self._config.timeout_seconds,
                )

            raw_indexes.append(await self._sdk_call(describe_index))
        try:
            descriptor = _collection_descriptor(
                raw_collection,
                tuple(raw_indexes),
                expected_collection=physical_collection,
            )
        except SearchError:
            raise
        except Exception:
            self._discard_resolved(resolved)
            raise SearchUnavailable("search provider returned an invalid collection") from None
        if not _descriptor_matches_target(descriptor, target):
            self._discard_resolved(resolved)
            raise SearchUnavailable("Milvus collection does not match configured target")
        self._mark_validated(resolved)
        return descriptor

    async def hybrid_search(
        self,
        request: MilvusHybridRequest,
    ) -> tuple[Mapping[str, object], ...]:
        if not isinstance(request, MilvusHybridRequest):
            raise SearchUnavailable("search provider request is invalid")
        physical_collection = self._validated_collection(request.collection_name)
        collection_name = str(physical_collection)

        def call() -> Coroutine[Any, Any, object]:
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
            return self._async_client().hybrid_search(
                collection_name=collection_name,
                reqs=sdk_requests,
                ranker=RRFRanker(),
                limit=request.limit,
                output_fields=list(request.output_fields),
                timeout=self._config.timeout_seconds,
                consistency_level="Strong",
            )

        raw = await self._sdk_call(call)
        self._ensure_open()
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
        collection_name = str(physical_collection)
        raw = await self._sdk_call(
            lambda: self._async_client().query(
                collection_name=collection_name,
                filter=request.filter_expression,
                output_fields=list(request.output_fields),
                limit=request.limit,
                timeout=self._config.timeout_seconds,
                consistency_level="Strong",
            )
        )
        self._ensure_open()
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
        async with self._close_lock:
            with self._binding_lock:
                if self._close_complete:
                    return
                if not self._closed:
                    self._closed = True
                    self._generation += 1
                    self._capabilities.clear()
            client = self._client
            calls = tuple(self._active_calls)
            close_task = asyncio.create_task(client.close()) if client is not None else None
            for call in calls:
                call.cancel()
            tasks = (*calls, *((close_task,) if close_task is not None else ()))
            try:
                results = await _settle_owned_tasks(tasks)
            except asyncio.CancelledError as cancellation:
                if client is not None:
                    retry_close = asyncio.create_task(client.close())
                    await _wait_task_terminal(retry_close)
                raise cancellation
            retry_result: object | BaseException | None = None
            if client is not None and calls:
                retry_result = (await _settle_owned_tasks((asyncio.create_task(client.close()),)))[
                    0
                ]
            async with self._operation_lock:
                pass
            if close_task is not None:
                close_result = results[-1]
                if isinstance(close_result, asyncio.CancelledError):
                    raise SearchUnavailable("search provider close was cancelled") from None
                if isinstance(close_result, BaseException):
                    raise SearchUnavailable("search provider close failed") from None
            if isinstance(retry_result, asyncio.CancelledError):
                raise SearchUnavailable("search provider close was cancelled") from None
            if isinstance(retry_result, BaseException):
                raise SearchUnavailable("search provider close failed") from None
            self._client = None
            self._close_complete = True

    async def _sdk_call[T](self, call: Callable[[], Coroutine[Any, Any, T]]) -> T:
        async with self._operation_lock:
            self._ensure_open()
            try:
                operation = call()
            except SearchError:
                raise
            except Exception:
                raise SearchUnavailable("search provider call failed") from None
            worker: asyncio.Task[T] = asyncio.create_task(operation)
            tracked = cast(asyncio.Task[object], worker)
            self._active_calls.add(tracked)
            worker.add_done_callback(self._active_calls.discard)
            try:
                return await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=self._config.timeout_seconds,
                )
            except asyncio.CancelledError as cancellation:
                caller = asyncio.current_task()
                if (
                    worker.done()
                    and worker.cancelled()
                    and caller is not None
                    and not caller.cancelling()
                ):
                    self._ensure_open()
                    raise SearchUnavailable("search provider call was cancelled") from None
                worker.cancel()
                await _wait_task_terminal(worker)
                raise cancellation
            except TimeoutError:
                worker.cancel()
                await _wait_task_terminal(worker)
                raise SearchUnavailable("search provider deadline exceeded") from None
            except SearchError:
                raise
            except Exception:
                raise SearchUnavailable("search provider call failed") from None

    def _async_client(self) -> _AsyncMilvusClient:
        self._ensure_open()
        if self._client is None:
            self._client = cast(
                _AsyncMilvusClient,
                AsyncMilvusClient(
                    uri=self._config.uri,
                    user=self._config.username,
                    password=self._config.password.get_secret_value(),
                    db_name=self._config.database,
                    timeout=self._config.timeout_seconds,
                    dedicated=True,
                ),
            )
        return self._client

    def _target_for_alias(self, alias: str) -> MilvusIndexTarget:
        targets = tuple(target for target in self._config.targets.values() if target.alias == alias)
        if len(targets) != 1:
            raise SearchUnavailable("search provider alias is not configured")
        return targets[0]

    def _register_resolved(
        self,
        physical_collection: str,
        target: MilvusIndexTarget,
    ) -> _ResolvedCollectionName:
        resolved = _ResolvedCollectionName(physical_collection)
        identity = id(resolved)

        def discard(reference: weakref.ReferenceType[_ResolvedCollectionName]) -> None:
            with self._binding_lock:
                capability = self._capabilities.get(identity)
                if capability is not None and capability.reference is reference:
                    self._capabilities.pop(identity, None)

        reference = weakref.ref(resolved, discard)
        with self._binding_lock:
            self._ensure_open_locked()
            self._capabilities[identity] = _CollectionCapability(
                reference=reference,
                target=target,
                generation=self._generation,
            )
        return resolved

    def _resolved_target(
        self,
        collection_name: str,
    ) -> tuple[_ResolvedCollectionName, MilvusIndexTarget]:
        return self._capability_for(collection_name, require_validated=False)

    def _capability_for(
        self,
        collection_name: str,
        *,
        require_validated: bool,
    ) -> tuple[_ResolvedCollectionName, MilvusIndexTarget]:
        unavailable_message = (
            "Milvus physical collection is not bound"
            if require_validated
            else "Milvus physical collection is not resolved"
        )
        with self._binding_lock:
            self._ensure_open_locked()
            capability = self._capabilities.get(id(collection_name))
            if capability is None or capability.generation != self._generation:
                raise SearchUnavailable(unavailable_message)
            resolved = capability.reference()
            if resolved is None:
                self._capabilities.pop(id(collection_name), None)
                raise SearchUnavailable(unavailable_message)
            if resolved is not collection_name:
                raise SearchUnavailable(unavailable_message)
            if require_validated and not capability.validated:
                raise SearchUnavailable("Milvus physical collection is not bound")
            return resolved, capability.target

    def _validated_collection(self, collection_name: str) -> _ResolvedCollectionName:
        resolved, _ = self._capability_for(collection_name, require_validated=True)
        return resolved

    def _mark_validated(self, collection_name: _ResolvedCollectionName) -> None:
        with self._binding_lock:
            self._ensure_open_locked()
            capability = self._capabilities.get(id(collection_name))
            if (
                capability is None
                or capability.generation != self._generation
                or capability.reference() is not collection_name
            ):
                raise SearchUnavailable("Milvus physical collection is not resolved")
            self._capabilities[id(collection_name)] = _CollectionCapability(
                reference=capability.reference,
                target=capability.target,
                generation=capability.generation,
                validated=True,
            )

    def _discard_resolved(self, collection_name: _ResolvedCollectionName) -> None:
        with self._binding_lock:
            capability = self._capabilities.get(id(collection_name))
            if capability is not None and capability.reference() is collection_name:
                self._capabilities.pop(id(collection_name), None)

    def _ensure_open(self) -> None:
        with self._binding_lock:
            self._ensure_open_locked()

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise SearchUnavailable("search provider reader is closed")


async def _wait_task_terminal[T](task: asyncio.Task[T]) -> None:
    """Retain ownership until one cancelled native-async provider call is terminal."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    try:
        task.result()
    except BaseException:
        pass


async def _settle_owned_tasks(
    tasks: tuple[asyncio.Task[object] | asyncio.Task[None], ...],
) -> tuple[object | BaseException, ...]:
    if not tasks:
        return ()
    settlement = asyncio.gather(*tasks, return_exceptions=True)
    try:
        return tuple(await asyncio.shield(settlement))
    except asyncio.CancelledError as cancellation:
        for task in tasks:
            task.cancel()
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        raise cancellation


def _validated_async_client(client: object | None) -> _AsyncMilvusClient | None:
    if client is None:
        return None
    methods = (
        "describe_alias",
        "describe_collection",
        "describe_index",
        "hybrid_search",
        "query",
        "close",
    )
    if any(not inspect.iscoroutinefunction(getattr(client, method, None)) for method in methods):
        raise TypeError("Milvus reader requires a native async client")
    return cast(_AsyncMilvusClient, client)


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
    _validate_collection_envelope(collection)
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


def collection_base_descriptor(
    raw_collection: object,
    *,
    expected_collection: str,
    expected_schema: Mapping[str, object],
) -> MilvusCollectionDescriptor:
    """Validate immutable collection facts before repairing any missing indexes."""

    try:
        collection = _mapping(raw_collection)
        if set(collection) - _COLLECTION_FIELDS:
            raise ValueError("collection description is widened")
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.ENVELOPE) from None
    try:
        _validate_collection_properties(collection)
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.PROPERTIES) from None
    try:
        _validate_collection_aliases(collection)
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.ALIASES) from None
    try:
        if collection.get("collection_name") != expected_collection:
            raise ValueError("collection identity does not match")
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.IDENTITY) from None
    try:
        if (
            collection.get("auto_id") is not False
            or collection.get("enable_dynamic_field") is not False
            or collection.get("enable_namespace") is not False
            or collection.get("consistency_level_name") != "Strong"
            or collection.get("description") != expected_schema.get("description")
        ):
            raise ValueError("collection base metadata does not match")
        metadata = _collection_metadata(collection.get("description"))
        family = SourceFamily(cast(str, metadata["family"]))
        schema_version = _metadata_string(metadata, "schemaVersion")
        schema_sha256 = _metadata_digest(metadata, "schemaSha256")
        corpus_version = _metadata_string(metadata, "corpusVersion")
        embedding_model_version = _metadata_string(metadata, "embeddingModelVersion")
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.METADATA) from None
    try:
        fields = _canonical_fields(collection.get("fields"))
        expected_fields = [
            dict(cast(Mapping[str, object], item))
            for item in cast(Sequence[object], expected_schema.get("fields"))
        ]
        if fields != expected_fields:
            raise ValueError("collection fields do not match the canonical schema")
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.FIELDS) from None
    try:
        functions = _canonical_functions(collection.get("functions"))
        if functions != [
            {
                "input_field_names": ["content"],
                "name": "content_bm25_v1",
                "output_field_names": ["bm25_sparse"],
                "params": {},
                "type": 1,
            }
        ]:
            raise ValueError("collection functions do not match the canonical schema")
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.FUNCTIONS) from None
    try:
        vector_dimension = metadata["vectorDimension"]
        dense_fields = [field for field in fields if field["name"] == "dense_vector"]
        if (
            type(vector_dimension) is not int
            or len(dense_fields) != 1
            or dense_fields[0]["params"] != {"dim": vector_dimension}
        ):
            raise ValueError("vector dimension does not match dense field")
    except Exception:
        raise CollectionBaseSchemaMismatch(_CollectionBaseSchemaComponent.VECTOR) from None
    return MilvusCollectionDescriptor(
        collection_name=expected_collection,
        family=family,
        schema_version=schema_version,
        schema_sha256=schema_sha256,
        corpus_version=corpus_version,
        embedding_model_version=embedding_model_version,
        vector_dimension=vector_dimension,
        dynamic_fields_enabled=False,
        consistency_level="Strong",
    )


def _validate_collection_envelope(collection: Mapping[str, object]) -> None:
    _validate_collection_properties(collection)
    _validate_collection_aliases(collection)


def _validate_collection_properties(collection: Mapping[str, object]) -> None:
    properties = collection.get("properties")
    if type(properties) is not dict or len(properties) != 1:
        raise ValueError("collection properties are widened")
    key, value = next(iter(properties.items()))
    if type(key) is not str or key != "timezone" or type(value) is not str or value != "UTC":
        raise ValueError("collection properties are widened")


def _validate_collection_aliases(collection: Mapping[str, object]) -> None:
    aliases = collection.get("aliases")
    if (
        isinstance(aliases, (str, bytes))
        or not isinstance(aliases, Sequence)
        or len(aliases) > _MAX_COLLECTION_ALIASES
        or any(
            not isinstance(alias, str) or _SAFE_COLLECTION_NAME.fullmatch(alias) is None
            for alias in aliases
        )
        or len(set(cast(Sequence[str], aliases))) != len(aliases)
    ):
        raise ValueError("collection alias inventory is malformed")


def validate_collection_indexes(
    raw_indexes: tuple[object, ...],
    *,
    expected_schema: Mapping[str, object],
) -> None:
    """Validate exact index details without requiring collection-observer authority."""

    configured = expected_schema.get("indexes")
    if not isinstance(configured, Mapping) or set(configured) != set(_INDEX_FIELDS):
        raise ValueError("expected index schema is outside the closed doc family")
    expected: list[dict[str, object]] = []
    for field_name, raw_definition in configured.items():
        if not isinstance(field_name, str) or not isinstance(raw_definition, Mapping):
            raise ValueError("expected index schema is malformed")
        definition = dict(raw_definition)
        if not set(definition) <= {"index_type", "metric_type", "params"}:
            raise ValueError("expected index schema is widened")
        index_type = definition.get("index_type")
        metric_type = definition.get("metric_type")
        params = definition.get("params", {})
        if (
            not isinstance(index_type, str)
            or (metric_type is not None and not isinstance(metric_type, str))
            or not isinstance(params, Mapping)
        ):
            raise ValueError("expected index schema is malformed")
        expected.append(
            {
                "field_name": field_name,
                "index_name": field_name,
                "index_type": index_type,
                "metric_type": metric_type,
                "params": dict(params),
            }
        )
    expected.sort(key=lambda item: cast(str, item["field_name"]))
    if _canonical_indexes(raw_indexes) != expected:
        raise ValueError("collection indexes do not match the canonical schema")


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
                "params": _canonical_field_params(
                    value.get("params", {}),
                    field_name=name,
                ),
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
        index_type = _description_name(value, "index_type")
        metric_type = _optional_description_name(value.get("metric_type"))
        canonical.append(
            {
                "field_name": field_name,
                "index_name": index_name,
                "index_type": index_type,
                "metric_type": metric_type,
                "params": _canonical_index_params(
                    value,
                    field_name=field_name,
                    index_type=index_type,
                    metric_type=metric_type,
                ),
            }
        )
    if {index["field_name"] for index in canonical} != set(_INDEX_FIELDS):
        raise ValueError("index descriptions are incomplete")
    return sorted(canonical, key=lambda item: cast(str, item["field_name"]))


def _canonical_index_params(
    value: Mapping[str, object],
    *,
    field_name: str,
    index_type: str,
    metric_type: str | None,
) -> dict[str, object]:
    flattened = set(value) & _FLATTENED_BM25_FIELDS
    if not flattened:
        return _canonical_params(value.get("params", {}))
    if (
        "params" in value
        or flattened != _FLATTENED_BM25_FIELDS
        or field_name != "bm25_sparse"
        or index_type != "SPARSE_INVERTED_INDEX"
        or metric_type != "BM25"
        or value.get("inverted_index_algo") != "DAAT_MAXSCORE"
    ):
        raise ValueError("flattened index settings are outside the pinned BM25 shape")
    return {
        "bm25_b": _canonical_bm25_number(value["bm25_b"], expected="0.75"),
        "bm25_k1": _canonical_bm25_number(value["bm25_k1"], expected="1.2"),
        "inverted_index_algo": "DAAT_MAXSCORE",
    }


def _canonical_bm25_number(raw: object, *, expected: str) -> float:
    if isinstance(raw, str):
        if len(raw) > 64 or _JSON_NUMBER_STRING.fullmatch(raw) is None:
            raise ValueError("BM25 numeric setting is not canonical")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise ValueError("BM25 numeric setting is invalid") from None
    elif type(raw) is int:
        value = Decimal(raw)
    elif type(raw) is float:
        provider_float = raw
        if not math.isfinite(provider_float):
            raise ValueError("BM25 numeric setting is not finite")
        value = Decimal(str(provider_float))
    else:
        raise ValueError("BM25 numeric setting has an invalid type")
    if not value.is_finite() or value != Decimal(expected):
        raise ValueError("BM25 numeric setting does not match the canonical schema")
    return float(expected)


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
        if set(hit) != {"chunk_id", "distance", "entity"}:
            raise ValueError("hybrid hit shape is malformed")
        primary = hit["chunk_id"]
        if not isinstance(primary, str) or not primary:
            raise ValueError("hybrid primary identity is malformed")
        entity = hit.get("entity")
        if not isinstance(entity, dict):
            raise ValueError("hybrid hit entity is malformed")
        if not set(entity) <= allowed_fields:
            raise ValueError("hybrid entity returned unrequested fields")
        if "chunk_id" in entity and entity["chunk_id"] != primary:
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
                "chunk_id": primary,
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
            self._consume_bytes(int.__sizeof__(value))
            return value
        if isinstance(value, str):
            self._consume_utf8_bytes(value)
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
        self._consume_utf8_bytes(key)
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

    def _consume_utf8_bytes(self, value: str) -> None:
        """Account UTF-8 bytes incrementally without allocating an encoded copy."""
        for character in value:
            code_point = ord(character)
            if code_point < 0x80:
                self._consume_bytes(1)
            elif code_point < 0x800:
                self._consume_bytes(2)
            elif 0xD800 <= code_point <= 0xDFFF:
                raise ValueError("provider string is not valid UTF-8")
            elif code_point < 0x10000:
                self._consume_bytes(3)
            else:
                self._consume_bytes(4)


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


def _canonical_field_params(
    raw: object,
    *,
    field_name: str,
) -> dict[str, object]:
    value = _mapping(raw)
    analyzer_enabled = value.get("enable_analyzer")
    if field_name == "content" and type(analyzer_enabled) is str and analyzer_enabled == "true":
        value = {**value, "enable_analyzer": True}
    return _canonical_params(value)


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
