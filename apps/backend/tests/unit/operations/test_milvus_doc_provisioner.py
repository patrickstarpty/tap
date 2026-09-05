"""Idempotent document-index reconciliation against SDK-shaped doubles."""

from __future__ import annotations

import pytest

from tap.operations.milvus.client import (
    MilvusSdk,
    PyMilvusDocProvisioner,
    PyMilvusDocReader,
)
from tap.operations.milvus.doc_schema import (
    DocCollectionMetadata,
    build_doc_collection_schema,
    doc_schema_sha256,
)


class IndexParams:
    def __init__(self) -> None:
        self.index_name: str | None = None
        self.definition: dict[str, object] = {}

    def add_index(self, **kwargs: object) -> None:
        value = kwargs.get("index_name")
        assert isinstance(value, str)
        self.index_name = value
        self.definition = dict(kwargs)


class PartialIndexClient:
    def __init__(self) -> None:
        self.indexes: set[str] = set()
        self.failed = False
        self.create_attempts: list[str] = []
        self.definitions: dict[str, dict[str, object]] = {}

    def list_indexes(self, collection_name: str, **kwargs: object) -> list[str]:
        assert collection_name == "kb_doc_v1_tapper_demo"
        return sorted(self.indexes)

    def prepare_index_params(self) -> IndexParams:
        return IndexParams()

    def create_index(
        self,
        collection_name: str,
        index_params: IndexParams,
        **kwargs: object,
    ) -> None:
        assert collection_name == "kb_doc_v1_tapper_demo"
        assert index_params.index_name is not None
        self.create_attempts.append(index_params.index_name)
        if index_params.index_name == "bm25_sparse" and not self.failed:
            self.failed = True
            raise RuntimeError("injected second-index interruption")
        if index_params.index_name in self.indexes:
            raise AssertionError("retry attempted to recreate an existing index")
        self.indexes.add(index_params.index_name)
        self.definitions[index_params.index_name] = index_params.definition

    def describe_index(
        self,
        collection_name: str,
        index_name: str,
        **kwargs: object,
    ) -> dict[str, object]:
        assert index_name in self.indexes
        return self.definitions[index_name]

    def load_collection(self, collection_name: str, **kwargs: object) -> None:
        assert collection_name == "kb_doc_v1_tapper_demo"

    def get_load_state(self, collection_name: str, **kwargs: object) -> dict[str, object]:
        assert collection_name == "kb_doc_v1_tapper_demo"
        return {"state": "Loaded"}


class ReleasedCollectionClient:
    def __init__(self) -> None:
        self.loaded = False
        self.events: list[str] = []

    def load_collection(self, collection_name: str, **kwargs: object) -> None:
        assert collection_name == "kb_doc_v1_tapper_demo"
        self.events.append("load")
        self.loaded = True

    def get_load_state(self, collection_name: str, **kwargs: object) -> dict[str, object]:
        assert collection_name == "kb_doc_v1_tapper_demo"
        self.events.append("state")
        return {"state": "Loaded" if self.loaded else "NotLoad"}


class ExactCollectionClient:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.aliases = {"kb_doc_tapper_demo_active": "kb_doc_v1_tapper_demo"}

    def has_collection(self, collection_name: str, **kwargs: object) -> bool:
        assert kwargs["timeout"] == 30.0
        self.names.append(collection_name)
        return collection_name == "kb_doc_v1_tapper_demo" or collection_name in self.aliases

    def describe_alias(self, alias: str, **kwargs: object) -> dict[str, object]:
        assert kwargs["timeout"] == 30.0
        return {
            "alias": alias,
            "collection_name": self.aliases[alias],
            "db_name": "default",
        }

    def list_aliases(self, **kwargs: object) -> object:
        raise AssertionError("exact alias observation must not enumerate aliases")

    def list_collections(self, **kwargs: object) -> object:
        raise AssertionError("exact collection existence must not enumerate other collections")


def sdk() -> MilvusSdk:
    return MilvusSdk(
        client_factory=lambda **kwargs: object(),
        create_schema=lambda **kwargs: object(),
        function_factory=lambda **kwargs: object(),
        ann_search_request_factory=lambda **kwargs: object(),
        ranker_factory=object,
        varchar_type=object(),
        sparse_vector_type=object(),
        float_vector_type=object(),
        array_type=object(),
        int64_type=object(),
        bool_type=object(),
        bm25_function_type=object(),
        permission_error=RuntimeError,
    )


@pytest.mark.asyncio
async def test_partial_index_creation_is_reconciled_after_adapter_reconstruction() -> None:
    client = PartialIndexClient()
    schema = build_doc_collection_schema(
        DocCollectionMetadata(
            schema_version="doc-schema-v1",
            schema_sha256=doc_schema_sha256(),
            corpus_version="tapper-demo-v1",
            embedding_model_version="tapper-embedding",
            vector_dimension=1536,
        )
    )
    first = PyMilvusDocProvisioner(client, sdk(), database_name="default")  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="second-index interruption"):
        await first.create_indexes("kb_doc_v1_tapper_demo", schema)
    assert client.indexes == {"dense_vector"}

    reconstructed = PyMilvusDocProvisioner(
        client,
        sdk(),
        database_name="default",  # type: ignore[arg-type]
    )
    await reconstructed.create_indexes("kb_doc_v1_tapper_demo", schema)
    await reconstructed.validate_collection_indexes("kb_doc_v1_tapper_demo", schema)

    assert client.indexes == set(schema["indexes"])
    assert client.create_attempts.count("dense_vector") == 1


