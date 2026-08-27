"""Mutable Athena document-index contract against deterministic provider doubles."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace

import pytest

from tap.modules.knowledge.adapters.milvus.transport import MilvusCollectionDescriptor
from tap.modules.knowledge.adapters.milvus_documents import (
    ATHENA_ALIAS,
    ATHENA_CORPUS_VERSION,
    ATHENA_EMBEDDING_MODEL,
    ATHENA_PHYSICAL_COLLECTION,
    ATHENA_SCHEMA_VERSION,
    AthenaMilvusConfig,
    IndexFenced,
    IndexUnavailable,
    MilvusDocumentIndex,
    ReadyRevisionArtifacts,
    RebuildRejected,
)
from tap.modules.knowledge.domain.documents import ChunkDraft, DocumentId, canonical_sha256
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DeletionTarget,
    EmbeddingArtifact,
    IngestionWork,
    JobKind,
    JobStage,
)
from tap.operations.milvus.contracts import (
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusScopedGrant,
)
from tap.operations.milvus.doc_schema import doc_schema_sha256


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


class MemoryMilvus:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, object]]] = {}
        self.schemas: dict[str, Mapping[str, object]] = {}
        self.aliases: dict[str, str] = {}
        self.grants: dict[tuple[str, str], set[str]] = {}
        self.upsert_batches: list[int] = []
        self.delete_batches: list[int] = []
        self.descriptor_model = ATHENA_EMBEDDING_MODEL
        self.cancel_retirement = False

    async def collection_exists(self, name: str) -> bool:
        return name in self.collections

    async def list_collections(self) -> tuple[str, ...]:
        return tuple(self.collections)

    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None:
        self.collections[name] = {}
        self.schemas[name] = schema

    async def create_indexes(self, name: str) -> None:
        assert name in self.collections

    async def describe_collection(self, name: str) -> MilvusCollectionDescriptor:
        assert name in self.schemas
        return MilvusCollectionDescriptor(
            collection_name=name,
            family=SourceFamily.DOC,
            schema_version=ATHENA_SCHEMA_VERSION,
            schema_sha256=doc_schema_sha256(),
            corpus_version=ATHENA_CORPUS_VERSION,
            embedding_model_version=self.descriptor_model,
            vector_dimension=1536,
            dynamic_fields_enabled=False,
            consistency_level="Strong",
        )

    async def grant_collection(self, name: str, role_name: str) -> None:
        privileges = {
            "tap_reader": READER_TARGET_PRIVILEGES,
            "tap_writer": WRITER_PRIVILEGES,
        }[role_name]
        self.grants.setdefault((name, role_name), set()).update(privileges)

    async def revoke_collection(self, name: str, role_name: str) -> None:
        self.grants[(name, role_name)] = set()
        if self.cancel_retirement and name == ATHENA_PHYSICAL_COLLECTION:
            self.cancel_retirement = False
            raise asyncio.CancelledError

    async def collection_grants(self, name: str, role_name: str) -> frozenset[MilvusScopedGrant]:
        return frozenset(
            MilvusScopedGrant(role_name, "Collection", "default", name, privilege)
            for privilege in self.grants.get((name, role_name), set())
        )

    async def create_alias(self, alias: str, collection_name: str) -> None:
        self.aliases[alias] = collection_name

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        self.aliases[alias] = collection_name

    async def describe_alias(self, alias: str) -> str | None:
        return self.aliases.get(alias)

    async def drop_alias(self, alias: str) -> None:
        self.aliases.pop(alias, None)

    async def drop_collection(self, name: str) -> None:
        self.collections.pop(name, None)

    async def upsert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None:
        self.upsert_batches.append(len(rows))
        for row in rows:
            self.collections[name][str(row["chunk_id"])] = dict(row)

    async def insert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None:
        await self.upsert(name, rows)

    async def delete(self, name: str, chunk_ids: tuple[str, ...]) -> None:
        self.delete_batches.append(len(chunk_ids))
        for chunk_id in chunk_ids:
            self.collections[name].pop(chunk_id, None)

    async def flush(self, name: str) -> None:
        assert name in self.collections

    async def query_persisted_rows(
        self,
        collection_name: str,
        filter_expression: str,
        output_fields: tuple[str, ...],
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        rows = self.collections[collection_name].values()
        if filter_expression.startswith("source_revision == "):
            revision = json.loads(filter_expression.split(" == ", 1)[1])
            rows = (row for row in rows if row.get("source_revision") == revision)
        elif filter_expression.startswith("chunk_id == "):
            chunk_id = json.loads(filter_expression.split(" == ", 1)[1])
            rows = (row for row in rows if row.get("chunk_id") == chunk_id)
        elif filter_expression == 'source_type == "athena_fence"':
            rows = (row for row in rows if row.get("source_type") == "athena_fence")
        elif filter_expression.startswith("corpus_version == "):
            corpus = json.loads(filter_expression.split(" == ", 1)[1])
            rows = (row for row in rows if row.get("corpus_version") == corpus)
        else:
            raise AssertionError(filter_expression)
        return tuple({field: row[field] for field in output_fields} for row in list(rows)[:limit])

    async def close(self) -> None:
        return None


def index_for(memory: MemoryMilvus) -> MilvusDocumentIndex:
    return MilvusDocumentIndex(
        config=AthenaMilvusConfig(),
        provisioner=memory,
        writer=memory,
        reader=memory,
    )


@pytest.mark.asyncio
async def test_ensure_upsert_read_back_delete_and_negative_probe_are_exact() -> None:
    """Returning before exact readback would expose a partially published revision."""
    memory = MemoryMilvus()
    index = index_for(memory)
    target = await index.ensure_target()
    chunks = tuple(chunk(i) for i in range(1, 66))
    embeddings = EmbeddingArtifact(
        ATHENA_EMBEDDING_MODEL,
        1536,
        tuple((float(i),) + (0.0,) * 1535 for i in range(1, 66)),
    )
    receipt = await index.upsert_revision(work(), chunks, embeddings, index_version="athena-v1")

    assert target.physical_collection == ATHENA_PHYSICAL_COLLECTION
    assert target.alias == ATHENA_ALIAS
    assert receipt.indexed_count == 65
    assert memory.upsert_batches[-2:] == [64, 1]
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_reader")] == set(
        READER_TARGET_PRIVILEGES
    )
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_writer")] == set(WRITER_PRIVILEGES)
    first_row = memory.collections[ATHENA_PHYSICAL_COLLECTION][str(chunks[0].chunk_id)]
    assert first_row["logical_chunk_id"] == "h_" + "0" * 63 + "1"
    assert str(first_row["root_id"]).startswith("h_")
    assert str(first_row["parent_id"]).startswith("h_")
    assert first_row["source_id"] == "doc_a"
    assert first_row["source_type"] == "doc"

    target_delete = DeletionTarget("doc_a", "rev_a", tuple(str(c.chunk_id) for c in chunks), ())
    await index.delete_revision(target_delete)
    assert await index.count_revision(target_delete) == 0
    assert memory.delete_batches[-1] == 65


@pytest.mark.asyncio
async def test_durable_fence_blocks_late_upsert_and_survives_index_reconstruction() -> None:
    """An in-process fence set would allow a restarted worker to resurrect deletion."""
    memory = MemoryMilvus()
    first = index_for(memory)
    await first.ensure_target()
    target = DeletionTarget("doc_a", "rev_a", (str(chunk().chunk_id),), ())
    await first.fence_revision(target)
    restarted = index_for(memory)

    with pytest.raises(IndexFenced):
        await restarted.upsert_revision(
            work(),
            (chunk(),),
            EmbeddingArtifact(ATHENA_EMBEDDING_MODEL, 1536, ((0.1,) * 1536,)),
            index_version="athena-v1",
        )

    assert await restarted.count_revision(target) == 0


@pytest.mark.asyncio
async def test_rebuild_uses_fresh_physical_and_switches_alias_only_after_exact_parity() -> None:
    """Switching early would let readers observe a partial rebuild."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    fenced = DeletionTarget("doc_a", "rev_deleted", (str(chunk(9).chunk_id),), ())
    await index.fence_revision(fenced)
    record = ReadyRevisionArtifacts(
        work=work(),
        chunks=(chunk(),),
        embeddings=EmbeddingArtifact(ATHENA_EMBEDDING_MODEL, 1536, ((0.1,) * 1536,)),
        index_version="athena-v1",
    )

    receipt = await index.rebuild((record,))

    assert receipt.physical_collection.startswith(ATHENA_PHYSICAL_COLLECTION + "_")
    assert len(receipt.physical_collection.removeprefix(ATHENA_PHYSICAL_COLLECTION + "_")) == 12
    assert receipt.row_count == 1
    assert receipt.cleanup_facts == (
        f"unaliased_retained_physical:{ATHENA_PHYSICAL_COLLECTION}",
    )
    assert memory.aliases[ATHENA_ALIAS] == receipt.physical_collection
    assert memory.grants[(receipt.physical_collection, "tap_reader")] == set(
        READER_TARGET_PRIVILEGES
    )
    assert memory.grants[(receipt.physical_collection, "tap_writer")] == set(WRITER_PRIVILEGES)

    restarted = index_for(memory)
    assert (await restarted.ensure_target()).physical_collection == receipt.physical_collection
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_reader")] == set()
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_writer")] == set()
    with pytest.raises(IndexFenced):
        await restarted.upsert_revision(
            work("rev_deleted"),
            (chunk(9),),
            EmbeddingArtifact(ATHENA_EMBEDDING_MODEL, 1536, ((0.1,) * 1536,)),
            index_version="athena-v1",
        )


