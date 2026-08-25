from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence

import pytest
from pydantic import SecretStr

from tap.modules.knowledge.adapters.milvus.config import MilvusIndexTarget, MilvusSearchConfig
from tap.modules.knowledge.adapters.milvus.targets import bind_target
from tap.modules.knowledge.adapters.milvus.transport import (
    MILVUS_OUTPUT_FIELDS,
    MilvusChannelRequest,
    MilvusHybridRequest,
    MilvusQueryRequest,
    PyMilvusReader,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.errors import SearchUnavailable

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


def _canonical_schema() -> dict[str, object]:
    return {
        "consistency_level": "Strong",
        "fields": [
            {
                "auto_id": False,
                "element_type": None,
                "is_function_output": False,
                "is_primary": True,
                "name": "chunk_id",
                "nullable": False,
                "params": {"max_length": 66},
                "type": 21,
            },
            {
                "auto_id": False,
                "element_type": None,
                "is_function_output": False,
                "is_primary": False,
                "name": "content",
                "nullable": False,
                "params": {"enable_analyzer": True, "max_length": 32768},
                "type": 21,
            },
            {
                "auto_id": False,
                "element_type": None,
                "is_function_output": True,
                "is_primary": False,
                "name": "bm25_sparse",
                "nullable": False,
                "params": {},
                "type": 104,
            },
            {
                "auto_id": False,
                "element_type": None,
                "is_function_output": False,
                "is_primary": False,
                "name": "dense_vector",
                "nullable": False,
                "params": {"dim": 1536},
                "type": 101,
            },
        ],
        "functions": [
            {
                "input_field_names": ["content"],
                "name": "content_bm25_v1",
                "output_field_names": ["bm25_sparse"],
                "params": {},
                "type": 1,
            }
        ],
        "indexes": [
            {
                "field_name": "allowed_group_ids",
                "index_name": "allowed_group_ids",
                "index_type": "INVERTED",
                "metric_type": None,
                "params": {},
            },
            {
                "field_name": "bm25_sparse",
                "index_name": "bm25_sparse",
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": "BM25",
                "params": {"bm25_b": 0.75, "bm25_k1": 1.2},
            },
            {
                "field_name": "classification_rank",
                "index_name": "classification_rank",
                "index_type": "INVERTED",
                "metric_type": None,
                "params": {},
            },
            {
                "field_name": "corpus_version",
                "index_name": "corpus_version",
                "index_type": "INVERTED",
                "metric_type": None,
                "params": {},
            },
            {
                "field_name": "deleted",
                "index_name": "deleted",
                "index_type": "INVERTED",
                "metric_type": None,
                "params": {},
            },
            {
                "field_name": "dense_vector",
                "index_name": "dense_vector",
                "index_type": "FLAT",
                "metric_type": "COSINE",
                "params": {},
            },
            {
                "field_name": "environment",
                "index_name": "environment",
                "index_type": "INVERTED",
                "metric_type": None,
                "params": {},
            },
            {
                "field_name": "project_id",
                "index_name": "project_id",
                "index_type": "INVERTED",
                "metric_type": None,
                "params": {},
            },
            {
                "field_name": "tenant_id",
                "index_name": "tenant_id",
                "index_type": "INVERTED",
                "metric_type": None,
                "params": {},
            },
        ],
    }


def _schema_digest() -> str:
    encoded = json.dumps(
        _canonical_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _metadata_description(*, claimed_digest: str | None = None) -> str:
    metadata = {
        "family": "doc",
        "schemaVersion": "doc-schema-v1",
        "schemaSha256": claimed_digest or _schema_digest(),
        "corpusVersion": "corpus-fixture-v1",
        "embeddingModelVersion": "research-embedding-v1",
        "vectorDimension": 1536,
    }
    return "tap-collection-metadata-v1:" + json.dumps(
        metadata,
        sort_keys=True,
        separators=(",", ":"),
    )


def _raw_collection(*, claimed_digest: str | None = None) -> dict[str, object]:
    canonical = _canonical_schema()
    fields = []
    for field_id, field in enumerate(canonical["fields"], start=100):  # type: ignore[union-attr]
        fields.append(
            {
                **field,
                "field_id": field_id,
                "description": "",
            }
        )
    functions = []
    for function in canonical["functions"]:  # type: ignore[union-attr]
        functions.append(
            {
                **function,
                "id": 500,
                "description": "",
                "input_field_ids": [101],
                "output_field_ids": [102],
            }
        )
    return {
        "collection_name": "kb_doc_v1_corpus_fixture_v1",
        "auto_id": False,
        "num_shards": 1,
        "description": _metadata_description(claimed_digest=claimed_digest),
        "fields": fields,
        "functions": functions,
        "aliases": ["kb_doc_active"],
        "collection_id": 123,
        "consistency_level": 0,
        "consistency_level_name": "Strong",
        "properties": {},
        "num_partitions": 1,
        "enable_dynamic_field": False,
        "enable_namespace": False,
    }


def _raw_indexes() -> dict[str, dict[str, object]]:
    canonical = _canonical_schema()
    return {
        index["index_name"]: {
            **index,
            "total_rows": 12,
            "indexed_rows": 12,
            "pending_index_rows": 0,
            "state": "Finished",
        }
        for index in canonical["indexes"]  # type: ignore[union-attr]
    }


def _config(*, timeout_seconds: float = 1.0) -> MilvusSearchConfig:
    return MilvusSearchConfig(
        uri="http://127.0.0.1:19530",
        database="tap_local",
        username="tap_reader",
        password=SecretStr("uri-password-secret"),
        targets={SourceFamily.DOC: _target()},
        timeout_seconds=timeout_seconds,
    )


def _target(*, schema_sha256: str | None = None) -> MilvusIndexTarget:
    return MilvusIndexTarget(
        family=SourceFamily.DOC,
        alias="kb_doc_active",
        physical_name_prefix="kb_doc_v1_",
        schema_version="doc-schema-v1",
        schema_sha256=schema_sha256 or _schema_digest(),
        corpus_version="corpus-fixture-v1",
        embedding_model_version="research-embedding-v1",
        vector_dimension=1536,
    )


def _hybrid_request(
    collection_name: str = "kb_doc_v1_corpus_fixture_v1",
    *,
    limit: int = 5,
) -> MilvusHybridRequest:
    expression = (
        'tenant_id == "tenant-a" and ARRAY_CONTAINS_ANY(allowed_group_ids, ["group-secret"])'
    )
    return MilvusHybridRequest(
        collection_name=collection_name,
        channels=(
            MilvusChannelRequest(
                kind="bm25",
                query="refund policy",
                filter_expression=expression,
                limit=limit,
            ),
            MilvusChannelRequest(
                kind="dense",
                query=(0.1, 0.2, 0.3),
                filter_expression=expression,
                limit=limit,
            ),
        ),
        output_fields=MILVUS_OUTPUT_FIELDS,
        limit=limit,
    )


def _query_request(
    collection_name: str,
    *,
    output_fields: tuple[str, ...] = ("chunk_id",),
    limit: int = 1,
) -> MilvusQueryRequest:
    return MilvusQueryRequest(
        collection_name=collection_name,
        filter_expression='tenant_id == "tenant-a"',
        output_fields=output_fields,
        limit=limit,
    )


class RecordingSDKClient:
    def __init__(self, *, claimed_digest: str | None = None) -> None:
        self.collection = _raw_collection(claimed_digest=claimed_digest)
        self.indexes = _raw_indexes()
        self.calls: list[tuple[str, object]] = []
        self.alias_target = "kb_doc_v1_corpus_fixture_v1"
        self.hybrid_result: list[list[dict[str, object]]] = [
            [
                {
                    "id": "h_" + "1" * 64,
                    "distance": 0.75,
                    "entity": {
                        "chunk_id": "h_" + "1" * 64,
                        "content": "Refunds require approval.",
                    },
                }
            ]
        ]
        self.query_result: list[dict[str, object]] = [{"chunk_id": "h_" + "1" * 64}]

    def describe_alias(self, alias: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("describe_alias", {"alias": alias, **kwargs}))
        return {"alias": alias, "collection_name": self.alias_target, "db_name": "tap_local"}

    def describe_collection(
        self,
        collection_name: str,
        **kwargs: object,
    ) -> dict[str, object]:
        self.calls.append(("describe_collection", {"collection_name": collection_name, **kwargs}))
        return self.collection

    def describe_index(
        self,
        collection_name: str,
        index_name: str,
        **kwargs: object,
    ) -> dict[str, object]:
        self.calls.append(
            (
                "describe_index",
                {"collection_name": collection_name, "index_name": index_name, **kwargs},
            )
        )
        return self.indexes[index_name]

    def hybrid_search(self, **kwargs: object) -> list[list[dict[str, object]]]:
        self.calls.append(("hybrid_search", kwargs))
        return self.hybrid_result

    def query(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append(("query", kwargs))
        return self.query_result

    def close(self) -> None:
        self.calls.append(("close", {}))


class RepeatedNames(Sequence[str]):
    """Stand in for the protobuf repeated scalar container returned by PyMilvus."""

    def __init__(self, values: tuple[str, ...]) -> None:
        self._values = values

    def __getitem__(self, index: int) -> str:
        return self._values[index]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)


@pytest.mark.asyncio
async def test_transport_computes_schema_digest_from_closed_fields_functions_and_indexes() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    resolved = await reader.describe_alias("kb_doc_active")

    descriptor = await reader.describe_collection(resolved)

    assert descriptor.schema_sha256 == _schema_digest()
    assert descriptor.family is SourceFamily.DOC
    assert descriptor.vector_dimension == 1536
    index_calls = [call for call in client.calls if call[0] == "describe_index"]
    assert [call[1]["index_name"] for call in index_calls] == list(_INDEX_FIELDS)  # type: ignore[index]


@pytest.mark.asyncio
async def test_transport_accepts_pymilvus_repeated_function_name_containers() -> None:
    client = RecordingSDKClient()
    function = client.collection["functions"][0]  # type: ignore[index]
    function["input_field_names"] = RepeatedNames(("content",))
    function["output_field_names"] = RepeatedNames(("bm25_sparse",))
    reader = PyMilvusReader(_config(), client=client)
    resolved = await reader.describe_alias("kb_doc_active")

    descriptor = await reader.describe_collection(resolved)

    assert descriptor.schema_sha256 == _schema_digest()


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("is_partition_key", True),
        ("is_dynamic", True),
        ("is_clustering_key", True),
        ("default_value", "forged-default"),
    ),
)
@pytest.mark.asyncio
async def test_transport_rejects_undigested_field_semantics(
    field: str,
    forged: object,
) -> None:
    client = RecordingSDKClient()
    client.collection["fields"][0][field] = forged  # type: ignore[index]
    reader = PyMilvusReader(_config(), client=client)
    resolved = await reader.describe_alias("kb_doc_active")

    with pytest.raises(SearchUnavailable, match="invalid collection"):
        await reader.describe_collection(resolved)


