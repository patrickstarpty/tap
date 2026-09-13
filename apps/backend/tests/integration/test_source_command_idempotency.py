"""Source request replay against explicitly owned MySQL, never caller defaults."""

import asyncio
import hashlib
import os
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    MysqlDocumentRepository,
    knowledge_source_command,
)
from tap.modules.knowledge.domain.sources import (
    SourceCommand,
    SourceCommandConflict,
    SourceCommandReplay,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.asyncio


async def test_projection_cutover_retains_predecessor_and_guards_rollback(repository):
    from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator
    from tap.modules.knowledge.ports.errors import IndexUnavailable

    _, engine = repository
    coordinator = MysqlProjectionCoordinator(
        engine, scope=VALIDATION_SCOPE, authority_namespace="task6a-" + uuid4().hex
    )
    async with coordinator.mutation("kb_doc_tapper_demo_active") as lease:
        old = "kb_doc_v1_tapper_demo"
        fresh = "kb_doc_v2_tapper_demo_123456789abc"
        await lease.initialize(old)
        build = await lease.reserve_build(fresh, old, uuid4().hex)
        await lease.activate_build(build, retain_predecessor=True)
        retained = await lease.ownership(old)
        assert retained is not None and retained.status == "retained"
        assert await lease.owned_cleanup(64) == ()
        assert not await lease.verify_cleanup(retained)
        with pytest.raises(IndexUnavailable):
            await lease.reactivate_retained(retained, expected_current="kb_doc_v2_wrong")
        assert (await lease.state())[1] == fresh
        await lease.reactivate_retained(retained, expected_current=fresh)
        assert (await lease.state())[1] == old
        assert (await lease.ownership(fresh)).status == "retained"
        assert await lease.owned_cleanup(64) == ()
        with pytest.raises(IndexUnavailable):
            await lease.reactivate_retained(retained, expected_current=fresh)


@pytest_asyncio.fixture
async def operator_repository(owned_project_mysql):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        yield (
            MysqlDocumentRepository(
                sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
            ),
            engine,
        )
    finally:
        await engine.dispose()


async def test_operator_snapshot_carries_active_source_owner_and_rejects_tombstone(
    operator_repository,
):
    from sqlalchemy import insert, update

    from tap.modules.knowledge.adapters import mysql_documents as ledger
    from tap.modules.knowledge.adapters.milvus_documents import (
        MilvusDocumentIndex,
        ReadyRevisionArtifacts,
        TapperMilvusConfig,
    )
    from tap.modules.knowledge.adapters.mysql_operations import MysqlOperationRepository
    from tap.modules.knowledge.domain.documents import (
        ChunkDraft,
        DocumentId,
        RevisionId,
        canonical_sha256,
        chunk_id_for,
        logical_chunk_id_for,
    )
    from tap.modules.knowledge.ports.documents import EmbeddingArtifact
    from tap.platform.db.project_scope import scope_values

    repo, engine = operator_repository
    request = upload()
    reserved = await repo.reserve_upload(request)
    accepted = await repo.activate_upload(reserved, ArtifactLocator("blob:owned-snapshot"))
    source_id = (await repo.source_command_result(request.command.key)).body["source"]["sourceId"]
    anchor = '{"type":"document"}'
    content = "Owned receipt regression."
    content_hash = canonical_sha256(content.encode())
    chunk = ChunkDraft(
        chunk_id_for(RevisionId(accepted.revision_id), anchor, content_hash),
        logical_chunk_id_for(DocumentId(accepted.document_id), anchor),
        DocumentId(accepted.document_id),
        None,
        content,
        anchor,
        request.source_content_hash,
        content_hash,
    )
    async with engine.begin() as connection:
        await connection.execute(
            update(ledger.knowledge_document)
            .where(ledger.knowledge_document.c.document_id == accepted.document_id)
            .values(status="ready", stage="ready")
        )
        await connection.execute(
            update(ledger.knowledge_document_revision)
            .where(ledger.knowledge_document_revision.c.revision_id == accepted.revision_id)
            .values(
                chunks_blob_locator="chunks:owned",
                embeddings_blob_locator="embeddings:owned",
                chunk_manifest_digest="sha256:" + "b" * 64,
                projection_digest="sha256:" + "c" * 64,
            )
        )
        await connection.execute(
            update(ledger.knowledge_ingestion_job)
            .where(ledger.knowledge_ingestion_job.c.job_id == accepted.job_id)
            .values(stage="ready", status="completed")
        )
        await connection.execute(
            insert(ledger.knowledge_chunk_manifest).values(
                **scope_values(VALIDATION_SCOPE),
                chunk_id=str(chunk.chunk_id),
                logical_chunk_id=str(chunk.logical_chunk_id),
                revision_id=accepted.revision_id,
                ordinal=0,
                root_id=accepted.document_id,
                parent_id=None,
                anchor_json={"type": "document"},
                chunk_content_hash=content_hash,
                embedding_model_version="tapper-embedding",
                index_version="tapper-index-v1",
                created_at=request.now,
            )
        )
    operations = MysqlOperationRepository(engine, scope=VALIDATION_SCOPE)
    records = await operations.ready_work(500)
    work = next(item for item in records if item.document_id == accepted.document_id)
    assert work.chunk_manifest_digest == "sha256:" + "b" * 64
    assert work.projection_digest == "sha256:" + "c" * 64
    # Pure preflight: no provider is composed or called. SQL receipts must survive
    # the repository boundary and reject otherwise matching loaded artifacts.
    peers = [
        MilvusDocumentIndex(
            config=TapperMilvusConfig.for_schema(schema),
            provisioner=None,
            writer=None,
            reader=None,
            coordinator=None,
        )
        for schema in ("doc-schema-v1", "doc-schema-v2")
    ]
    record = ReadyRevisionArtifacts(
        work,
        (chunk,),
        EmbeddingArtifact(
            "tapper-embedding",
            1536,
            ((0.1,) * 1536,),
            (str(chunk.chunk_id),),
        ),
        "tapper-index-v1",
    )
    with pytest.raises(ValueError, match="SQL manifest digest"):
        peers[1]._validate_migration_artifacts((record,), peers[0])
    record = replace(record, work=replace(work, chunk_manifest_digest=None))
    with pytest.raises(ValueError, match="SQL projection receipt"):
        peers[1]._validate_migration_artifacts((record,), peers[0])
    peers[1]._validate_migration_artifacts(
        (
            replace(
                record,
                work=replace(
                    record.work,
                    projection_digest=None,
                ),
            ),
        ),
        peers[0],
    )
    assert (work.enterprise_id, work.project_id, work.source_id) == (
        "local",
        "tapper-demo",
        source_id,
    )
    async with engine.begin() as connection:
        await connection.execute(
            update(ledger.knowledge_source)
            .where(ledger.knowledge_source.c.source_id == source_id)
            .values(deleted_at=request.now)
        )
    with pytest.raises(ValueError, match="Source ownership"):
        await operations.ready_work(500)


@pytest_asyncio.fixture
async def repository():
    if not os.getenv("TAP_TASK6A_OWNED_PROJECT", "").startswith("tap-task6a-"):
        pytest.skip("requires the reviewed Task6A owned-service wrapper")
    url = os.environ["TAP_DATABASE_URL"]
    port = 36306 if os.getenv("TAP_TASK6A_CLI_PROOF") == "1" else 34306
    assert f"127.0.0.1:{port}/" in url
    engine, sessions = create_engine_and_session_factory(url)
    try:
        yield (
            MysqlDocumentRepository(
                sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
            ),
            engine,
        )
    finally:
        await engine.dispose()


def upload(key=None):
    nonce = uuid4().hex
    return ReserveUpload(
        filename="note.txt",
        media_type="text/plain",
        source_content_hash="sha256:" + hashlib.sha256(nonce.encode()).hexdigest(),
        size=32,
        now=datetime.now(timezone.utc),
        staging_key="staging:" + nonce,
        command=SourceCommand(key or nonce, "source.upload", "correlation-original"),
    )


async def test_pending_key_rejects_changed_request_without_creating_another_source(repository):
    repo, engine = repository
    request = upload()
    reserved = await repo.reserve_upload(request)
    with pytest.raises(SourceCommandConflict):
        await repo.reserve_upload(replace(request, filename="changed.txt"))
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(knowledge_source_command).where(
                        knowledge_source_command.c.idempotency_key == request.command.key
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1 and rows[0]["document_id"] == reserved.document_id
    assert rows[0]["status"] == "pending" and rows[0]["result"] is None


async def test_activation_commits_original_result_and_replays_after_source_tombstone(repository):
    repo, engine = repository
    request = upload()
    reserved = await repo.reserve_upload(request)
    await repo.activate_upload(reserved, ArtifactLocator("blob:" + reserved.document_id))
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    select(knowledge_source_command).where(
                        knowledge_source_command.c.idempotency_key == request.command.key
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "completed" and row["http_status"] == 202
    assert row["result"]["accepted"]["duplicate"] is False
    await repo.delete_source(row["source_id"])
    with pytest.raises(SourceCommandReplay) as replay:
        await repo.reserve_upload(
            replace(request, staging_key="staging:replayed", parser_version="parser-new-deployment")
        )
    assert replay.value.result.status == 202
    assert replay.value.result.body == row["result"]


async def test_concurrent_keys_share_content_but_retain_distinct_original_results(repository):
    repo, engine = repository
    request = upload()
    second = replace(request, command=SourceCommand(uuid4().hex, "source.upload", "second"))
    first_reservation, second_reservation = await asyncio.gather(
        repo.reserve_upload(request), repo.reserve_upload(second)
    )
    assert first_reservation.document_id == second_reservation.document_id
    await repo.activate_upload(
        first_reservation, ArtifactLocator("blob:" + first_reservation.document_id)
    )
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(knowledge_source_command).where(
                        knowledge_source_command.c.document_id == first_reservation.document_id
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 2
    assert {row["result"]["accepted"]["duplicate"] for row in rows} == {True, False}


async def test_opaque_command_keys_preserve_case_and_trailing_space_identity(repository):
    repo, engine = repository
    request = upload("Opaque-" + uuid4().hex)
    keys = (request.command.key, request.command.key + " ", request.command.key.lower())
    for key in keys:
        await repo.reserve_upload(
            replace(request, command=SourceCommand(key, "source.upload", "opaque-key-proof"))
        )
    async with engine.connect() as connection:
        stored = tuple(
            (
                await connection.execute(
                    select(knowledge_source_command.c.idempotency_key).where(
                        knowledge_source_command.c.idempotency_key.in_(keys)
                    )
                )
            ).scalars()
        )
    assert len(stored) == 3 and set(stored) == set(keys)


async def test_delete_command_replays_after_tombstone_and_conflicts_on_changed_target(repository):
    repo, engine = repository
    request = upload()
    reserved = await repo.reserve_upload(request)
    async with engine.connect() as connection:
        source_id = await connection.scalar(
            select(knowledge_source_command.c.source_id).where(
                knowledge_source_command.c.idempotency_key == request.command.key
            )
        )
    intent = SourceCommand(uuid4().hex, "source.delete", "delete-correlation")
    await repo.delete_source(source_id, command=intent)
    await repo.delete_source(source_id, command=intent)
    with pytest.raises(SourceCommandConflict):
        await repo.delete_source("src_" + "f" * 32, command=intent)
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    select(knowledge_source_command).where(
                        knowledge_source_command.c.idempotency_key == intent.key
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["http_status"] == 204 and row["result"] is None
    await repo.complete_upload_cleanup(reserved.reservation_id, reserved.owner_token)
    with pytest.raises(SourceCommandReplay) as replay:
        await repo.reserve_upload(request)
    assert replay.value.result.status == 409
    assert replay.value.result.body == {"code": "source-unavailable"}


async def test_retry_key_replays_original_accepted_attempt_after_state_changes(repository):
    from sqlalchemy import update

    from tap.modules.knowledge.adapters.mysql_documents import (
        knowledge_document,
        knowledge_ingestion_job,
    )

    repo, engine = repository
    request = upload()
    reservation = await repo.reserve_upload(request)
    record = await repo.activate_upload(
        reservation, ArtifactLocator("blob:" + reservation.document_id)
    )
    async with engine.begin() as connection:
        source_id = await connection.scalar(
            select(knowledge_document.c.source_id).where(
                knowledge_document.c.document_id == record.document_id
            )
        )
        await connection.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == record.document_id)
            .values(status="failed", error_code="parser-timeout", error_summary="Parsing failed")
        )
        await connection.execute(
            update(knowledge_ingestion_job)
            .where(knowledge_ingestion_job.c.job_id == record.job_id)
            .values(status="failed")
        )
    intent = SourceCommand(uuid4().hex, "source.retry", "retry-correlation")
    await repo.retry_failed(
        record.document_id,
        datetime.now(timezone.utc),
        command=intent,
        source_id=source_id,
        revision_id=record.revision_id,
        expected_attempt=1,
    )
    with pytest.raises(SourceCommandReplay) as replay:
        await repo.retry_failed(
            record.document_id,
            datetime.now(timezone.utc),
            command=intent,
            source_id=source_id,
            revision_id=record.revision_id,
            expected_attempt=1,
        )
    assert replay.value.result.status == 202
    assert replay.value.result.body["accepted"]["document"]["status"] == "queued"
    with pytest.raises(SourceCommandConflict):
        await repo.retry_failed(
            record.document_id,
            datetime.now(timezone.utc),
            command=intent,
            source_id=source_id,
            revision_id=record.revision_id,
            expected_attempt=2,
        )


async def test_source_list_and_detail_report_owned_documents_without_collapsing_cardinality(
    repository,
):
    repo, _ = repository
    first = upload()
    reservation = await repo.reserve_upload(first)
    await repo.activate_upload(reservation, ArtifactLocator("blob:" + reservation.document_id))
    result = await repo.source_command_result(first.command.key)
    source_id = result.body["source"]["sourceId"]
    second = replace(upload(), source_id=source_id)
    reservation2 = await repo.reserve_upload(second)
    await repo.activate_upload(reservation2, ArtifactLocator("blob:" + reservation2.document_id))
    detail = await repo.source_detail(source_id, None, 25)
    assert detail.source_id == source_id and detail.document_count == 2 and detail.ready_count == 0
    assert {item.document_id for item in detail.documents.items} == {
        reservation.document_id,
        reservation2.document_id,
    }
    assert all(item.source_id == source_id and item.attempt == 1 for item in detail.documents.items)
    page = await repo.list_sources(None, 1)
    assert len(page.items) == 1 and page.next_cursor is not None
    next_page = await repo.list_sources(page.next_cursor, 1)
    assert next_page.items[0].source_id != page.items[0].source_id


async def test_source_http_uses_real_command_pipeline_and_safe_conflict(repository):
    from types import SimpleNamespace

    from conftest import validation_http_services
    from httpx import ASGITransport, AsyncClient

    from tap.interfaces.http.app import create_app
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.knowledge.application.documents import DocumentService
    from tap.modules.knowledge.application.sources import SourceService
    from tap.modules.knowledge.ports.documents import StagedOriginal

    class Artifacts:
        async def stage_original(self, upload, *, max_bytes):
            data = b"".join([part async for part in upload.content])
            assert len(data) <= max_bytes
            return StagedOriginal(
                "stage:" + uuid4().hex,
                upload.filename,
                upload.media_type,
                len(data),
                "sha256:" + hashlib.sha256(data).hexdigest(),
            )

        async def commit_original(self, staged, revision_id):
            return ArtifactLocator("blob:" + revision_id)

        async def discard_staged(self, staged):
            pass

    repo, _ = repository
    documents = DocumentService(repository=repo, artifacts=Artifacts())
    service = KnowledgeHttpService(
        documents=documents,
        answers=SimpleNamespace(scope=VALIDATION_SCOPE),
        citations=SimpleNamespace(scope=VALIDATION_SCOPE),
        sources=SourceService(repo, documents),
    )
    app = create_app(
        validation_http_services(service), allowed_origins=frozenset({"http://127.0.0.1:15175"})
    )
    base = "/api/v1/projects/tapper-demo/knowledge/sources"
    key = uuid4().hex
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://127.0.0.1:15175", "Idempotency-Key": key},
    ) as client:
        payload = uuid4().hex.encode()
        first = await client.post(base, files={"upload": ("note.txt", payload, "text/plain")})
        assert first.status_code == 202, first.text
        assert (
            first.json()["accepted"]["document"]["sourceId"] == first.json()["source"]["sourceId"]
        )
        replay = await client.post(base, files={"upload": ("note.txt", payload, "text/plain")})
        assert replay.status_code == 202 and replay.json() == first.json()
        changed = await client.post(base, files={"upload": ("other.txt", payload, "text/plain")})
        assert changed.status_code == 409 and changed.json()["type"].endswith(
            "/idempotency-conflict"
        )
        detail = await client.get(base + "/" + first.json()["source"]["sourceId"])
        assert detail.status_code == 200 and detail.json()["documentCount"] == 1


async def test_document_facade_keeps_its_shape_and_requires_the_same_durable_key_pipeline(
    repository,
):
    repo, engine = repository
    request = replace(
        upload(), command=SourceCommand(uuid4().hex, "document.upload", "legacy-facade")
    )
    reserved = await repo.reserve_upload(request)
    await repo.activate_upload(reserved, ArtifactLocator("blob:" + reserved.document_id))
    result = await repo.source_command_result(request.command.key)
    assert result.status == 202 and set(result.body) == {"document", "jobId", "duplicate"}
    with pytest.raises(SourceCommandConflict):
        await repo.reserve_upload(
            replace(request, command=replace(request.command, operation="source.upload"))
        )
    intent = SourceCommand(uuid4().hex, "document.delete", "delete-facade")
    await repo.request_delete(reserved.document_id, datetime.now(timezone.utc), command=intent)
    with pytest.raises(SourceCommandReplay) as replay:
        await repo.request_delete(reserved.document_id, datetime.now(timezone.utc), command=intent)
    assert replay.value.result.status == 204 and replay.value.result.body is None


async def test_concurrent_delete_same_key_returns_one_durable_outcome(repository):
    repo, engine = repository
    request = upload()
    reserved = await repo.reserve_upload(request)
    await repo.activate_upload(reserved, ArtifactLocator("blob:" + reserved.document_id))
    result = await repo.source_command_result(request.command.key)
    source_id = result.body["source"]["sourceId"]
    key = SourceCommand(uuid4().hex, "source.delete", "parallel-delete")
    await asyncio.gather(
        repo.delete_source(source_id, command=key), repo.delete_source(source_id, command=key)
    )
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(knowledge_source_command).where(
                        knowledge_source_command.c.idempotency_key == key.key
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1 and rows[0]["http_status"] == 204


async def test_retry_rejection_is_retained_for_the_original_key(repository):
    repo, _ = repository
    request = upload()
    reserved = await repo.reserve_upload(request)
    record = await repo.activate_upload(reserved, ArtifactLocator("blob:" + reserved.document_id))
    result = await repo.source_command_result(request.command.key)
    source_id = result.body["source"]["sourceId"]
    key = SourceCommand(uuid4().hex, "source.retry", "not-failed")
    for _ in range(2):
        with pytest.raises(SourceCommandReplay) as replay:
            await repo.retry_failed(
                record.document_id,
                datetime.now(timezone.utc),
                command=key,
                source_id=source_id,
                revision_id=record.revision_id,
                expected_attempt=1,
            )
        assert replay.value.result.status == 409
    result = await repo.source_command_result(key.key)
    assert result.body == {"code": "document-not-retryable"}


async def test_activation_failure_does_not_finalize_command_and_retry_uses_same_reservation(
    repository,
):
    from tap.modules.knowledge.adapters.mysql_documents import knowledge_ingestion_job

    repo, engine = repository

    class FailingRepository(MysqlDocumentRepository):
        async def _revision_event(self, *args, **kwargs):
            raise RuntimeError("injected acceptance event failure")

    failing = FailingRepository(
        repo._sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
    )
    request = upload()
    reserved = await repo.reserve_upload(request)
    with pytest.raises(RuntimeError, match="injected"):
        await failing.activate_upload(reserved, ArtifactLocator("blob:" + reserved.document_id))
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    select(knowledge_source_command).where(
                        knowledge_source_command.c.idempotency_key == request.command.key
                    )
                )
            )
            .mappings()
            .one()
        )
        jobs = (
            await connection.execute(
                select(knowledge_ingestion_job.c.job_id).where(
                    knowledge_ingestion_job.c.revision_id == reserved.revision_id
                )
            )
        ).all()
    assert row["status"] == "pending" and row["result"] is None and jobs == []
    await repo.activate_upload(reserved, ArtifactLocator("blob:" + reserved.document_id))
    assert (await repo.source_command_result(request.command.key)).status == 202


