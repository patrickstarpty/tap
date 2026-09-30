"""Real MySQL persistence with explicitly fake model and index ports."""

import asyncio
import json
from dataclasses import asdict
from datetime import datetime

import pytest
from sqlalchemy import func, select, update

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql import graph_extraction_job
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore, MysqlGraphReadyProjection
from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.adapters.managed_chunk_search import collapse_parent_groups
from tap.modules.knowledge.adapters.mysql_documents import (
    MysqlDocumentRepository,
    knowledge_document,
    knowledge_document_revision,
    knowledge_ingestion_job,
)
from tap.modules.knowledge.adapters.mysql_managed_chunks import (
    ChunkConflict,
    MysqlManagedChunks,
    managed_documents,
)
from tap.modules.knowledge.domain.documents import (
    DocumentId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
)
from tap.modules.knowledge.domain.managed_chunks import ChunkSettings, split_text
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.domain.sources import (
    SourceCommand,
    SourceCommandConflict,
    SourceCommandReplay,
    projection_digest,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    EmbeddingArtifact,
    IndexReceipt,
    ReserveUpload,
)
from tap.modules.knowledge.ports.models import SearchHit
from tap.platform.db.schema import outbox
from tap.platform.db.session import create_engine_and_session_factory
from tests.owned_mysql import owned_project_database_url


class Artifacts:
    def __init__(self):
        self.values = {}

    async def read_original(self, locator):
        return self.values[locator]

    async def write_normalized(self, revision_id, artifact):
        locator = ArtifactLocator("artifact:" + revision_id + ":normalized")
        from tap.modules.knowledge.adapters.artifact_codecs import (
            decode_normalized_artifact,
            encode_normalized_artifact,
        )

        self.values[locator] = decode_normalized_artifact(
            encode_normalized_artifact(revision_id, artifact), expected_revision=revision_id
        )
        return locator

    async def read_normalized(self, locator):
        return self.values[locator]

    async def write_chunks(self, revision_id, chunks):
        locator = ArtifactLocator("artifact:" + revision_id + ":chunks")
        from tap.modules.knowledge.adapters.artifact_codecs import (
            decode_chunks_artifact,
            encode_chunks_artifact,
        )

        self.values[locator] = decode_chunks_artifact(
            encode_chunks_artifact(revision_id, chunks), expected_revision=revision_id
        )
        return locator

    async def read_chunks(self, locator):
        return self.values[locator]

    async def write_embeddings(self, revision_id, artifact, **kwargs):
        locator = ArtifactLocator("artifact:" + revision_id + ":embedding")
        self.values[locator] = artifact
        return locator


class Embeddings:
    def __init__(self):
        self.texts = ()
        self.entered = None
        self.release = None

    async def embed_documents(self, texts, *, model_alias, chunk_ids):
        if self.entered is not None:
            self.entered.set()
            await self.release.wait()
        self.texts = texts
        return EmbeddingArtifact(model_alias, 2, tuple((1.0, 0.0) for _ in texts), chunk_ids)


class Index:
    def __init__(self):
        self.fail = False
        self.chunks = ()
        self.work = None
        # Live projection rows per revision of the (single) test document.
        self.revisions: dict[str, set[str]] = {}
        self.fenced: set[str] = set()
        self.purges: list[tuple[str, str | None]] = []

    async def upsert_revision(self, work, chunks, embeddings, *, index_version):
        if self.fail:
            raise RuntimeError("injected index failure")
        if work.revision_id in self.fenced:
            raise RuntimeError("fenced revision")
        self.chunks = chunks
        self.work = work
        self.revisions[work.revision_id] = {str(chunk.chunk_id) for chunk in chunks}
        return IndexReceipt(
            work.revision_id,
            index_version,
            len(chunks),
            projection_digest(work.revision_id, "doc-schema-v2", index_version, work.manifest),
            "doc-schema-v2",
        )

    async def purge_document(self, document_id, *, keep_revision_id, fence_revision_ids=()):
        self.purges.append((document_id, keep_revision_id))
        self.revisions = {
            revision: rows
            for revision, rows in self.revisions.items()
            if revision == keep_revision_id
        }
        self.fenced.update(fence_revision_ids)