@pytest.mark.parametrize("field", ("auto_id", "enable_namespace"))
@pytest.mark.asyncio
async def test_transport_rejects_widened_collection_semantics(field: str) -> None:
    client = RecordingSDKClient()
    client.collection[field] = True
    reader = PyMilvusReader(_config(), client=client)
    resolved = await reader.describe_alias("kb_doc_active")

    with pytest.raises(SearchUnavailable, match="invalid collection"):
        await reader.describe_collection(resolved)


@pytest.mark.asyncio
async def test_declared_computed_and_configured_schema_digests_must_all_match() -> None:
    valid_reader = PyMilvusReader(_config(), client=RecordingSDKClient())
    bound = await bind_target(valid_reader, _target())
    assert bound.physical_collection == "kb_doc_v1_corpus_fixture_v1"

    declared_forgery = "sha256:" + "d" * 64
    forged_reader = PyMilvusReader(
        _config(),
        client=RecordingSDKClient(claimed_digest=declared_forgery),
    )
    forged_resolved = await forged_reader.describe_alias("kb_doc_active")
    with pytest.raises(SearchUnavailable, match="schema declaration does not match description"):
        await forged_reader.describe_collection(forged_resolved)

    with pytest.raises(SearchUnavailable, match="collection does not match configured target"):
        await bind_target(valid_reader, _target(schema_sha256="sha256:" + "e" * 64))