async def test_same_upload_key_concurrently_binds_exactly_one_original_result(repository):
    repo, engine = repository
    request = upload()
    reservations = await asyncio.gather(repo.reserve_upload(request), repo.reserve_upload(request))
    assert reservations[0].document_id == reservations[1].document_id
    await repo.activate_upload(
        reservations[0], ArtifactLocator("blob:" + reservations[0].document_id)
    )
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(knowledge_source_command).where(
                        knowledge_source_command.c.idempotency_key == request.command.key
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1 and rows[0]["result"]["accepted"]["duplicate"] is False


async def test_source_command_schema_matches_metadata_and_refuses_lossy_downgrade(repository):
    import subprocess
    import sys

    from scripts.migration_support import schema_differences
    from sqlalchemy import text

    from tap.platform.db.registry import load_authoritative_metadata

    repo, engine = repository
    await repo.reserve_upload(upload())
    async with engine.connect() as connection:
        assert (
            await connection.run_sync(
                lambda sync: schema_differences(sync, load_authoritative_metadata())
            )
            == []
        )
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/backend/alembic.ini",
            "downgrade",
            "0010_knowledge_sources",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "Source command facts require retention" in result.stderr
    async with engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0010a_source_commands"


async def test_source_selection_freezes_current_ready_documents_without_adding_newly_ready_sibling(
    repository,
):
    from sqlalchemy import update

    from tap.modules.knowledge.adapters.mysql_documents import knowledge_document

    repo, engine = repository
    first = upload()
    r1 = await repo.reserve_upload(first)
    await repo.activate_upload(r1, ArtifactLocator("blob:" + r1.document_id))
    source_id = (await repo.source_command_result(first.command.key)).body["source"]["sourceId"]
    second = replace(upload(), source_id=source_id)
    r2 = await repo.reserve_upload(second)
    await repo.activate_upload(r2, ArtifactLocator("blob:" + r2.document_id))
    async with engine.begin() as connection:
        await connection.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == r1.document_id)
            .values(status="ready")
        )
    selected = await repo.load_source_revisions((source_id,))
    assert (
        len(selected) == 1
        and selected[0].document_id == r1.document_id
        and selected[0].source_id == source_id
    )
    async with engine.begin() as connection:
        await connection.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == r2.document_id)
            .values(status="ready")
        )
    current = await repo.load_current_source_revisions(
        tuple((item.source_id, item.revision_id, item.source_content_hash) for item in selected)
    )
    assert current == selected
    assert len(await repo.load_source_revisions((source_id,))) == 2
    from tap.modules.knowledge.ports.answers import DocumentStateChanged

    with pytest.raises(DocumentStateChanged):
        await repo.load_source_revisions((r1.document_id,))
