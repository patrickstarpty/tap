"""Mutable Athena document-index contract against deterministic provider doubles."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import asynccontextmanager
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
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    ChunkDraft,
    DocumentId,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    revision_id_for,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DeletionTarget,
    EmbeddingArtifact,
    IngestionWork,
    JobKind,
    JobStage,
)
from tap.modules.knowledge.ports.projection import ProjectionOwnershipReceipt
from tap.operations.milvus.contracts import (
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusScopedGrant,
)
from tap.operations.milvus.doc_schema import doc_schema_sha256

EXPECTED_INDEXES = frozenset(
    {
        "dense_vector",
        "bm25_sparse",
        "tenant_id",
        "project_id",
        "allowed_group_ids",
        "classification_rank",
        "environment",
        "corpus_version",
        "deleted",
    }
)


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
        chunk_id=chunk_id_for(revision_id, anchor, content_hash),
        logical_chunk_id=logical_chunk_id_for(DocumentId("doc_a"), anchor),
        root_id=DocumentId("doc_a"),
        parent_id=f"block-{index}",
        content=content,
        anchor_json=anchor,
        source_content_hash=source_hash,
        chunk_content_hash=content_hash,
    )


class MemoryProjectionCoordinator:
    """Shared durable-authority double; adapter instances share one state object."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.generation = 0
        self.physical: str | None = None
        self.fence_records: dict[str, str] = {}
        self.cleanup: dict[str, int] = {}
        self.owned: dict[str, ProjectionOwnershipReceipt] = {}
        self.cancel_activation = False
        self.activate_error_before_success = False
        self.activate_error_after_success = False
        self.cancel_release_after_body = False

    @asynccontextmanager
    async def mutation(self, alias: str):  # type: ignore[no-untyped-def]
        assert alias == ATHENA_ALIAS
        async with self.lock:
            try:
                yield self
            finally:
                if self.cancel_release_after_body:
                    self.cancel_release_after_body = False
                    raise asyncio.CancelledError("lease release cancelled after commit")

    async def state(self) -> tuple[int, str | None]:
        return self.generation, self.physical

    async def initialize(self, physical: str) -> tuple[int, str]:
        if self.physical is None:
            self.generation = 1
            self.physical = physical
        return self.generation, self.physical

    async def is_fenced(self, revision_id: str) -> bool:
        return revision_id in self.fence_records

    async def record_fence(self, revision_id: str, document_id: str) -> None:
        existing = self.fence_records.setdefault(revision_id, document_id)
        if existing != document_id:
            raise RuntimeError("fence document identity conflicts")

    async def fences(self, limit: int) -> tuple[tuple[str, str], ...]:
        return tuple(self.fence_records.items())[:limit]

    async def activate(self, physical: str) -> tuple[int, str]:
        if self.physical != physical:
            self.generation += 1
            self.physical = physical
        return self.generation, physical

    async def enqueue_cleanup(self, physical: str) -> None:
        self.cleanup.setdefault(physical, self.generation)

    async def pending_cleanup(self, limit: int) -> tuple[str, ...]:
        return tuple(self.cleanup)[:limit]

    async def complete_cleanup(self, physical: str) -> None:
        self.cleanup.pop(physical, None)

    async def reserve_build(
        self,
        physical: str,
        predecessor: str,
        operation_id: str,
    ) -> ProjectionOwnershipReceipt:
        if physical in self.owned:
            raise RuntimeError("physical collection ownership already exists")
        receipt = ProjectionOwnershipReceipt(
            physical_collection=physical,
            operation_id=operation_id,
            predecessor_collection=predecessor,
            status="building",
        )
        self.owned[physical] = receipt
        return receipt

    async def ownership(self, physical: str) -> ProjectionOwnershipReceipt | None:
        return self.owned.get(physical)

    async def activate_build(
        self,
        receipt: ProjectionOwnershipReceipt,
    ) -> tuple[int, str]:
        if self.cancel_activation:
            self.cancel_activation = False
            raise asyncio.CancelledError
        if self.activate_error_before_success:
            self.activate_error_before_success = False
            raise RuntimeError("activation failed before durable commit")
        current = self.owned.get(receipt.physical_collection)
        if (
            current != receipt
            or receipt.status != "building"
            or self.physical != receipt.predecessor_collection
        ):
            raise RuntimeError("projection build ownership conflicts")
        if self.physical is not None:
            predecessor = self.owned.get(self.physical)
            if predecessor is not None and predecessor.status == "active":
                self.owned[self.physical] = replace(predecessor, status="cleanup")
        self.generation += 1
        self.physical = receipt.physical_collection
        self.owned[receipt.physical_collection] = replace(receipt, status="active")
        if self.activate_error_after_success:
            self.activate_error_after_success = False
            raise RuntimeError("activation response lost after durable commit")
        return self.generation, self.physical

    async def abandon_build(self, receipt: ProjectionOwnershipReceipt) -> None:
        current = self.owned.get(receipt.physical_collection)
        if current != receipt or receipt.status != "building":
            raise RuntimeError("projection build ownership conflicts")
        self.owned[receipt.physical_collection] = replace(receipt, status="cleanup")

    async def owned_cleanup(self, limit: int) -> tuple[ProjectionOwnershipReceipt, ...]:
        return tuple(
            item for item in self.owned.values() if item.status in {"building", "cleanup"}
        )[:limit]

    async def verify_cleanup(self, receipt: ProjectionOwnershipReceipt) -> bool:
        return self.owned.get(receipt.physical_collection) == receipt and receipt.status in {
            "building",
            "cleanup",
        }

    async def complete_owned_cleanup(self, receipt: ProjectionOwnershipReceipt) -> None:
        if not await self.verify_cleanup(receipt):
            raise RuntimeError("projection cleanup ownership conflicts")
        self.owned.pop(receipt.physical_collection)

    async def close(self) -> None:
        return