@pytest.mark.asyncio
async def test_alias_switch_after_binding_cannot_retarget_the_hybrid_request() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    client.alias_target = "kb_doc_v1_corpus_new"

    rows = await reader.hybrid_search(_hybrid_request(bound.physical_collection))

    assert rows == (
        {
            "chunk_id": "h_" + "1" * 64,
            "content": "Refunds require approval.",
            "score": 0.75,
            "provider_request_id": None,
        },
    )
    assert [call[0] for call in client.calls].count("describe_alias") == 1
    search_call = next(call[1] for call in client.calls if call[0] == "hybrid_search")
    assert search_call["collection_name"] == "kb_doc_v1_corpus_fixture_v1"  # type: ignore[index]


@pytest.mark.parametrize(
    ("operation", "collection_name"),
    (
        ("query", "kb_doc_active"),
        ("query", "kb_doc_v1_unvalidated"),
        ("hybrid_search", "kb_doc_active"),
        ("hybrid_search", "kb_doc_v1_unvalidated"),
    ),
)
@pytest.mark.asyncio
async def test_transport_rejects_aliases_and_unvalidated_physical_collections_before_sdk_io(
    operation: str,
    collection_name: str,
) -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)

    with pytest.raises(SearchUnavailable, match="physical collection is not bound"):
        if operation == "query":
            await reader.query(_query_request(collection_name))
        else:
            await reader.hybrid_search(_hybrid_request(collection_name))

    assert operation not in [call[0] for call in client.calls]