@pytest.mark.asyncio
async def test_index_validation_rejects_extra_collection_scoped_index_inventory() -> None:
    client = PartialIndexClient()
    client.failed = True
    schema = build_doc_collection_schema(
        DocCollectionMetadata(
            schema_version="doc-schema-v1",
            schema_sha256=doc_schema_sha256(),
            corpus_version="tapper-demo-v1",
            embedding_model_version="tapper-embedding",
            vector_dimension=1536,
        )
    )
    provisioner = PyMilvusDocProvisioner(
        client,
        sdk(),
        database_name="default",  # type: ignore[arg-type]
    )
    await provisioner.create_indexes("kb_doc_v1_tapper_demo", schema)
    client.indexes.add("unmanaged_extra")

    with pytest.raises(ValueError, match="canonical schema"):
        await provisioner.validate_collection_indexes("kb_doc_v1_tapper_demo", schema)


@pytest.mark.asyncio
async def test_released_collection_has_independent_load_and_exact_readiness_surface() -> None:
    """Complete indexes do not imply a collection is loaded for stable reader publication."""
    client = ReleasedCollectionClient()
    provisioner = PyMilvusDocProvisioner(
        client,
        sdk(),
        database_name="default",  # type: ignore[arg-type]
    )

    assert await provisioner.is_loaded("kb_doc_v1_tapper_demo") is False
    await provisioner.ensure_loaded("kb_doc_v1_tapper_demo")
    assert await provisioner.is_loaded("kb_doc_v1_tapper_demo") is True
    assert client.events == ["state", "load", "state"]


@pytest.mark.asyncio
async def test_reader_observes_only_exact_collection_and_alias_identities() -> None:
    client = ExactCollectionClient()
    reader = PyMilvusDocReader(client, database_name="default")  # type: ignore[arg-type]

    assert await reader.collection_exists("kb_doc_v1_tapper_demo") is True
    assert await reader.describe_alias("kb_doc_tapper_demo_active") == "kb_doc_v1_tapper_demo"
    assert await reader.describe_alias("kb_doc_missing") is None
    assert client.names == [
        "kb_doc_v1_tapper_demo",
        "kb_doc_tapper_demo_active",
        "kb_doc_missing",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "alias": "kb_doc_other_alias",
            "collection_name": "kb_doc_v1_tapper_demo",
            "db_name": "default",
        },
        {
            "alias": "kb_doc_tapper_demo_active",
            "collection_name": "kb_doc_v1_tapper_demo",
            "db_name": "other",
        },
        {
            "alias": "kb_doc_tapper_demo_active",
            "collection_name": "kb_doc_v1_tapper_demo",
            "db_name": "default",
            "extra": "widened",
        },
        {
            "alias": "kb_doc_tapper_demo_active",
            "collection_name": "",
            "db_name": "default",
        },
        {
            "alias": "kb_doc_tapper_demo_active",
            "collection_name": "kb_doc_v1_tapper_demo",
        },
    ],
)
@pytest.mark.asyncio
async def test_reader_rejects_alias_metadata_not_bound_to_exact_request_and_database(
    payload: dict[str, object],
) -> None:
    client = ExactCollectionClient()
    client.describe_alias = lambda *_args, **_kwargs: payload  # type: ignore[method-assign]
    reader = PyMilvusDocReader(client, database_name="default")  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="alias metadata"):
        await reader.describe_alias("kb_doc_tapper_demo_active")


def _grant_record(
    *,
    object_type: str = "Collection",
    object_name: str = "kb_doc_v1_tapper_demo",
    database_name: str = "default",
    privilege: str = "Search",
) -> dict[str, str]:
    return {
        "object_type": object_type,
        "object_name": object_name,
        "db_name": database_name,
        "role_name": "tap_reader",
        "privilege": privilege,
    }


class GrantInventoryClient:
    def __init__(self, records: list[dict[str, str]]) -> None:
        self.records = records

    def describe_role(self, role_name: str, **kwargs: object) -> dict[str, object]:
        assert role_name == "tap_reader"
        assert kwargs["db_name"] == "default"
        return {"role": role_name, "privileges": self.records}


@pytest.mark.asyncio
async def test_collection_grants_bind_every_same_name_record_to_exact_database_and_type() -> None:
    client = GrantInventoryClient([_grant_record()])
    provisioner = PyMilvusDocProvisioner(
        client,
        sdk(),
        database_name="default",  # type: ignore[arg-type]
    )

    grants = await provisioner.collection_grants(
        "kb_doc_v1_tapper_demo",
        "tap_reader",
    )

    assert {(item.object_type, item.db_name, item.privilege) for item in grants} == {
        ("Collection", "default", "Search")
    }


@pytest.mark.parametrize(
    "records",
    [
        [_grant_record(database_name="other")],
        [
            _grant_record(
                object_type="Global",
                object_name="*",
                database_name="other",
                privilege="DescribeCollection",
            )
        ],
        [_grant_record(), _grant_record(database_name="other", privilege="Query")],
        [_grant_record(object_type="Global", privilege="DescribeCollection")],
        [_grant_record(object_type="Database", privilege="Query")],
        [_grant_record(), _grant_record(object_type="Global", privilege="Query")],
    ],
)
@pytest.mark.asyncio
async def test_collection_grants_reject_wrong_database_or_object_type_for_same_name(
    records: list[dict[str, str]],
) -> None:
    provisioner = PyMilvusDocProvisioner(
        GrantInventoryClient(records),
        sdk(),
        database_name="default",  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="grant metadata"):
        await provisioner.collection_grants(
            "kb_doc_v1_tapper_demo",
            "tap_reader",
        )