class MemoryMilvus:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, object]]] = {}
        self.schemas: dict[str, Mapping[str, object]] = {}
        self.indexes: dict[str, set[str]] = {}
        self.loaded: set[str] = set()
        self.aliases: dict[str, str] = {}
        self.grants: dict[tuple[str, str], set[str]] = {}
        self.upsert_batches: list[int] = []
        self.delete_batches: list[int] = []
        self.descriptor_model = ATHENA_EMBEDDING_MODEL
        self.retirement_outcome: str | None = None
        self.fail_index_creations = 0
        self.fail_after_index_creations: int | None = None
        self.create_alias_error_after_success = False
        self.alter_alias_error_after_success = False
        self.events: list[str] = []
        self.coordinator = MemoryProjectionCoordinator()
        self.require_fresh_receipt_before_create = False

    async def collection_exists(self, name: str) -> bool:
        return name in self.collections

    async def list_collections(self) -> tuple[str, ...]:
        return tuple(self.collections)

    async def collection_aliases(self, name: str) -> tuple[str, ...]:
        return tuple(alias for alias, target in self.aliases.items() if target == name)

    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None:
        if (
            self.require_fresh_receipt_before_create
            and name != ATHENA_PHYSICAL_COLLECTION
            and name not in self.coordinator.owned
        ):
            raise AssertionError("fresh collection create preceded its ownership receipt")
        self.events.append(f"create-collection:{name}")
        self.collections[name] = {}
        self.schemas[name] = schema

    async def create_indexes(self, name: str, schema: Mapping[str, object] | None = None) -> None:
        assert name in self.collections
        assert schema is None or schema == self.schemas[name]
        self.events.append(f"create-indexes:{name}")
        if self.fail_index_creations:
            self.fail_index_creations -= 1
            raise RuntimeError("injected index creation interruption")
        present = self.indexes.setdefault(name, set())
        created = 0
        for index_name in EXPECTED_INDEXES:
            if index_name in present:
                continue
            if self.fail_after_index_creations == created:
                self.fail_after_index_creations = None
                raise RuntimeError("injected partial index creation interruption")
            present.add(index_name)
            created += 1

    async def ensure_loaded(self, name: str) -> None:
        self.events.append(f"load:{name}")
        self.loaded.add(name)

    async def is_loaded(self, name: str) -> bool:
        self.events.append(f"load-state:{name}")
        return name in self.loaded

    async def describe_collection_schema(
        self, name: str, schema: Mapping[str, object]
    ) -> MilvusCollectionDescriptor:
        self.events.append(f"describe-schema:{name}")
        assert name in self.schemas
        assert schema == self.schemas[name]
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

    async def describe_collection(self, name: str) -> MilvusCollectionDescriptor:
        self.events.append(f"describe-full:{name}")
        if self.indexes.get(name) != EXPECTED_INDEXES:
            raise RuntimeError("indexes are incomplete")
        return await self.describe_collection_schema(name, self.schemas[name])

    async def grant_collection(self, name: str, role_name: str) -> None:
        self.events.append(f"grant:{role_name}:{name}")
        privileges = {
            "tap_reader": READER_TARGET_PRIVILEGES,
            "tap_writer": WRITER_PRIVILEGES,
        }[role_name]
        self.grants.setdefault((name, role_name), set()).update(privileges)

    async def revoke_collection(self, name: str, role_name: str) -> None:
        self.events.append(f"revoke:{role_name}:{name}")
        if self.retirement_outcome == "error_before" and name == ATHENA_PHYSICAL_COLLECTION:
            self.retirement_outcome = None
            raise RuntimeError("injected retirement interruption")
        self.grants[(name, role_name)] = set()
        if self.retirement_outcome == "error_after" and name == ATHENA_PHYSICAL_COLLECTION:
            self.retirement_outcome = None
            raise RuntimeError("injected lost retirement response")
        if self.retirement_outcome == "cancel_after" and name == ATHENA_PHYSICAL_COLLECTION:
            self.retirement_outcome = None
            raise asyncio.CancelledError

    async def collection_grants(self, name: str, role_name: str) -> frozenset[MilvusScopedGrant]:
        return frozenset(
            MilvusScopedGrant(role_name, "Collection", "default", name, privilege)
            for privilege in self.grants.get((name, role_name), set())
        )

    async def create_alias(self, alias: str, collection_name: str) -> None:
        self.events.append(f"create-alias:{collection_name}")
        self.aliases[alias] = collection_name
        if self.create_alias_error_after_success:
            self.create_alias_error_after_success = False
            raise RuntimeError("injected success-then-error alias outcome")

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        self.events.append(f"alter-alias:{collection_name}")
        self.aliases[alias] = collection_name
        if self.alter_alias_error_after_success:
            self.alter_alias_error_after_success = False
            raise RuntimeError("injected success-then-error alter alias outcome")

    async def describe_alias(self, alias: str) -> str | None:
        return self.aliases.get(alias)

    async def drop_alias(self, alias: str) -> None:
        self.aliases.pop(alias, None)

    async def drop_collection(self, name: str) -> None:
        self.events.append(f"drop-collection:{name}")
        self.collections.pop(name, None)
        self.schemas.pop(name, None)
        self.indexes.pop(name, None)
        for key in tuple(self.grants):
            if key[0] == name:
                self.grants.pop(key)

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
        coordinator=memory.coordinator,
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
        tuple(str(item.chunk_id) for item in chunks),
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
    assert first_row["logical_chunk_id"] == "h_" + str(chunks[0].logical_chunk_id)[3:]
    assert str(first_row["root_id"]).startswith("h_")
    assert str(first_row["parent_id"]).startswith("h_")
    assert first_row["source_id"] == "doc_a"
    assert first_row["source_type"] == "doc"

    target_delete = DeletionTarget(
        "doc_a", work().revision_id, tuple(str(c.chunk_id) for c in chunks), ()
    )
    await index.delete_revision(target_delete)
    assert await index.count_revision(target_delete) == 0
    assert memory.delete_batches[-1] == 65


