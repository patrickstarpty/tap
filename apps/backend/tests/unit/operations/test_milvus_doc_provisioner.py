"""Idempotent document-index reconciliation against SDK-shaped doubles."""

from __future__ import annotations

import pytest

from tap.operations.milvus.client import MilvusSdk, PyMilvusDocProvisioner
from tap.operations.milvus.doc_schema import (
    DocCollectionMetadata,
    build_doc_collection_schema,
    doc_schema_sha256,
)


class IndexParams:
    def __init__(self) -> None:
        self.index_name: str | None = None

    def add_index(self, **kwargs: object) -> None:
        value = kwargs.get("index_name")
        assert isinstance(value, str)
        self.index_name = value


class PartialIndexClient:
    def __init__(self) -> None:
        self.indexes: set[str] = set()
        self.failed = False
        self.create_attempts: list[str] = []

    def list_indexes(self, collection_name: str, **kwargs: object) -> list[str]:
        assert collection_name == "kb_doc_v1_athena_demo"
        return sorted(self.indexes)

    def prepare_index_params(self) -> IndexParams:
        return IndexParams()

    def create_index(
        self,
        collection_name: str,
        index_params: IndexParams,
        **kwargs: object,
    ) -> None:
        assert collection_name == "kb_doc_v1_athena_demo"
        assert index_params.index_name is not None
        self.create_attempts.append(index_params.index_name)
        if index_params.index_name == "bm25_sparse" and not self.failed:
            self.failed = True
            raise RuntimeError("injected second-index interruption")
        if index_params.index_name in self.indexes:
            raise AssertionError("retry attempted to recreate an existing index")
        self.indexes.add(index_params.index_name)

    def describe_index(
        self,
        collection_name: str,
        index_name: str,
        **kwargs: object,
    ) -> dict[str, object]:
        assert index_name in self.indexes
        return {"index_name": index_name}

    def load_collection(self, collection_name: str, **kwargs: object) -> None:
        assert collection_name == "kb_doc_v1_athena_demo"

    def get_load_state(self, collection_name: str, **kwargs: object) -> dict[str, object]:
        assert collection_name == "kb_doc_v1_athena_demo"
        return {"state": "Loaded"}


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
            corpus_version="athena-demo-v1",
            embedding_model_version="athena-embedding",
            vector_dimension=1536,
        )
    )
    first = PyMilvusDocProvisioner(client, sdk(), database_name="default")  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="second-index interruption"):
        await first.create_indexes("kb_doc_v1_athena_demo", schema)
    assert client.indexes == {"dense_vector"}

    reconstructed = PyMilvusDocProvisioner(
        client,
        sdk(),
        database_name="default",  # type: ignore[arg-type]
    )
    await reconstructed.create_indexes("kb_doc_v1_athena_demo", schema)

    assert client.indexes == set(schema["indexes"])
    assert client.create_attempts.count("dense_vector") == 1