@pytest.mark.asyncio
async def test_physical_capability_is_valid_only_after_alias_and_description_validation() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    resolved = await reader.describe_alias("kb_doc_active")

    with pytest.raises(SearchUnavailable, match="physical collection is not bound"):
        await reader.query(_query_request(resolved))

    await reader.describe_collection(resolved)
    rows = await reader.query(_query_request(resolved))
    assert len(rows) == 1


@pytest.mark.parametrize("operation", ("query", "hybrid_search"))
@pytest.mark.asyncio
async def test_provider_rows_cannot_exceed_the_request_limit(operation: str) -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    client.query_result = [
        {"chunk_id": "h_" + "1" * 64},
        {"chunk_id": "h_" + "2" * 64},
    ]
    client.hybrid_result[0].append(
        {
            "id": "h_" + "2" * 64,
            "distance": 0.5,
            "entity": {"chunk_id": "h_" + "2" * 64},
        }
    )

    with pytest.raises(SearchUnavailable, match=f"invalid {operation.split('_')[0]} rows"):
        if operation == "query":
            await reader.query(_query_request(bound.physical_collection, limit=1))
        else:
            await reader.hybrid_search(_hybrid_request(bound.physical_collection, limit=1))


def _nested_list(depth: int) -> list[object]:
    value: list[object] = []
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    ("case", "value"),
    (
        ("depth", _nested_list(100)),
        ("elements", ["value"] * 20_000),
        ("bytes", "sensitive-provider-value" * 500_000),
    ),
    ids=("depth", "elements", "bytes"),
)
@pytest.mark.asyncio
async def test_query_result_normalization_has_finite_resource_budgets(
    case: str,
    value: object,
) -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    client.query_result = [{"content": value}]

    with pytest.raises(SearchUnavailable, match="invalid query rows") as caught:
        await reader.query(_query_request(bound.physical_collection, output_fields=("content",)))

    rendered = str(caught.value) + repr(caught.value)
    assert case not in rendered
    assert "sensitive-provider-value" not in rendered