@pytest.mark.asyncio
async def test_durable_fence_blocks_late_upsert_and_survives_index_reconstruction() -> None:
    """An in-process fence set would allow a restarted worker to resurrect deletion."""
    memory = MemoryMilvus()
    first = index_for(memory)
    await first.ensure_target()
    target = DeletionTarget("doc_a", work().revision_id, (str(chunk().chunk_id),), ())
    await first.fence_revision(target)
    restarted = index_for(memory)

    with pytest.raises(IndexFenced):
        await restarted.upsert_revision(
            work(),
            (chunk(),),
            EmbeddingArtifact(
                ATHENA_EMBEDDING_MODEL,
                1536,
                ((0.1,) * 1536,),
                (str(chunk().chunk_id),),
            ),
            index_version="athena-v1",
        )

    assert await restarted.count_revision(target) == 0


@pytest.mark.asyncio
async def test_rebuild_uses_fresh_physical_and_switches_alias_only_after_exact_parity() -> None:
    """Switching early would let readers observe a partial rebuild."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    fenced = DeletionTarget(
        "doc_a",
        work("rev_deleted").revision_id,
        (str(chunk(9, "rev_deleted").chunk_id),),
        (),
    )
    await index.fence_revision(fenced)
    record = ReadyRevisionArtifacts(
        work=work(),
        chunks=(chunk(),),
        embeddings=EmbeddingArtifact(
            ATHENA_EMBEDDING_MODEL,
            1536,
            ((0.1,) * 1536,),
            (str(chunk().chunk_id),),
        ),
        index_version="athena-v1",
    )

    receipt = await index.rebuild((record,))

    assert receipt.physical_collection.startswith(ATHENA_PHYSICAL_COLLECTION + "_")
    assert len(receipt.physical_collection.removeprefix(ATHENA_PHYSICAL_COLLECTION + "_")) == 12
    assert receipt.row_count == 1
    assert receipt.cleanup_facts == (f"retained_legacy_physical:{ATHENA_PHYSICAL_COLLECTION}",)
    assert memory.aliases[ATHENA_ALIAS] == receipt.physical_collection
    assert memory.grants[(receipt.physical_collection, "tap_reader")] == set(
        READER_TARGET_PRIVILEGES
    )
    assert memory.grants[(receipt.physical_collection, "tap_writer")] == set(WRITER_PRIVILEGES)
    assert await memory.collection_aliases(ATHENA_PHYSICAL_COLLECTION) == ()
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_reader")] == set()
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_writer")] == set()

    restarted = index_for(memory)
    assert (await restarted.ensure_target()).physical_collection == receipt.physical_collection
    assert ATHENA_PHYSICAL_COLLECTION in memory.collections
    with pytest.raises(IndexFenced):
        await restarted.upsert_revision(
            work("rev_deleted"),
            (chunk(9, "rev_deleted"),),
            EmbeddingArtifact(
                ATHENA_EMBEDDING_MODEL,
                1536,
                ((0.1,) * 1536,),
                (str(chunk(9, "rev_deleted").chunk_id),),
            ),
            index_version="athena-v1",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retirement_outcome", "expected_error"),
    (
        ("error_before", RebuildRejected),
        ("error_after", RebuildRejected),
        ("cancel_after", asyncio.CancelledError),
    ),
)
async def test_rebuild_retirement_interruption_restores_old_alias_and_exact_grants(
    retirement_outcome: str,
    expected_error: type[BaseException],
) -> None:
    """Any uncertain precommit old-grant revoke must remain rollback-safe."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    memory.retirement_outcome = retirement_outcome

    with pytest.raises(expected_error):
        await index.rebuild((ready_record(),))

    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_reader")] == set(
        READER_TARGET_PRIVILEGES
    )
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_writer")] == set(WRITER_PRIVILEGES)


