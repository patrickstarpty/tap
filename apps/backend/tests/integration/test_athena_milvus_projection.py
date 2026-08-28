"""Opt-in real Milvus mutable Athena projection gates."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

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

from tap.entrypoints.athena_runtime import OwnedResources  # noqa: E402
from tap.modules.knowledge.adapters.milvus_documents import (  # noqa: E402
    ATHENA_ALIAS,
    ATHENA_PHYSICAL_COLLECTION,
    AthenaMilvusConfig,
    IndexFenced,
    MilvusDocumentIndex,
)
from tap.modules.knowledge.adapters.mysql_projection import (  # noqa: E402
    MysqlProjectionCoordinator,
)
from tap.modules.knowledge.domain.documents import (  # noqa: E402
    PARSER_VERSION,
    ChunkDraft,
    DocumentId,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    revision_id_for,
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
    create_athena_document_clients,
)
from tap.operations.milvus.contracts import (  # noqa: E402
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
)
from tap.platform.db.session import create_engine_and_session_factory  # noqa: E402


def vector(value: float) -> tuple[float, ...]:
    return (value,) + (0.0,) * 1535


def _source_hash(revision_key: str) -> str:
    return (
        "sha256:" + "a" * 64 if revision_key == "rev_a" else canonical_sha256(revision_key.encode())
    )


def work(revision_key: str = "rev_a") -> IngestionWork:
    source_hash = _source_hash(revision_key)
    revision_id = str(revision_id_for(DocumentId("doc_a"), source_hash, PARSER_VERSION))
    return IngestionWork(
        job_id="job_a",
        lease_token="lease_a",
        kind=JobKind.INGESTION,
        stage=JobStage.PUBLISHING,
        document_id="doc_a",
        revision_id=revision_id,
        filename="policy.md",
        media_type="text/markdown",
        source_content_hash=source_hash,
        original_locator=ArtifactLocator(f"athena-originals/revisions/{revision_id}/a"),
        normalized_locator=None,
        chunks_locator=None,
        embeddings_locator=None,
        parser_version="athena-parser-v1",
        chunker_version="athena-structure-512-v1",
        pipeline_version="athena-ingestion-v1",
        manifest=(),
    )


def chunk(index: int = 1, revision_key: str = "rev_a") -> ChunkDraft:
    content = f"Athena policy {index}."
    anchor = json.dumps(
        {"headingPath": [], "type": "document"},
        separators=(",", ":"),
        sort_keys=True,
    )
    source_hash = _source_hash(revision_key)
    revision_id = revision_id_for(DocumentId("doc_a"), source_hash, PARSER_VERSION)
    content_hash = canonical_sha256(content.encode())
    return ChunkDraft(
        chunk_id=chunk_id_for(RevisionId(revision_id), anchor, content_hash),
        logical_chunk_id=logical_chunk_id_for(DocumentId("doc_a"), anchor),
        root_id=DocumentId("doc_a"),
        parent_id=f"block-{index}",
        content=content,
        anchor_json=anchor,
        source_content_hash=source_hash,
        chunk_content_hash=content_hash,
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
    owner = os.getenv("TAP_MILVUS_OWNED_INSTANCE")
    if owner != "task5-athena-owned":
        pytest.fail(
            "real Athena tests require TAP_MILVUS_OWNED_INSTANCE=task5-athena-owned "
            "for a dedicated empty instance"
        )
    uri = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
    database = os.getenv("MILVUS_DATABASE", "default")
    resources = OwnedResources()
    pending_roles: OwnedResources | None = None
    pending_coordinator: OwnedResources | None = None
    owns_middleware = False
    cleanup_milvus = None
    cleanup_authority = None
    primary: BaseException | None = None
    try:
        admin_client = MilvusClient(
            uri=uri,
            user="root",
            password=os.getenv("MILVUS_ROOT_PASSWORD", "tap-local-Root1!"),
            db_name=database,
        )

        async def close_admin() -> None:
            await asyncio.to_thread(admin_client.close)

        resources.callback(close_admin)
        database_url = os.getenv(
            "TAP_DATABASE_URL",
            "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
        )
        engine, _ = create_engine_and_session_factory(database_url)
        resources.push(engine)
        clients = await create_athena_document_clients(
            uri=uri,
            database=database,
            provisioner_username=os.getenv("MILVUS_PROVISIONER_USERNAME", "tap_provisioner"),
            provisioner_password=SecretStr(
                os.getenv("MILVUS_PROVISIONER_PASSWORD", "tap-local-Provisioner1!")
            ),
            writer_username=os.getenv("MILVUS_WRITER_USERNAME", "tap_writer"),
            writer_password=SecretStr(os.getenv("MILVUS_WRITER_PASSWORD", "tap-local-Writer1!")),
            reader_username=os.getenv("MILVUS_READER_USERNAME", "tap_reader"),
            reader_password=SecretStr(os.getenv("MILVUS_READER_PASSWORD", "tap-local-Reader1!")),
            sdk=sdk(),
        )
        provisioner = clients.provisioner
        writer = clients.writer
        reader = clients.reader
        pending_roles = OwnedResources()
        pending_roles.push(provisioner)
        pending_roles.push(writer)
        pending_roles.push(reader)
        authority_namespace = "task5-athena-owned"
        authority_key = f"{authority_namespace}:{ATHENA_ALIAS}"
        coordinator = MysqlProjectionCoordinator(
            engine,
            authority_namespace=authority_namespace,
        )
        pending_coordinator = OwnedResources()
        pending_coordinator.push(coordinator)
        index = MilvusDocumentIndex(
            config=AthenaMilvusConfig(),
            provisioner=provisioner,
            writer=writer,
            reader=reader,
            coordinator=coordinator,
        )
        resources.push(index)
        pending_roles = None
        pending_coordinator = None

        async def clean_authority() -> None:
            async with engine.begin() as connection:
                for table_name in (
                    "knowledge_projection_lineage",
                    "knowledge_projection_cleanup",
                    "knowledge_projection_fence",
                    "knowledge_projection_state",
                ):
                    await connection.execute(
                        text(f"DELETE FROM {table_name} WHERE alias_name=:alias"),
                        {"alias": authority_key},
                    )

        async def admin_collections() -> tuple[str, ...]:
            raw = await asyncio.to_thread(admin_client.list_collections)
            if (
                isinstance(raw, (str, bytes))
                or not isinstance(raw, list)
                or any(not isinstance(item, str) for item in raw)
            ):
                pytest.fail("owned Milvus collection inventory is malformed")
            return tuple(raw)

        async def admin_collection_aliases(collection_name: str) -> tuple[str, ...]:
            raw = await asyncio.to_thread(
                admin_client.list_aliases,
                collection_name=collection_name,
            )
            if isinstance(raw, dict):
                if (
                    set(raw) != {"aliases", "collection_name", "db_name"}
                    or raw.get("collection_name") != collection_name
                    or raw.get("db_name") != database
                ):
                    pytest.fail("owned Milvus alias inventory is malformed")
                raw = raw.get("aliases")
            if (
                isinstance(raw, (str, bytes))
                or not isinstance(raw, list)
                or any(not isinstance(item, (str, dict)) for item in raw)
            ):
                pytest.fail("owned Milvus alias inventory is malformed")
            aliases = tuple(item if isinstance(item, str) else item.get("alias") for item in raw)
            if any(not isinstance(item, str) for item in aliases):
                pytest.fail("owned Milvus alias inventory is malformed")
            return aliases  # type: ignore[return-value]

        async def clean_owned_milvus() -> None:
            # The dedicated empty-instance marker proves this exact bounded inventory
            # was created by the current fixture invocation.
            owned_collections = tuple(
                name
                for name in await admin_collections()
                if name == ATHENA_PHYSICAL_COLLECTION
                or (
                    name.startswith(ATHENA_PHYSICAL_COLLECTION + "_")
                    and len(name) == len(ATHENA_PHYSICAL_COLLECTION) + 13
                    and all(character in "0123456789abcdef" for character in name[-12:])
                )
            )
            alias_target = await reader.describe_alias(ATHENA_ALIAS)
            if alias_target is not None:
                if alias_target not in owned_collections:
                    pytest.fail("owned Milvus alias points outside the fixture receipt set")
                await provisioner.drop_alias(ATHENA_ALIAS)
            for collection_name in owned_collections:
                if await admin_collection_aliases(collection_name):
                    pytest.fail("refusing to drop an owned collection that remains aliased")
                await provisioner.drop_collection(collection_name)

        cleanup_milvus = clean_owned_milvus
        cleanup_authority = clean_authority
        initial_collections = await admin_collections()
        initial_alias = await reader.describe_alias(ATHENA_ALIAS)
        if initial_collections or initial_alias is not None:
            pytest.fail(
                "owned Milvus preflight found pre-existing resources; refusing destructive setup"
            )
        owns_middleware = True
        await clean_authority()
        yield index, provisioner, reader
    except BaseException as error:
        primary = error
    finally:
        if owns_middleware and cleanup_milvus is not None:
            try:
                await cleanup_milvus()
            except BaseException as error:
                primary = _fixture_error(primary, error)
        if owns_middleware and cleanup_authority is not None:
            try:
                await cleanup_authority()
            except BaseException as error:
                primary = _fixture_error(primary, error)
        if pending_roles is not None:
            try:
                await pending_roles.aclose(primary)
            except BaseException as error:
                primary = error
        if pending_coordinator is not None:
            try:
                await pending_coordinator.aclose(primary)
            except BaseException as error:
                primary = error
        try:
            await resources.aclose(primary)
        except BaseException:
            raise


def _fixture_error(
    primary: BaseException | None,
    cleanup: BaseException,
) -> BaseException:
    if primary is None:
        return cleanup
    errors = [primary, cleanup]
    if all(isinstance(error, Exception) for error in errors):
        return ExceptionGroup(
            "owned Milvus fixture cleanup failed",
            [error for error in errors if isinstance(error, Exception)],
        )
    return BaseExceptionGroup("owned Milvus fixture cleanup failed", errors)


@pytest.mark.asyncio
async def test_real_milvus_ensure_upsert_delete_and_durable_late_write_fence(real_index) -> None:  # type: ignore[no-untyped-def]
    index, _, _ = real_index
    await index.ensure_target()
    chunks = (chunk(), chunk(2))
    receipt = await index.upsert_revision(
        work(),
        chunks,
        EmbeddingArtifact(
            "athena-embedding",
            1536,
            (vector(0.1), vector(0.3)),
            tuple(str(item.chunk_id) for item in chunks),
        ),
        index_version="athena-v1",
    )
    target = DeletionTarget(
        "doc_a",
        work().revision_id,
        tuple(str(item.chunk_id) for item in chunks),
        (),
    )

    assert receipt.indexed_count == 2
    assert await index.count_revision(target) == 2
    await index.fence_revision(target)
    await index.delete_revision(target)
    assert await index.count_revision(target) == 0
    with pytest.raises(IndexFenced):
        await index.upsert_revision(
            work(),
            chunks,
            EmbeddingArtifact(
                "athena-embedding",
                1536,
                (vector(0.1), vector(0.3)),
                tuple(str(item.chunk_id) for item in chunks),
            ),
            index_version="athena-v1",
        )
    assert await index.count_revision(target) == 0


@pytest.mark.asyncio
async def test_real_milvus_target_grants_are_exact_without_false_exclusivity(real_index) -> None:  # type: ignore[no-untyped-def]
    index, provisioner, reader = real_index
    await index.ensure_target()

    reader_grants = await provisioner.collection_grants(ATHENA_PHYSICAL_COLLECTION, "tap_reader")
    writer_grants = await provisioner.collection_grants(ATHENA_PHYSICAL_COLLECTION, "tap_writer")
    assert frozenset(item.privilege for item in reader_grants) == READER_TARGET_PRIVILEGES
    assert frozenset(item.privilege for item in writer_grants) == WRITER_PRIVILEGES
    assert await reader.describe_alias(ATHENA_ALIAS) == ATHENA_PHYSICAL_COLLECTION


@pytest.mark.asyncio
async def test_real_milvus_released_complete_target_is_loaded_before_republication(
    real_index,
) -> None:  # type: ignore[no-untyped-def]
    index, provisioner, _ = real_index
    await index.ensure_target()
    await asyncio.to_thread(
        provisioner._client.release_collection,  # type: ignore[attr-defined]
        ATHENA_PHYSICAL_COLLECTION,
    )
    assert await provisioner.is_loaded(ATHENA_PHYSICAL_COLLECTION) is False

    receipt = await index.ensure_target()

    assert receipt.physical_collection == ATHENA_PHYSICAL_COLLECTION
    assert await provisioner.is_loaded(ATHENA_PHYSICAL_COLLECTION) is True