def test_exact_athena_defaults_are_closed() -> None:
    """Default drift would publish rows unreadable by the fixed local reader."""
    config = AthenaMilvusConfig()
    assert (
        config.physical_collection,
        config.alias,
        config.schema_version,
        config.corpus_version,
        config.embedding_model,
        config.vector_dimension,
    ) == (
        ATHENA_PHYSICAL_COLLECTION,
        ATHENA_ALIAS,
        ATHENA_SCHEMA_VERSION,
        ATHENA_CORPUS_VERSION,
        ATHENA_EMBEDDING_MODEL,
        1536,
    )
    with pytest.raises(ValueError):
        replace(config, alias="wrong_alias")


@pytest.mark.asyncio
async def test_wrong_alias_and_stale_collection_metadata_fail_closed() -> None:
    wrong_alias = MemoryMilvus()
    wrong_alias.aliases[ATHENA_ALIAS] = "kb_doc_v1_untrusted"
    with pytest.raises(IndexUnavailable):
        await index_for(wrong_alias).ensure_target()

    stale = MemoryMilvus()
    stale.descriptor_model = "stale-embedding"
    with pytest.raises(IndexUnavailable):
        await index_for(stale).ensure_target()


@pytest.mark.asyncio
async def test_rebuild_failure_and_post_switch_cancellation_restore_old_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    record = ReadyRevisionArtifacts(
        work=work(),
        chunks=(chunk(),),
        embeddings=EmbeddingArtifact(ATHENA_EMBEDDING_MODEL, 1536, ((0.1,) * 1536,)),
        index_version="athena-v1",
    )

    async def fail_parity(*args: object) -> None:
        raise RuntimeError("injected parity failure")

    monkeypatch.setattr(index, "_require_revision_parity", fail_parity)
    with pytest.raises(RebuildRejected) as rejected:
        await index.rebuild((record,))
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION
    assert rejected.value.cleanup_facts[0].startswith("partial_collection:")

    monkeypatch.undo()
    memory.cancel_retirement = True
    with pytest.raises(asyncio.CancelledError):
        await index.rebuild((record,))
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_reader")] == set(
        READER_TARGET_PRIVILEGES
    )
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_writer")] == set(WRITER_PRIVILEGES)


@pytest.mark.asyncio
async def test_close_attempts_every_distinct_client_after_one_fails() -> None:
    closed: list[str] = []

    class CloseOnly:
        def __init__(self, name: str, *, fails: bool = False) -> None:
            self.name = name
            self.fails = fails

        async def close(self) -> None:
            closed.append(self.name)
            if self.fails:
                raise RuntimeError("injected close failure")

    index = MilvusDocumentIndex(
        config=AthenaMilvusConfig(),
        provisioner=CloseOnly("provisioner"),  # type: ignore[arg-type]
        writer=CloseOnly("writer"),  # type: ignore[arg-type]
        reader=CloseOnly("reader", fails=True),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="injected close failure"):
        await index.close()
    assert closed == ["reader", "writer", "provisioner"]