def ready_record(revision_id: str = "rev_a", index: int = 1) -> ReadyRevisionArtifacts:
    item = chunk(index, revision_id)
    return ReadyRevisionArtifacts(
        work=work(revision_id),
        chunks=(item,),
        embeddings=EmbeddingArtifact(
            ATHENA_EMBEDDING_MODEL,
            1536,
            ((0.1,) * 1536,),
            (str(item.chunk_id),),
        ),
        index_version="athena-v1",
    )


@pytest.mark.asyncio
async def test_rebuild_excludes_stale_ready_record_with_existing_durable_fence() -> None:
    """A stale MySQL ready snapshot must not resurrect a revision fenced before rebuild."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    target = DeletionTarget("doc_a", work().revision_id, (str(chunk().chunk_id),), ())
    await index.fence_revision(target)

    receipt = await index.rebuild((ready_record(),))

    assert receipt.row_count == 0
    assert await index.count_revision(target) == 0
    with pytest.raises(IndexFenced):
        await index.upsert_revision(
            work(),
            (chunk(),),
            ready_record().embeddings,
            index_version="athena-v1",
        )


@pytest.mark.asyncio
async def test_concurrent_fence_after_snapshot_applies_to_activated_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fence/rebuild interleaving must not split the tombstone and document generations."""
    memory = MemoryMilvus()
    rebuilding = index_for(memory)
    deleting = index_for(memory)
    await rebuilding.ensure_target()
    entered = asyncio.Event()
    release = asyncio.Event()
    original = rebuilding._require_fence_parity

    async def barrier(*args, **kwargs):  # type: ignore[no-untyped-def]
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(rebuilding, "_require_fence_parity", barrier)
    rebuild_task = asyncio.create_task(rebuilding.rebuild((ready_record(),)))
    await entered.wait()
    target = DeletionTarget("doc_a", work().revision_id, (str(chunk().chunk_id),), ())
    fence_task = asyncio.create_task(deleting.fence_revision(target))
    await asyncio.sleep(0)
    release.set()
    await rebuild_task
    await fence_task

    assert await deleting.count_revision(target) == 0
    active = memory.aliases[ATHENA_ALIAS]
    assert any(
        row.get("source_type") == "athena_fence" for row in memory.collections[active].values()
    )


