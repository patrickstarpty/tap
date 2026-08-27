"""Opt-in real Milvus mutable Athena projection gates."""

from __future__ import annotations

import json
import os

import pytest
import pytest_asyncio

if os.getenv("TAP_RUN_MILVUS_INTEGRATION") != "1":
    pytest.skip("real Milvus suite requires TAP_RUN_MILVUS_INTEGRATION=1", allow_module_level=True)

from pymilvus import (  # type: ignore[import-untyped]  # noqa: E402
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
)
from pymilvus.exceptions import MilvusException  # type: ignore[import-untyped]  # noqa: E402

from tap.modules.knowledge.adapters.milvus_documents import (  # noqa: E402
    ATHENA_ALIAS,
    ATHENA_PHYSICAL_COLLECTION,
    AthenaMilvusConfig,
    IndexFenced,
    MilvusDocumentIndex,
)
from tap.modules.knowledge.domain.documents import (  # noqa: E402
    ChunkDraft,
    DocumentId,
    canonical_sha256,
)
from tap.modules.knowledge.ports.documents import (  # noqa: E402
    ArtifactLocator,
    DeletionTarget,
    EmbeddingArtifact,
    IngestionWork,
    JobKind,
    JobStage,
)
from tap.operations.milvus.client import (  # noqa: E402
    MilvusSdk,
    PyMilvusDocProvisioner,
    PyMilvusDocReader,
    PyMilvusWriter,
)
from tap.operations.milvus.contracts import (  # noqa: E402
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
)


def vector(value: float) -> tuple[float, ...]:
    return (value,) + (0.0,) * 1535


def work(revision_id: str = "rev_a") -> IngestionWork:
    return IngestionWork(
        job_id="job_a",
        lease_token="lease_a",
        kind=JobKind.INGESTION,
        stage=JobStage.PUBLISHING,
        document_id="doc_a",
        revision_id=revision_id,
        filename="policy.md",
        media_type="text/markdown",
        source_content_hash="sha256:" + "a" * 64,
        original_locator=ArtifactLocator("athena-originals/revisions/rev_a/a"),
        normalized_locator=None,
        chunks_locator=None,
        embeddings_locator=None,
        parser_version="athena-parser-v1",
        chunker_version="athena-structure-512-v1",
        pipeline_version="athena-ingestion-v1",
        manifest=(),
    )


def chunk(index: int = 1) -> ChunkDraft:
    content = f"Athena policy {index}."
    return ChunkDraft(
        chunk_id=f"h_{index:064x}",  # type: ignore[arg-type]
        logical_chunk_id=f"lc_{index:064x}",  # type: ignore[arg-type]
        root_id=DocumentId("doc_a"),
        parent_id=f"block-{index}",
        content=content,
        anchor_json=json.dumps(
            {"headingPath": [], "type": "document"},
            separators=(",", ":"),
            sort_keys=True,
        ),
        source_content_hash="sha256:" + "a" * 64,
        chunk_content_hash=canonical_sha256(content.encode()),
    )


def sdk() -> MilvusSdk:
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


@pytest_asyncio.fixture(name="real_index")
async def _real_index():  # type: ignore[no-untyped-def]
    uri = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
    database = os.getenv("MILVUS_DATABASE", "default")
    provisioner_client = MilvusClient(
        uri=uri,
        user=os.getenv("MILVUS_PROVISIONER_USERNAME", "tap_provisioner"),
        password=os.getenv("MILVUS_PROVISIONER_PASSWORD", "tap-local-Provisioner1!"),
        db_name=database,
    )
    writer_client = MilvusClient(
        uri=uri,
        user=os.getenv("MILVUS_WRITER_USERNAME", "tap_writer"),
        password=os.getenv("MILVUS_WRITER_PASSWORD", "tap-local-Writer1!"),
        db_name=database,
    )
    reader_client = MilvusClient(
        uri=uri,
        user=os.getenv("MILVUS_READER_USERNAME", "tap_reader"),
        password=os.getenv("MILVUS_READER_PASSWORD", "tap-local-Reader1!"),
        db_name=database,
    )
    provisioner = PyMilvusDocProvisioner(
        provisioner_client,
        sdk(),
        database_name=database,
    )
    writer = PyMilvusWriter(writer_client)
    reader = PyMilvusDocReader(reader_client)
    index = MilvusDocumentIndex(
        config=AthenaMilvusConfig(),
        provisioner=provisioner,
        writer=writer,
        reader=reader,
    )

    async def cleanup() -> None:
        if await provisioner.describe_alias(ATHENA_ALIAS) is not None:
            await provisioner.drop_alias(ATHENA_ALIAS)
        for collection_name in await provisioner.list_collections():
            if collection_name == ATHENA_PHYSICAL_COLLECTION or (
                collection_name.startswith(ATHENA_PHYSICAL_COLLECTION + "_")
                and len(collection_name) == len(ATHENA_PHYSICAL_COLLECTION) + 13
            ):
                await provisioner.drop_collection(collection_name)

    await cleanup()
    try:
        yield index, provisioner
    finally:
        await cleanup()
        await index.close()


@pytest.mark.asyncio
async def test_real_milvus_ensure_upsert_delete_and_durable_late_write_fence(real_index) -> None:  # type: ignore[no-untyped-def]
    index, _ = real_index
    await index.ensure_target()
    chunks = (chunk(), chunk(2))
    receipt = await index.upsert_revision(
        work(),
        chunks,
        EmbeddingArtifact("athena-embedding", 1536, (vector(0.1), vector(0.3))),
        index_version="athena-v1",
    )
    target = DeletionTarget("doc_a", "rev_a", tuple(str(item.chunk_id) for item in chunks), ())

    assert receipt.indexed_count == 2
    assert await index.count_revision(target) == 2
    await index.fence_revision(target)
    await index.delete_revision(target)
    assert await index.count_revision(target) == 0
    with pytest.raises(IndexFenced):
        await index.upsert_revision(
            work(),
            chunks,
            EmbeddingArtifact("athena-embedding", 1536, (vector(0.1), vector(0.3))),
            index_version="athena-v1",
        )
    assert await index.count_revision(target) == 0


@pytest.mark.asyncio
async def test_real_milvus_target_grants_are_exact_without_false_exclusivity(real_index) -> None:  # type: ignore[no-untyped-def]
    index, provisioner = real_index
    await index.ensure_target()

    reader_grants = await provisioner.collection_grants(ATHENA_PHYSICAL_COLLECTION, "tap_reader")
    writer_grants = await provisioner.collection_grants(ATHENA_PHYSICAL_COLLECTION, "tap_writer")
    assert frozenset(item.privilege for item in reader_grants) == READER_TARGET_PRIVILEGES
    assert frozenset(item.privilege for item in writer_grants) == WRITER_PRIVILEGES
    assert await provisioner.describe_alias(ATHENA_ALIAS) == ATHENA_PHYSICAL_COLLECTION