@pytest.mark.parametrize("mode", ["general", "parent_child"])
def test_mutations_persist_reindex_and_fence_old_revisions(owned_project_mysql, mode, caplog):
    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions,
            scope=VALIDATION_SCOPE,
            audit_factory=create_project_audit,
            ready_projection=MysqlGraphReadyProjection(
                MysqlGraphJobStore(sessions), model_alias="test"
            ),
        )
        artifacts, embeddings, index = Artifacts(), Embeddings(), Index()
        try:
            content = "Original alpha paragraph.\n\nSecond beta paragraph."
            source_hash = canonical_sha256(content.encode())
            artifacts.values["artifact:original"] = content.encode()
            reserved = await repository.reserve_upload(
                ReserveUpload(
                    filename="example.md",
                    media_type="text/markdown",
                    source_content_hash=source_hash,
                    size=len(content),
                    now=datetime.now(),
                    staging_key="stage:test",
                    chunk_settings=asdict(ChunkSettings(mode=mode, childMaxLength=8)),
                )
            )
            original_receipt = await repository.activate_upload(
                reserved, ArtifactLocator("artifact:original")
            )
            document_id = reserved.document_id
            async with sessions() as session:
                document = (
                    (
                        await session.execute(
                            select(knowledge_document).where(
                                knowledge_document.c.document_id == document_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                revision_id = document["current_revision_id"]
            normalized = NormalizedArtifact(
                "example.md",
                MediaType.MARKDOWN,
                source_hash,
                (NormalizedBlock("b1", "paragraph", content, (), None, 0, 0, len(content)),),
                DocumentId(document_id),
                RevisionId(revision_id),
            )
            normalized_locator = await artifacts.write_normalized(revision_id, normalized)
            chunks_locator = await artifacts.write_chunks(
                revision_id, StructuralChunker().chunk(normalized)
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_document_revision)
                    .where(knowledge_document_revision.c.revision_id == revision_id)
                    .values(
                        normalized_blob_locator=normalized_locator,
                        chunks_blob_locator=chunks_locator,
                    )
                )
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == document_id)
                    .values(status="ready", stage="ready", chunk_count=1)
                )

            def manager():
                return MysqlManagedChunks(
                    sessions=sessions,
                    repository=repository,
                    scope=VALIDATION_SCOPE,
                    artifacts=artifacts,
                    embeddings=embeddings,
                    index=index,
                    embedding_model_alias="test",
                    embedding_dimension=2,
                    index_version="test-v1",
                )

            # Opposite source modes roll back the entire new reservation.
            with pytest.raises(SourceCommandConflict):
                await repository.reserve_upload(
                    ReserveUpload(
                        filename="conflict.md",
                        media_type="text/markdown",
                        source_content_hash="sha256:" + "d" * 64,
                        size=10,
                        now=datetime.now(),
                        staging_key="stage:conflict",
                        source_id=document["source_id"],
                        chunk_settings=asdict(
                            ChunkSettings(
                                mode="general" if mode == "parent_child" else "parent_child"
                            )
                        ),
                    )
                )
            async with sessions() as session:
                assert (
                    await session.scalar(select(func.count()).select_from(knowledge_document)) == 1
                )
            # A failed initial parse before a ready item cannot starve the ready queue.
            large_content = "x" * 40000
            large_hash = canonical_sha256(large_content.encode())
            failing = await repository.reserve_upload(
                ReserveUpload(
                    filename="large.md",
                    media_type="text/markdown",
                    source_content_hash=large_hash,
                    size=40000,
                    now=datetime.now(),
                    staging_key="stage:large",
                    chunk_settings=asdict(
                        ChunkSettings(mode="parent_child", parentMode="full_doc")
                    ),
                )
            )
            await repository.activate_upload(failing, ArtifactLocator("artifact:large-original"))
            large_normalized = NormalizedArtifact(
                "large.md",
                MediaType.MARKDOWN,
                large_hash,
                (
                    NormalizedBlock(
                        "large", "paragraph", large_content, (), None, 0, 0, len(large_content)
                    ),
                ),
                DocumentId(failing.document_id),
                RevisionId(failing.revision_id),
            )
            large_locator = await artifacts.write_normalized(failing.revision_id, large_normalized)
            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == failing.document_id)
                    .values(status="failed")
                )
                await session.execute(
                    update(knowledge_document_revision)
                    .where(knowledge_document_revision.c.revision_id == failing.revision_id)
                    .values(normalized_blob_locator=large_locator)
                )
            assert await repository.load_ready_revisions((document_id,)) == ()
            detail = await repository.get_document(DocumentId(document_id))
            assert detail.status.value == "processing"
            assert (
                await repository.source_detail(document["source_id"], None, 20)
            ).ready_count == 0
            with pytest.raises(ChunkConflict):
                await manager().create(document_id, "Not before ingestion")
            preview = await manager().preview(
                document_id, ChunkSettings(mode=mode, childMaxLength=8)
            )
            assert await manager().run_pending() == 1
            async with sessions() as session, session.begin():
                state = (
                    (
                        await session.execute(
                            select(managed_documents).where(
                                managed_documents.c.document_id == failing.document_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert state["index_status"] == "ingestion_error"
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == failing.document_id)
                    .values(status="ready")
                )
            # Queue failure reconciliation must not lock document rows held by retry.
            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == failing.document_id)
                    .values(status="failed")
                )
                await session.execute(
                    update(managed_documents)
                    .where(managed_documents.c.document_id == failing.document_id)
                    .values(index_status="waiting_ingestion")
                )
                original_job = (
                    (
                        await session.execute(
                            select(knowledge_ingestion_job).where(
                                knowledge_ingestion_job.c.revision_id == failing.revision_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                checkpoint = original_job["stage_results_json"][0]["completedAt"]
                await session.execute(
                    update(knowledge_ingestion_job)
                    .where(knowledge_ingestion_job.c.job_id == original_job["job_id"])
                    .values(status="failed", stage="parsing")
                )
            async with sessions() as retry_lock, retry_lock.begin():
                await retry_lock.execute(
                    select(knowledge_document)
                    .where(knowledge_document.c.document_id == failing.document_id)
                    .with_for_update()
                )
                assert await asyncio.wait_for(manager().run_pending(), timeout=2) == 0
            retried = await repository.retry_failed(DocumentId(failing.document_id), datetime.now())
            assert retried.job_id == original_job["job_id"]
            async with sessions() as session, session.begin():
                original_job_after = (
                    (
                        await session.execute(
                            select(knowledge_ingestion_job).where(
                                knowledge_ingestion_job.c.job_id == original_job["job_id"]
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert original_job_after["stage_results_json"][0]["completedAt"] == checkpoint
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == failing.document_id)
                    .values(status="ready")
                )
            # Oversized full-document processing records an error, not a dead worker.
            assert await manager().run_pending() == 1
            async with sessions() as session:
                failed_state = (
                    (
                        await session.execute(
                            select(managed_documents).where(
                                managed_documents.c.document_id == failing.document_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert failed_state["index_status"] == "error"
            assert (await manager().list_chunks(document_id))["items"][0]["content"] == preview[
                "items"
            ][0]["content"]
            managed_revision = (await repository.get_document(document_id)).revision_id
            for _ in range(2):
                async with sessions() as session:
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(outbox)
                            .where(
                                outbox.c.aggregate_id == managed_revision,
                                outbox.c.message_type == "knowledge.document-revision.ready",
                            )
                        )
                        == 1
                    )
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(graph_extraction_job)
                            .where(
                                graph_extraction_job.c.revision_id == managed_revision,
                            )
                        )
                        == 1
                    )
                await manager().process_document(document_id)
            duplicate_request = ReserveUpload(
                filename="duplicate.md",
                media_type="text/markdown",
                source_content_hash=source_hash,
                size=len(content),
                now=datetime.now(),
                staging_key="stage:duplicate",
                command=SourceCommand(
                    "duplicate-upload", "document.upload", "duplicate-correlation"
                ),
            )
            async with sessions() as session:
                jobs_before = await session.scalar(
                    select(func.count()).select_from(knowledge_ingestion_job)
                )
            duplicate_receipt = await repository.reserve_upload(duplicate_request)
            assert duplicate_receipt.document.job_id == original_receipt.job_id
            assert (await repository.get_document(document_id)).job_id != original_receipt.job_id
            with pytest.raises(SourceCommandReplay) as replay:
                await repository.reserve_upload(duplicate_request)
            assert replay.value.result.body["jobId"] == original_receipt.job_id
            async with sessions() as session:
                assert (
                    await session.scalar(select(func.count()).select_from(knowledge_ingestion_job))
                    == jobs_before
                )
            async with sessions() as locked, locked.begin():
                await manager()._document(locked, document_id, lock=True)
                chunks = await asyncio.wait_for(manager().list_chunks(document_id), timeout=2)
            first = chunks["items"][0]
            assert json.loads(index.chunks[0].anchor_json)["headingPath"] == []
            if mode == "parent_child":
                hits = tuple(
                    SearchHit(
                        SourceFamily.DOC,
                        str(chunk.chunk_id),
                        str(chunk.logical_chunk_id),
                        None,
                        chunk.content,
                        SourceRevisionRef(
                            document["source_id"],
                            "doc",
                            RevisionKind.BLOB_VERSION,
                            index.work.revision_id,
                            index.work.source_content_hash,
                            DocumentAnchor(heading_path=("untrusted heading",)),
                        ),
                        chunk.chunk_content_hash,
                        ContentRole.SOURCE,
                        IndexRevision("index", "doc-schema-v1", "corpus"),
                        "test",
                        float(i + 1),
                        i + 1,
                        parent_id="untrusted-provider-parent",
                    )
                    for i, chunk in enumerate(index.chunks)
                )
                groups = await manager().parent_groups(hits)
                unique = collapse_parent_groups(hits, groups)
                assert len(unique) == 1
                assert unique[0].chunk_id == hits[-1].chunk_id
                assert unique[0].local_rank == 1
                child = first["children"][0]
                await manager().change(
                    document_id, child["chunkId"], child["version"], content="child query"
                )
                assert (await manager().list_chunks(document_id))["items"][0]["content"] == first[
                    "content"
                ]
                await manager().run_pending()
                assert embeddings.texts[0] == "child query"
                assert collapse_parent_groups(hits, await manager().parent_groups(hits)) == ()
                assert index.chunks[0].content == first["content"]
            edited = await manager().change(
                document_id, first["chunkId"], 1, content="Edited gamma content"
            )
            assert edited["indexStatus"] == "pending"
            await manager().run_pending()
            assert index.chunks[0].content == "Edited gamma content"
            assert (await manager().original(document_id))[2] == content.encode()
            assert index.chunks[0].source_content_hash != source_hash
            assert json.loads(index.chunks[0].anchor_json)["headingPath"] == []
            assert edited["edited"] is True
            assert index.work.parser_version == "managed-chunks-v1"
            assert (
                artifacts.values[index.work.normalized_locator]
                .parse_inventory[0]
                .original_alignment_reason
                == "managed-content-not-original-excerpt"
            )
            assert artifacts.values[normalized_locator].blocks[0].text == normalized.blocks[0].text
            assert (await manager().list_chunks(document_id, q="gamma"))["total"] == 1
            with pytest.raises(ChunkConflict):
                await manager().change(document_id, first["chunkId"], 1, content="Stale write")
            await manager().change(document_id, first["chunkId"], edited["version"], enabled=False)
            assert await repository.load_ready_revisions((document_id,)) == ()
            with pytest.raises(ChunkConflict):
                await manager().change(document_id, first["chunkId"], 3, content="Disabled edit")
            index.fail = True
            failed = await manager().change(document_id, first["chunkId"], 3, enabled=True)
            assert failed["indexStatus"] == "pending"
            await manager().run_pending()
            assert (await manager().list_chunks(document_id))["items"][0]["indexStatus"] == "error"
            assert await repository.load_ready_revisions((document_id,)) == ()
            index.fail = False
            await manager().batch(
                document_id, "retry", [{"chunkId": first["chunkId"], "version": 4}]
            )
            await manager().run_pending()
            assert (await manager().list_chunks(document_id))["items"][0]["indexStatus"] == "ready"
            assert len(await repository.load_ready_revisions((document_id,))) == 1
            settings = await manager().settings(document_id)
            with pytest.raises(ChunkConflict):
                await manager().configure(
                    document_id,
                    ChunkSettings(mode="general" if mode == "parent_child" else "parent_child"),
                    settings["version"],
                    True,
                )
            added = await manager().import_contents(
                document_id, ["One import", "Two import"], key="import-1"
            )
            replayed = await manager().import_contents(
                document_id, ["One import", "Two import"], key="import-1"
            )
            assert added == replayed
            assert (await manager().list_chunks(document_id))["total"] == 3
            assert (await manager().list_chunks(document_id, q="Two"))["total"] == 1
            with pytest.raises(ChunkConflict):
                await manager().import_contents(document_id, ["Different"], key="import-1")
            if mode == "parent_child":
                parent = (await manager().list_chunks(document_id))["items"][0]
                replacement = await manager().change(
                    document_id,
                    parent["chunkId"],
                    parent["version"],
                    content="regenerated children text",
                    regenerate_children=True,
                )
                assert [c["content"] for c in replacement["children"]] == split_text(
                    "regenerated children text",
                    ChunkSettings(mode="parent_child", childMaxLength=8),
                    child=True,
                )
            all_chunks = (await manager().list_chunks(document_id))["items"]
            await manager().batch(
                document_id,
                "delete",
                [dict(chunkId=c["chunkId"], version=c["version"]) for c in all_chunks],
            )
            assert (await manager().list_chunks(document_id))["total"] == 0
            assert await repository.load_ready_revisions((document_id,)) == ()

            # A save arriving during embedding must not invert source/document locks.
            concurrent = await manager().create(document_id, "Concurrent first version")
            embeddings.entered, embeddings.release = asyncio.Event(), asyncio.Event()
            processing = asyncio.create_task(manager().process_document(document_id))
            await asyncio.wait_for(embeddings.entered.wait(), timeout=5)
            saving = asyncio.create_task(
                manager().change(
                    document_id, concurrent["chunkId"], 1, content="Concurrent second version"
                )
            )
            # Give the competing transaction an opportunity to acquire its first lock.
            await asyncio.sleep(0.2)
            assert (await asyncio.wait_for(manager().list_chunks(document_id), timeout=2))["items"]
            embeddings.release.set()
            await asyncio.wait_for(asyncio.gather(processing, saving), timeout=10)
            embeddings.entered = None
            assert (await manager().list_chunks(document_id))["items"][0][
                "indexStatus"
            ] == "pending"
            await manager().run_pending()
            assert (await manager().list_chunks(document_id))["items"][0]["indexStatus"] == "ready"
            caplog.clear()
            # Source deletion during indexing must settle without resurrecting visibility.
            await manager().change(
                document_id, concurrent["chunkId"], 2, content="Delete in flight"
            )
            embeddings.entered, embeddings.release = asyncio.Event(), asyncio.Event()
            processing = asyncio.create_task(manager().process_document(document_id))
            await asyncio.wait_for(embeddings.entered.wait(), timeout=5)
            deleting = asyncio.create_task(repository.delete_source(document["source_id"]))
            await asyncio.sleep(0.2)
            embeddings.release.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(processing, deleting, return_exceptions=True), timeout=15
            )
            assert all(value is None for value in outcomes), [
                type(value).__name__ for value in outcomes
            ]
            assert "knowledge.managed_index_failed" not in caplog.text
            assert await repository.get_document(document_id) is None
            assert await repository.load_ready_revisions((document_id,)) == ()
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(knowledge_document.c.status).where(
                            knowledge_document.c.document_id == document_id
                        )
                    )
                    == "deleting"
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_source_delete_survives_a_managed_revision_published_after_its_snapshot(
    owned_project_mysql,
):
    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        artifacts, embeddings, index = Artifacts(), Embeddings(), Index()
        try:
            content = "Original alpha paragraph.\n\nSecond beta paragraph."
            source_hash = canonical_sha256(content.encode())
            artifacts.values["artifact:original"] = content.encode()
            reserved = await repository.reserve_upload(
                ReserveUpload(
                    filename="race.md",
                    media_type="text/markdown",
                    source_content_hash=source_hash,
                    size=len(content),
                    now=datetime.now(),
                    staging_key="stage:race",
                    chunk_settings=asdict(ChunkSettings()),
                )
            )
            await repository.activate_upload(reserved, ArtifactLocator("artifact:original"))
            document_id = reserved.document_id
            async with sessions() as session:
                document = (
                    (
                        await session.execute(
                            select(knowledge_document).where(
                                knowledge_document.c.document_id == document_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            original_revision = document["current_revision_id"]
            normalized = NormalizedArtifact(
                "race.md",
                MediaType.MARKDOWN,
                source_hash,
                (NormalizedBlock("b1", "paragraph", content, (), None, 0, 0, len(content)),),
                DocumentId(document_id),
                RevisionId(original_revision),
            )
            normalized_locator = await artifacts.write_normalized(original_revision, normalized)
            chunks_locator = await artifacts.write_chunks(
                original_revision, StructuralChunker().chunk(normalized)
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_document_revision)
                    .where(knowledge_document_revision.c.revision_id == original_revision)
                    .values(
                        normalized_blob_locator=normalized_locator,
                        chunks_blob_locator=chunks_locator,
                    )
                )
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == document_id)
                    .values(status="ready", stage="ready", chunk_count=1)
                )
            manager = MysqlManagedChunks(
                sessions=sessions,
                repository=repository,
                scope=VALIDATION_SCOPE,
                artifacts=artifacts,
                embeddings=embeddings,
                index=index,
                embedding_model_alias="test",
                embedding_dimension=2,
                index_version="test-v1",
            )

            # The delete's first consistent read fixes its snapshot before the
            # managed worker publishes a new current revision.
            snapshot_taken, publish_done = asyncio.Event(), asyncio.Event()
            original_source_command = repository._source_command

            async def gated_source_command(*args, **kwargs):
                result = await original_source_command(*args, **kwargs)
                snapshot_taken.set()
                await publish_done.wait()
                return result

            repository._source_command = gated_source_command
            deleting = asyncio.create_task(
                repository.delete_source(
                    document["source_id"],
                    command=SourceCommand("delete-race", "source.delete", "correlation-race"),
                )
            )
            await asyncio.wait_for(snapshot_taken.wait(), timeout=5)
            await manager.run_pending()
            async with sessions() as session:
                published = await session.scalar(
                    select(knowledge_document.c.current_revision_id).where(
                        knowledge_document.c.document_id == document_id
                    )
                )
            assert published != original_revision
            publish_done.set()
            await asyncio.wait_for(deleting, timeout=15)

            assert await repository.get_document(document_id) is None
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(knowledge_document.c.status).where(
                            knowledge_document.c.document_id == document_id
                        )
                    )
                    == "deleting"
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_republish_purges_superseded_revision_rows_and_keeps_current(owned_project_mysql):
    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        artifacts, embeddings, index = Artifacts(), Embeddings(), Index()
        try:
            content = "Original alpha paragraph.\n\nSecond beta paragraph."
            source_hash = canonical_sha256(content.encode())
            artifacts.values["artifact:original"] = content.encode()
            reserved = await repository.reserve_upload(
                ReserveUpload(
                    filename="supersede.md",
                    media_type="text/markdown",
                    source_content_hash=source_hash,
                    size=len(content),
                    now=datetime.now(),
                    staging_key="stage:supersede",
                    chunk_settings=asdict(ChunkSettings()),
                )
            )
            await repository.activate_upload(reserved, ArtifactLocator("artifact:original"))
            document_id = reserved.document_id
            async with sessions() as session:
                uploaded = await session.scalar(
                    select(knowledge_document.c.current_revision_id).where(
                        knowledge_document.c.document_id == document_id
                    )
                )
            normalized = NormalizedArtifact(
                "supersede.md",
                MediaType.MARKDOWN,
                source_hash,
                (NormalizedBlock("b1", "paragraph", content, (), None, 0, 0, len(content)),),
                DocumentId(document_id),
                RevisionId(uploaded),
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_document_revision)
                    .where(knowledge_document_revision.c.revision_id == uploaded)
                    .values(
                        normalized_blob_locator=await artifacts.write_normalized(
                            uploaded, normalized
                        ),
                        chunks_blob_locator=await artifacts.write_chunks(
                            uploaded, StructuralChunker().chunk(normalized)
                        ),
                    )
                )
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == document_id)
                    .values(status="ready", stage="ready", chunk_count=1)
                )
            # The original ingestion published the uploaded revision's rows.
            index.revisions[uploaded] = {"uploaded-chunk"}
            manager = MysqlManagedChunks(
                sessions=sessions,
                repository=repository,
                scope=VALIDATION_SCOPE,
                artifacts=artifacts,
                embeddings=embeddings,
                index=index,
                embedding_model_alias="test",
                embedding_dimension=2,
                index_version="test-v1",
            )

            async def current_revision():
                async with sessions() as session:
                    return await session.scalar(
                        select(knowledge_document.c.current_revision_id).where(
                            knowledge_document.c.document_id == document_id
                        )
                    )

            await manager.run_pending()
            first = await current_revision()
            assert first != uploaded
            assert set(index.revisions) == {first}
            assert index.fenced == {uploaded}

            chunk = (await manager.list_chunks(document_id))["items"][0]
            await manager.change(
                document_id, chunk["chunkId"], chunk["version"], content="Edited alpha."
            )
            await manager.run_pending()
            second = await current_revision()
            assert second not in {uploaded, first}
            assert set(index.revisions) == {second}
            assert index.fenced == {uploaded, first}

            # Completed cleanup is durable: another sweep has nothing to purge.
            purges = len(index.purges)
            await manager.run_pending()
            assert len(index.purges) == purges
        finally:
            await engine.dispose()

    asyncio.run(run())