@pytest.mark.asyncio
async def test_concurrent_upsert_during_rebuild_lands_on_activated_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upsert that races alias switch must not finish only on the retired physical."""
    memory = MemoryMilvus()
    rebuilding = index_for(memory)
    publishing = index_for(memory)
    await rebuilding.ensure_target()
    entered = asyncio.Event()
    release = asyncio.Event()
    original = rebuilding._require_fence_parity

    async def barrier(*args, **kwargs):  # type: ignore[no-untyped-def]
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(rebuilding, "_require_fence_parity", barrier)
    rebuild_task = asyncio.create_task(rebuilding.rebuild((ready_record(),)))
    await entered.wait()
    late = ready_record("rev_b", 2)
    upsert_task = asyncio.create_task(
        publishing.upsert_revision(
            late.work,
            late.chunks,
            late.embeddings,
            index_version=late.index_version,
        )
    )
    await asyncio.sleep(0)
    release.set()
    await rebuild_task
    await upsert_task

    assert (
        await publishing.count_revision(
            DeletionTarget(
                "doc_a",
                work("rev_b").revision_id,
                (str(chunk(2, "rev_b").chunk_id),),
                (),
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_empty_rebuild_preserves_fences_and_switches_complete_empty_projection() -> None:
    """Deleting the final ready document still requires a valid fresh target and fence parity."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    target = DeletionTarget(
        "doc_a",
        work("rev_deleted").revision_id,
        (str(chunk(9, "rev_deleted").chunk_id),),
        (),
    )
    await index.fence_revision(target)

    receipt = await index.rebuild(())

    assert receipt.row_count == 0
    assert memory.aliases[ATHENA_ALIAS] == receipt.physical_collection
    with pytest.raises(IndexFenced):
        await index.upsert_revision(
            work("rev_deleted"),
            (chunk(9, "rev_deleted"),),
            EmbeddingArtifact(
                ATHENA_EMBEDDING_MODEL,
                1536,
                ((0.1,) * 1536,),
                (str(chunk(9, "rev_deleted").chunk_id),),
            ),
            index_version="athena-v1",
        )