class MeteredProviderMapping(Mapping[str, object]):
    """Provider mapping that records how far the reader traverses it."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.iterated = 0

    def __getitem__(self, key: str) -> object:
        return "sensitive-provider-value"

    def __iter__(self) -> Iterator[str]:
        for index in range(self.count):
            self.iterated += 1
            yield f"provider_field_{index}"

    def __len__(self) -> int:
        return self.count


@pytest.mark.asyncio
async def test_query_normalization_does_not_eagerly_traverse_oversized_provider_mappings() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    oversized = MeteredProviderMapping(20_001)
    client.query_result = [oversized]

    with pytest.raises(SearchUnavailable, match="invalid query rows") as caught:
        await reader.query(_query_request(bound.physical_collection, output_fields=("content",)))

    assert oversized.iterated <= 20_000
    assert "sensitive-provider-value" not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_query_normalization_counts_arbitrary_precision_integer_bytes() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    client.query_result = [{"content": 1 << (5 * 1024 * 1024 * 8)}]

    with pytest.raises(SearchUnavailable, match="invalid query rows") as caught:
        await reader.query(_query_request(bound.physical_collection, output_fields=("content",)))

    assert "41943040" not in str(caught.value) + repr(caught.value)


class NoEncodeString(str):
    """Fails if transport allocates a complete UTF-8 byte copy to count it."""

    encode_calls: int

    def __new__(cls, value: str) -> NoEncodeString:
        instance = super().__new__(cls, value)
        instance.encode_calls = 0
        return instance

    def encode(self, *args: object, **kwargs: object) -> bytes:
        self.encode_calls += 1
        raise AssertionError("normalization must not allocate a full UTF-8 copy")


@pytest.mark.parametrize("as_key", (False, True), ids=("value", "key"))
@pytest.mark.asyncio
async def test_query_normalization_counts_utf8_incrementally_without_full_copy(
    as_key: bool,
) -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    oversized = NoEncodeString("sensitive-provider-value" * 250_000)
    client.query_result = [{oversized: "safe"} if as_key else {"content": oversized}]

    with pytest.raises(SearchUnavailable, match="invalid query rows") as caught:
        await reader.query(_query_request(bound.physical_collection, output_fields=("content",)))

    assert oversized.encode_calls == 0
    assert "sensitive-provider-value" not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_query_normalization_rejects_surrogate_string_values_without_full_copy() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    surrogate = NoEncodeString("sensitive-provider-value\ud800")
    client.query_result = [{"content": surrogate}]

    with pytest.raises(SearchUnavailable, match="invalid query rows") as caught:
        await reader.query(_query_request(bound.physical_collection, output_fields=("content",)))

    assert surrogate.encode_calls == 0
    assert "sensitive-provider-value" not in str(caught.value) + repr(caught.value)


class UnreadableProviderMapping(Mapping[str, object]):
    """Records whether normalization reaches a value after a rejected mapping key."""

    def __init__(self) -> None:
        self.items_read = False

    def __getitem__(self, key: str) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self) -> Iterator[tuple[str, object]]:
        self.items_read = True
        return iter(())


@pytest.mark.asyncio
async def test_query_normalization_rejects_surrogate_mapping_keys_before_values() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    surrogate_key = NoEncodeString("content\ud800")
    unreadable = UnreadableProviderMapping()
    client.query_result = [{surrogate_key: unreadable}]

    with pytest.raises(SearchUnavailable, match="invalid query rows") as caught:
        await reader.query(_query_request(bound.physical_collection, output_fields=("content",)))

    assert surrogate_key.encode_calls == 0
    assert unreadable.items_read is False
    assert "content" not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_cyclic_query_result_is_sanitized_as_shared_search_unavailable() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    cyclic: dict[str, object] = {}
    cyclic["cycle"] = cyclic
    client.query_result = [{"content": cyclic}]

    with pytest.raises(SearchUnavailable, match="invalid query rows") as caught:
        await reader.query(_query_request(bound.physical_collection, output_fields=("content",)))

    assert "cycle" not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_hybrid_and_query_emit_only_bounded_physical_requests() -> None:
    client = RecordingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    request = _hybrid_request(bound.physical_collection)

    await reader.hybrid_search(request)
    await reader.query(
        MilvusQueryRequest(
            collection_name=request.collection_name,
            filter_expression=request.channels[0].filter_expression,
            output_fields=("chunk_id", "schema_version"),
            limit=1,
        )
    )

    hybrid_call = next(call[1] for call in client.calls if call[0] == "hybrid_search")
    sdk_channels = hybrid_call["reqs"]  # type: ignore[index]
    assert [channel.anns_field for channel in sdk_channels] == ["bm25_sparse", "dense_vector"]
    assert [channel.filter for channel in sdk_channels] == [
        request.channels[0].filter_expression,
        request.channels[0].filter_expression,
    ]
    assert tuple(hybrid_call["output_fields"]) == MILVUS_OUTPUT_FIELDS  # type: ignore[index,call-overload]
    assert not {
        "tenant_id",
        "project_id",
        "allowed_group_ids",
        "classification_rank",
        "environment",
        "deleted",
        "bm25_sparse",
        "dense_vector",
    } & set(hybrid_call["output_fields"])  # type: ignore[arg-type,index]
    query_call = next(call[1] for call in client.calls if call[0] == "query")
    assert query_call["collection_name"] == request.collection_name  # type: ignore[index]
    assert query_call["filter"] == request.channels[0].filter_expression  # type: ignore[index]


@pytest.mark.parametrize(
    "unsafe_fields",
    (("allowed_group_ids",), ("dense_vector",), ("chunk_id", "tenant_id")),
)
def test_request_values_reject_acl_and_vector_output_fields(
    unsafe_fields: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="output fields"):
        MilvusQueryRequest(
            collection_name="kb_doc_v1_corpus_fixture_v1",
            filter_expression='tenant_id == "tenant-a"',
            output_fields=unsafe_fields,
            limit=1,
        )


class FailingSDKClient(RecordingSDKClient):
    def __init__(self, operation: str, error_factory: Callable[[], BaseException]) -> None:
        super().__init__()
        self.operation = operation
        self.error_factory = error_factory

    def _fail(self, operation: str) -> None:
        if self.operation == operation:
            raise self.error_factory()

    def describe_alias(self, alias: str, **kwargs: object) -> dict[str, object]:
        self._fail("describe_alias")
        return super().describe_alias(alias, **kwargs)

    def describe_collection(
        self,
        collection_name: str,
        **kwargs: object,
    ) -> dict[str, object]:
        self._fail("describe_collection")
        return super().describe_collection(collection_name, **kwargs)

    def describe_index(
        self,
        collection_name: str,
        index_name: str,
        **kwargs: object,
    ) -> dict[str, object]:
        self._fail("describe_index")
        return super().describe_index(collection_name, index_name, **kwargs)

    def hybrid_search(self, **kwargs: object) -> list[list[dict[str, object]]]:
        self._fail("hybrid_search")
        return super().hybrid_search(**kwargs)

    def query(self, **kwargs: object) -> list[dict[str, object]]:
        self._fail("query")
        return super().query(**kwargs)

    def close(self) -> None:
        self._fail("close")
        super().close()


@pytest.mark.parametrize(
    "operation",
    ("describe_alias", "describe_collection", "describe_index", "hybrid_search", "query", "close"),
)
@pytest.mark.asyncio
async def test_every_sdk_failure_is_normalized_without_echoing_sensitive_values(
    operation: str,
) -> None:
    sensitive = "uri-password-secret raw-filter group-secret [0.123456789, 0.987654321]"
    client = FailingSDKClient(operation, lambda: RuntimeError(sensitive))
    reader = PyMilvusReader(_config(), client=client)

    async def invoke() -> object:
        if operation == "describe_alias":
            return await reader.describe_alias("kb_doc_active")
        if operation in {"describe_collection", "describe_index"}:
            resolved = await reader.describe_alias("kb_doc_active")
            return await reader.describe_collection(resolved)
        if operation == "hybrid_search":
            bound = await bind_target(reader, _target())
            return await reader.hybrid_search(_hybrid_request(bound.physical_collection))
        if operation == "query":
            bound = await bind_target(reader, _target())
            return await reader.query(
                MilvusQueryRequest(
                    collection_name=bound.physical_collection,
                    filter_expression="raw-filter group-secret",
                    output_fields=("chunk_id",),
                    limit=1,
                )
            )
        return await reader.close()

    with pytest.raises(SearchUnavailable) as caught:
        await invoke()

    rendered = str(caught.value) + repr(caught.value)
    for secret in (
        "uri-password-secret",
        "raw-filter",
        "group-secret",
        "0.123456789",
        "0.987654321",
    ):
        assert secret not in rendered


class SlowSDKClient(RecordingSDKClient):
    def describe_alias(self, alias: str, **kwargs: object) -> dict[str, object]:
        time.sleep(0.05)
        return super().describe_alias(alias, **kwargs)


@pytest.mark.asyncio
async def test_provider_deadline_is_normalized_without_sensitive_request_data() -> None:
    reader = PyMilvusReader(_config(timeout_seconds=0.01), client=SlowSDKClient())

    with pytest.raises(SearchUnavailable, match="search provider deadline exceeded") as caught:
        await reader.describe_alias("kb_doc_active")

    assert "kb_doc_active" not in str(caught.value) + repr(caught.value)


class BlockingSDKClient(RecordingSDKClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def describe_alias(self, alias: str, **kwargs: object) -> dict[str, object]:
        self.started.set()
        self.release.wait(timeout=1)
        return super().describe_alias(alias, **kwargs)


class BlockingQuerySDKClient(RecordingSDKClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def query(self, **kwargs: object) -> list[dict[str, object]]:
        self.started.set()
        self.release.wait(timeout=1)
        return super().query(**kwargs)


@pytest.mark.asyncio
async def test_released_bound_targets_do_not_accumulate_reader_capabilities() -> None:
    reader = PyMilvusReader(_config(), client=RecordingSDKClient())

    for _ in range(8):
        bound = await bind_target(reader, _target())
        capability = bound.physical_collection
        capability_ref = weakref.ref(capability)
        del bound
        del capability
        gc.collect()
        assert capability_ref() is None


@pytest.mark.asyncio
async def test_close_invalidates_overlapping_alias_work_without_repopulating_capabilities() -> None:
    client = BlockingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    alias_task = asyncio.create_task(reader.describe_alias("kb_doc_active"))
    while not client.started.is_set():
        await asyncio.sleep(0)

    close_task = asyncio.create_task(reader.close())
    await asyncio.sleep(0)
    client.release.set()

    with pytest.raises(SearchUnavailable, match="reader is closed"):
        await alias_task
    await close_task

    with pytest.raises(SearchUnavailable, match="reader is closed"):
        await reader.describe_alias("kb_doc_active")


@pytest.mark.asyncio
async def test_close_discards_an_overlapping_bound_query_result() -> None:
    client = BlockingQuerySDKClient()
    reader = PyMilvusReader(_config(), client=client)
    bound = await bind_target(reader, _target())
    query_task = asyncio.create_task(reader.query(_query_request(bound.physical_collection)))
    while not client.started.is_set():
        await asyncio.sleep(0)

    close_task = asyncio.create_task(reader.close())
    await asyncio.sleep(0)
    client.release.set()

    with pytest.raises(SearchUnavailable, match="reader is closed"):
        await query_task
    await close_task


@pytest.mark.asyncio
async def test_caller_cancellation_is_never_swallowed_as_provider_unavailability() -> None:
    client = BlockingSDKClient()
    reader = PyMilvusReader(_config(), client=client)
    task = asyncio.create_task(reader.describe_alias("kb_doc_active"))
    while not client.started.is_set():
        await asyncio.sleep(0)

    task.cancel()
    client.release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_transport_repr_never_contains_configured_credentials() -> None:
    reader = PyMilvusReader(_config(), client=RecordingSDKClient())

    assert "uri-password-secret" not in repr(reader)


def test_schema_fixture_indexes_cover_every_transport_digest_probe() -> None:
    assert set(_raw_indexes()) == set(_INDEX_FIELDS)
    assert isinstance(_raw_collection()["description"], str)
    assert isinstance(_canonical_schema(), Mapping)