@pytest.mark.asyncio
async def test_rebuild_resolves_success_then_error_alias_outcome_from_provider_truth() -> None:
    """A lost alter response after server success must not rollback a verified fresh target."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    memory.alter_alias_error_after_success = True

    receipt = await index.rebuild((ready_record(),))

    assert memory.aliases[ATHENA_ALIAS] == receipt.physical_collection
    assert receipt.row_count == 1


@pytest.mark.asyncio
async def test_repeated_rebuilds_bound_owned_unaliased_cleanup_queue() -> None:
    """A durable cleanup owner must prevent one leaked physical per successful rebuild."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()

    for _ in range(3):
        await index.rebuild((ready_record(),))

    assert len(memory.collections) <= 3
    assert len([name for name in memory.collections if name != ATHENA_PHYSICAL_COLLECTION]) <= 2
    assert ATHENA_PHYSICAL_COLLECTION in memory.collections
    assert len([item for item in memory.coordinator.owned.values() if item.status == "active"]) == 1
    assert (
        len([item for item in memory.coordinator.owned.values() if item.status == "cleanup"]) <= 1
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
    assert ATHENA_ALIAS not in stale.aliases
    assert not stale.grants


@pytest.mark.asyncio
async def test_ensure_validates_full_descriptor_before_any_grant_or_alias_publish() -> None:
    """Reader authority or stable discovery must never precede index/metadata validation."""
    memory = MemoryMilvus()

    await index_for(memory).ensure_target()

    full = memory.events.index(f"describe-full:{ATHENA_PHYSICAL_COLLECTION}")
    loaded = memory.events.index(f"load-state:{ATHENA_PHYSICAL_COLLECTION}")
    reader_grant = memory.events.index(f"grant:tap_reader:{ATHENA_PHYSICAL_COLLECTION}")
    writer_grant = memory.events.index(f"grant:tap_writer:{ATHENA_PHYSICAL_COLLECTION}")
    alias = memory.events.index(f"create-alias:{ATHENA_PHYSICAL_COLLECTION}")
    assert full < loaded < reader_grant < alias
    assert full < loaded < writer_grant < alias


@pytest.mark.asyncio
@pytest.mark.parametrize("alias_exists", [False, True])
async def test_ensure_loads_complete_but_released_target_before_any_publication(
    alias_exists: bool,
) -> None:
    """A complete but released target must not be granted or published before load-ready."""
    memory = MemoryMilvus()
    await memory.create_collection(ATHENA_PHYSICAL_COLLECTION, index_for(memory)._schema())
    memory.indexes[ATHENA_PHYSICAL_COLLECTION] = set(EXPECTED_INDEXES)
    if alias_exists:
        memory.aliases[ATHENA_ALIAS] = ATHENA_PHYSICAL_COLLECTION
    memory.events.clear()

    receipt = await index_for(memory).ensure_target()

    assert receipt.physical_collection == ATHENA_PHYSICAL_COLLECTION
    load = memory.events.index(f"load:{ATHENA_PHYSICAL_COLLECTION}")
    loaded = memory.events.index(f"load-state:{ATHENA_PHYSICAL_COLLECTION}")
    reader_grant = memory.events.index(f"grant:tap_reader:{ATHENA_PHYSICAL_COLLECTION}")
    writer_grant = memory.events.index(f"grant:tap_writer:{ATHENA_PHYSICAL_COLLECTION}")
    assert load < loaded < reader_grant
    assert load < loaded < writer_grant
    if not alias_exists:
        assert loaded < memory.events.index(f"create-alias:{ATHENA_PHYSICAL_COLLECTION}")


@pytest.mark.asyncio
async def test_ensure_reconciles_owned_partial_indexes_after_restart_before_publish() -> None:
    """An interrupted index build must retry from durable collection schema, not publish partial."""
    memory = MemoryMilvus()
    memory.fail_index_creations = 1

    with pytest.raises(IndexUnavailable):
        await index_for(memory).ensure_target()
    assert ATHENA_PHYSICAL_COLLECTION in memory.collections
    assert ATHENA_PHYSICAL_COLLECTION not in memory.indexes
    assert ATHENA_ALIAS not in memory.aliases
    assert not memory.grants

    receipt = await index_for(memory).ensure_target()

    assert receipt.physical_collection == ATHENA_PHYSICAL_COLLECTION
    assert ATHENA_PHYSICAL_COLLECTION in memory.indexes
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION


@pytest.mark.asyncio
async def test_ensure_reconciles_one_created_index_then_interruption_after_restart() -> None:
    """A retry must describe and create only missing indexes after a partial SDK call."""
    memory = MemoryMilvus()
    memory.fail_after_index_creations = 1

    with pytest.raises(IndexUnavailable):
        await index_for(memory).ensure_target()
    assert len(memory.indexes[ATHENA_PHYSICAL_COLLECTION]) == 1
    assert ATHENA_ALIAS not in memory.aliases
    assert not memory.grants

    await index_for(memory).ensure_target()

    assert memory.indexes[ATHENA_PHYSICAL_COLLECTION] == EXPECTED_INDEXES
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION


@pytest.mark.asyncio
async def test_ensure_queries_alias_after_success_then_error_outcome() -> None:
    """A lost alias response must be resolved from provider truth before reporting failure."""
    memory = MemoryMilvus()
    memory.create_alias_error_after_success = True

    receipt = await index_for(memory).ensure_target()

    assert receipt.physical_collection == ATHENA_PHYSICAL_COLLECTION
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION


@pytest.mark.asyncio
async def test_external_legal_alias_drift_never_manufactures_cleanup_ownership() -> None:
    """A legal name and valid schema are not evidence that this authority owns deletion."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    external = ATHENA_PHYSICAL_COLLECTION + "_" + "e" * 12
    await memory.create_collection(external, index._schema())
    memory.indexes[external] = set(EXPECTED_INDEXES)
    memory.loaded.add(external)
    memory.aliases[ATHENA_ALIAS] = external

    with pytest.raises(IndexUnavailable, match="ownership"):
        await index.ensure_target()

    assert ATHENA_PHYSICAL_COLLECTION in memory.collections
    assert f"drop-collection:{ATHENA_PHYSICAL_COLLECTION}" not in memory.events
    assert memory.coordinator.cleanup == {}


def test_direct_milvus_publication_rejects_coordinated_revision_rebinding() -> None:
    """The provider trust boundary must recompute revision, logical, and chunk identities."""
    memory = MemoryMilvus()
    index = index_for(memory)
    document_b = DocumentId("doc_b")
    source_b = "sha256:" + "b" * 64
    revision_a = str(revision_id_for(DocumentId("doc_a"), "sha256:" + "a" * 64, PARSER_VERSION))
    anchor = '{"type":"document"}'
    content = "Rebound publication."
    content_hash = canonical_sha256(content.encode())
    rebound = ChunkDraft(
        chunk_id=chunk_id_for(RevisionId(revision_a), anchor, content_hash),
        logical_chunk_id=logical_chunk_id_for(document_b, anchor),
        root_id=document_b,
        parent_id=None,
        content=content,
        anchor_json=anchor,
        source_content_hash=source_b,
        chunk_content_hash=content_hash,
    )
    rebound_work = replace(
        work(),
        document_id=str(document_b),
        source_content_hash=source_b,
        parser_version=PARSER_VERSION,
    )

    with pytest.raises(ValueError, match="provenance"):
        index._revision_rows(
            ATHENA_PHYSICAL_COLLECTION,
            rebound_work,
            (rebound,),
            EmbeddingArtifact(
                ATHENA_EMBEDDING_MODEL,
                1536,
                ((0.1,) * 1536,),
                (str(rebound.chunk_id),),
            ),
            "athena-v1",
        )


@pytest.mark.asyncio
async def test_rebuild_persists_exact_ownership_receipt_before_provider_create() -> None:
    """A provider create without a prior durable receipt must never confer later drop authority."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    memory.require_fresh_receipt_before_create = True

    receipt = await index.rebuild((ready_record(),))

    ownership = memory.coordinator.owned[receipt.physical_collection]
    assert ownership.status == "active"
    assert ownership.predecessor_collection == ATHENA_PHYSICAL_COLLECTION
    assert len(ownership.operation_id) == 32


@pytest.mark.asyncio
async def test_owned_alias_switch_after_crash_repairs_lineage_without_dropping_legacy() -> None:
    """Only a pre-create receipt may authorize crash recovery of an uncertain alias switch."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    fresh = ATHENA_PHYSICAL_COLLECTION + "_" + "c" * 12
    operation_id = "d" * 32
    build = await memory.coordinator.reserve_build(
        fresh,
        ATHENA_PHYSICAL_COLLECTION,
        operation_id,
    )
    await memory.create_collection(fresh, index._schema())
    memory.indexes[fresh] = set(EXPECTED_INDEXES)
    memory.loaded.add(fresh)
    memory.aliases[ATHENA_ALIAS] = fresh

    receipt = await index_for(memory).ensure_target()

    assert receipt.physical_collection == fresh
    assert memory.coordinator.physical == fresh
    assert memory.coordinator.owned[fresh] == replace(build, status="active")
    assert ATHENA_PHYSICAL_COLLECTION in memory.collections
    assert ATHENA_PHYSICAL_COLLECTION not in memory.coordinator.cleanup


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
        embeddings=EmbeddingArtifact(
            ATHENA_EMBEDDING_MODEL,
            1536,
            ((0.1,) * 1536,),
            (str(chunk().chunk_id),),
        ),
        index_version="athena-v1",
    )

    async def fail_parity(*args: object) -> None:
        raise RuntimeError("injected parity failure")

    monkeypatch.setattr(index, "_require_revision_parity", fail_parity)
    with pytest.raises(RebuildRejected) as rejected:
        await index.rebuild((record,))
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION
    assert rejected.value.cleanup_facts[0].startswith("dropped_owned_physical:")

    monkeypatch.undo()
    memory.coordinator.cancel_activation = True
    with pytest.raises(asyncio.CancelledError):
        await index.rebuild((record,))
    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_reader")] == set(
        READER_TARGET_PRIVILEGES
    )
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_writer")] == set(WRITER_PRIVILEGES)


@pytest.mark.asyncio
async def test_rebuild_returns_receipt_on_postcommit_lease_exit_cancellation() -> None:
    """A committed alias/generation must not be reported as cancelled without its receipt."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    memory.coordinator.cancel_release_after_body = True

    receipt = await index.rebuild((ready_record(),))

    assert memory.aliases[ATHENA_ALIAS] == receipt.physical_collection
    assert memory.coordinator.physical == receipt.physical_collection
    assert memory.coordinator.owned[receipt.physical_collection].status == "active"


@pytest.mark.asyncio
async def test_rebuild_activation_failure_restores_old_alias_and_exact_grants() -> None:
    """A failure after old retirement but before durable activation must roll back."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    memory.coordinator.activate_error_before_success = True

    with pytest.raises(RebuildRejected):
        await index.rebuild((ready_record(),))

    assert memory.aliases[ATHENA_ALIAS] == ATHENA_PHYSICAL_COLLECTION
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_reader")] == set(
        READER_TARGET_PRIVILEGES
    )
    assert memory.grants[(ATHENA_PHYSICAL_COLLECTION, "tap_writer")] == set(WRITER_PRIVILEGES)
    assert f"revoke:tap_reader:{ATHENA_PHYSICAL_COLLECTION}" in memory.events
    assert f"revoke:tap_writer:{ATHENA_PHYSICAL_COLLECTION}" in memory.events


@pytest.mark.asyncio
async def test_rebuild_resolves_activation_success_then_error_from_durable_lineage() -> None:
    """A lost activation response must use alias/state/ownership truth, not rollback a commit."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    memory.coordinator.activate_error_after_success = True

    receipt = await index.rebuild((ready_record(),))

    assert memory.aliases[ATHENA_ALIAS] == receipt.physical_collection
    assert memory.coordinator.physical == receipt.physical_collection
    assert memory.coordinator.owned[receipt.physical_collection].status == "active"


@pytest.mark.asyncio
async def test_rebuild_rollback_failure_reports_exact_owned_physical_cleanup_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup failure must preserve the pre-create receipt's exact physical identity."""
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()

    async def fail_parity(*args: object) -> None:
        raise RuntimeError("injected publication failure")

    async def fail_cleanup(*args: object) -> tuple[str, ...]:
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(index, "_require_revision_parity", fail_parity)
    monkeypatch.setattr(index, "_rebuild_rollback", fail_cleanup)

    with pytest.raises(RebuildRejected) as rejected:
        await index.rebuild((ready_record(),))

    [owned_physical] = memory.coordinator.owned
    assert rejected.value.cleanup_facts == (f"cleanup_unknown:{owned_physical}",)


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
        coordinator=CloseOnly("coordinator"),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="injected close failure"):
        await index.close()
    assert closed == ["reader", "writer", "provisioner", "coordinator"]
