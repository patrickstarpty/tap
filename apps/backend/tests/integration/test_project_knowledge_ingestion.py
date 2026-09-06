"""Source ledger and revision events share owned SQL transactions."""

import asyncio
from dataclasses import replace
from datetime import datetime

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from sqlalchemy import func, select

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload
from tap.platform.db.session import create_engine_and_session_factory


def test_source_lifecycle_repository_seams_exist():
    assert callable(getattr(MysqlDocumentRepository, "delete_source", None))
    assert callable(getattr(MysqlDocumentRepository, "get_source", None))


def test_source_atomic_reservation_activation_and_tombstone(owned_project_mysql):
    async def run():
        from tap.modules.governance.adapters.schema import project_audit
        from tap.modules.knowledge.adapters.mysql_documents import (
            knowledge_document,
            knowledge_source,
        )
        from tap.modules.knowledge.domain.sources import SourceUnavailable
        from tap.platform.db.schema import outbox

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        command = ReserveUpload(
            filename="private-name.md",
            media_type="text/markdown",
            source_content_hash="sha256:" + "a" * 64,
            size=3,
            now=datetime(2026, 9, 6),
            staging_key="stage:one",
        )
        try:
            first = await repository.reserve_upload(command)
            duplicate = await repository.reserve_upload(command)
            assert first.document_id == duplicate.document_id
            async with sessions() as session:
                document = (await session.execute(select(knowledge_document))).mappings().one()
                source_id = document["source_id"]
                assert await session.scalar(select(func.count()).select_from(knowledge_source)) == 1
                assert await session.scalar(select(func.count()).select_from(outbox)) == 0
            second = await repository.reserve_upload(
                replace(command, source_id=source_id, source_content_hash="sha256:" + "b" * 64)
            )
            accepted = await repository.activate_upload(first, ArtifactLocator("blob:one"))
            await repository.activate_upload(second, ArtifactLocator("blob:two"))
            assert await repository.activate_upload(first, ArtifactLocator("blob:one")) == accepted
            async with sessions() as session:
                events = (
                    (
                        await session.execute(
                            select(outbox).where(
                                outbox.c.message_type == "knowledge.document-revision.accepted"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                assert len(events) == 2
                assert events[0]["envelope"]["payload"]["sourceId"] == source_id
                audit = (await session.execute(select(project_audit))).mappings().all()
                assert {row["action"] for row in audit} == {
                    "source-created",
                    "document-revision-accepted",
                }
                assert all("private-name" not in str(row) for row in audit)
            await repository.delete_source(source_id)
            assert await repository.get_document(first.document_id) is None
            assert (
                await repository.load_ready_revisions((first.document_id, second.document_id)) == ()
            )
            with pytest.raises(SourceUnavailable):
                await repository.reserve_upload(replace(command, source_id=source_id))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_source_activation_audit_failure_rolls_back_domain_state(owned_project_mysql):
    async def run():
        from tap.modules.governance.domain.audit import AuditAction
        from tap.modules.knowledge.adapters.mysql_documents import (
            knowledge_document_revision,
            knowledge_ingestion_job,
        )
        from tap.platform.db.schema import outbox

        class FailingAudit(MysqlDocumentRepository):
            async def _audit(self, session, action, resource_id, key, facts):
                if action is AuditAction.REVISION_ACCEPTED:
                    raise RuntimeError("synthetic audit failure")
                return await super()._audit(session, action, resource_id, key, facts)

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = FailingAudit(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        try:
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="atomic.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "c" * 64,
                    size=3,
                    now=datetime(2026, 9, 6),
                    staging_key="stage:atomic",
                )
            )
            with pytest.raises(RuntimeError, match="synthetic audit failure"):
                await repository.activate_upload(reservation, ArtifactLocator("blob:atomic"))
            async with sessions() as session:
                for table in (knowledge_document_revision, knowledge_ingestion_job, outbox):
                    assert await session.scalar(select(func.count()).select_from(table)) == 0
            recovered = await MysqlDocumentRepository(
                sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
            ).activate_upload(reservation, ArtifactLocator("blob:atomic"))
            assert recovered.document_id == reservation.document_id
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("deletion_first", [True, False], ids=["delete-wins", "ready-wins"])
def test_source_delete_ready_race_has_bounded_atomic_order(owned_project_mysql, deletion_first):
    async def run():
        from datetime import timedelta

        from sqlalchemy import update

        from tap.modules.knowledge.adapters.mysql_documents import (
            knowledge_document,
            knowledge_document_revision,
            knowledge_ingestion_job,
            knowledge_source,
        )
        from tap.modules.knowledge.ports.documents import JobLeaseLost, JobStage, JobStageCommit
        from tap.platform.db.schema import outbox

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        locked, release = asyncio.Event(), asyncio.Event()

        class PausingSource(MysqlDocumentRepository):
            async def _require_source(self, session, source_id):
                row = await super()._require_source(session, source_id)
                locked.set()
                await asyncio.wait_for(release.wait(), 5)
                return row

        paused = PausingSource(sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit)
        try:
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="race.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "d" * 64,
                    size=3,
                    now=datetime(2026, 9, 6),
                    staging_key="stage:race",
                )
            )
            record = await repository.activate_upload(reservation, ArtifactLocator("blob:race"))
            claim = (
                await repository.claim_jobs(
                    worker_id="race-worker",
                    now=datetime.now(),
                    lease_duration=timedelta(seconds=30),
                    limit=1,
                )
            )[0]
            async with sessions.begin() as session:
                source_id = await session.scalar(
                    select(knowledge_document.c.source_id).where(
                        knowledge_document.c.document_id == record.document_id
                    )
                )
                await session.execute(
                    update(knowledge_ingestion_job)
                    .where(knowledge_ingestion_job.c.job_id == claim.job_id)
                    .values(stage="ready")
                )
                await session.execute(
                    update(knowledge_document_revision)
                    .where(knowledge_document_revision.c.revision_id == record.revision_id)
                    .values(
                        chunk_manifest_digest="sha256:" + "e" * 64,
                        projection_digest="sha256:" + "f" * 64,
                    )
                )
            commit = JobStageCommit(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                expected_stage=JobStage.READY,
                completed_at=datetime.now(),
                chunk_count=0,
            )
            first = asyncio.create_task(
                paused.delete_source(source_id) if deletion_first else paused.commit_stage(commit)
            )
            await asyncio.wait_for(locked.wait(), 5)
            second = asyncio.create_task(
                repository.commit_stage(commit)
                if deletion_first
                else repository.delete_source(source_id)
            )
            await asyncio.sleep(0.05)
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first, second, return_exceptions=True), 8
            )
            assert results[0] is None
            assert isinstance(results[1], JobLeaseLost) if deletion_first else results[1] is None
            async with sessions() as session:
                ready = (
                    (
                        await session.execute(
                            select(outbox).where(
                                outbox.c.message_type == "knowledge.document-revision.ready"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                assert len(ready) == (0 if deletion_first else 1)
                deleted_at = await session.scalar(
                    select(knowledge_source.c.deleted_at).where(
                        knowledge_source.c.source_id == source_id
                    )
                )
                assert deleted_at is not None
                if ready:
                    assert ready[0]["created_at"] <= deleted_at
            assert await repository.load_ready_revisions((record.document_id,)) == ()
        finally:
            release.set()
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("promoted", [False, True], ids=["reserved", "promoted"])
def test_source_cancelled_reservation_recovery_retains_retry_facts(owned_project_mysql, promoted):
    async def run():
        from datetime import timedelta

        from sqlalchemy import text

        from tap.modules.knowledge.adapters.mysql_documents import (
            knowledge_document,
            knowledge_document_revision,
        )
        from tap.modules.knowledge.application.documents import DocumentService
        from tap.modules.knowledge.ports.documents import DeletionTarget

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )

        class Artifacts:
            fail = True
            deleted = []

            async def recover_original(self, key, revision):
                return ArtifactLocator("owned:" + revision)

            async def delete_revision_artifacts(self, target):
                assert isinstance(target, DeletionTarget)
                assert target.artifact_locators == (ArtifactLocator("owned:" + target.revision_id),)
                if self.fail:
                    raise RuntimeError("synthetic cleanup failure")
                self.deleted.append(target.revision_id)

            async def discard_staging(self, key):
                assert key == "stage:cancel"

        try:
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="cancel.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "1" * 64,
                    size=3,
                    now=datetime.now(),
                    staging_key="stage:cancel",
                )
            )
            if promoted:
                await repository.record_upload_promotion(
                    reservation, ArtifactLocator("owned:" + reservation.revision_id)
                )
            async with sessions() as session:
                source_id = await session.scalar(select(knowledge_document.c.source_id))
            await repository.delete_source(source_id)
            artifacts = Artifacts()
            service = DocumentService(repository=repository, artifacts=artifacts)

            async def recover():
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE knowledge_document SET "
                            "reservation_expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND"
                        )
                    )
                return await service.recover_uploads(
                    worker_id="cleanup", lease_duration=timedelta(seconds=30), limit=10
                )

            with pytest.raises(RuntimeError, match="synthetic cleanup failure"):
                await recover()
            async with sessions() as session:
                row = (await session.execute(select(knowledge_document))).mappings().one()
                assert row["staging_blob_locator"] == "stage:cancel"
                assert row["deleted_at"] is None
            artifacts.fail = False
            assert await recover() == 1
            assert await recover() == 0
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(knowledge_document_revision)
                    )
                    == 0
                )
                row = (await session.execute(select(knowledge_document))).mappings().one()
                assert row["document_id"] == reservation.document_id
                assert row["source_id"] == source_id
                assert row["deleted_at"] is not None
                assert row["staging_blob_locator"] is None
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("deletion_first", [True, False], ids=["delete-wins", "answer-wins"])
def test_source_delete_answer_race_and_multisource_associations(
    owned_project_mysql, deletion_first
):
    async def run():
        from apps.backend.tests.integration.test_citation_snapshot_transaction import (
            seed_ready,
            snapshot,
        )
        from sqlalchemy import update
        from sqlalchemy.exc import IntegrityError

        from tap.modules.knowledge.adapters.mysql_documents import (
            knowledge_answer_snapshot,
            knowledge_answer_source,
            knowledge_document_revision,
        )
        from tap.modules.knowledge.ports.answers import DocumentStateChanged

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        locked, release = asyncio.Event(), asyncio.Event()

        class PausingSource(MysqlDocumentRepository):
            async def _require_source(self, session, source_id):
                row = await super()._require_source(session, source_id)
                locked.set()
                await asyncio.wait_for(release.wait(), 5)
                return row

        paused = PausingSource(sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit)
        try:
            first = await seed_ready(engine, "a")
            second = await seed_ready(engine, "b")
            from tap.modules.knowledge.adapters.mysql_documents import knowledge_ingestion_job
            from tap.modules.knowledge.ports.documents import (
                JobStage,
                StageResult,
                StageState,
                serialize_stage_results,
            )
            from tap.platform.db.project_scope import scope_values

            now = datetime.now()
            async with sessions.begin() as session:
                await session.execute(
                    knowledge_ingestion_job.insert().values(
                        **scope_values(VALIDATION_SCOPE),
                        job_id="completed-answer-source-job",
                        revision_id=first.revision_id,
                        kind="ingestion",
                        attempt=1,
                        status="completed",
                        stage="ready",
                        next_attempt_at=now,
                        stage_results_json=serialize_stage_results(
                            tuple(
                                StageResult(stage, StageState.COMPLETED, completed_at=now)
                                for stage in JobStage
                            )
                        ),
                        created_at=now,
                        updated_at=now,
                        completed_at=now,
                    )
                )

            answer = replace(
                snapshot("multi", first, with_citation=True), selected_revisions=(first, second)
            )
            await repository.save_answer_with_citations(answer)
            async with sessions() as session:
                rows = (
                    (
                        await session.execute(
                            select(knowledge_answer_source).order_by(
                                knowledge_answer_source.c.ordinal
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                assert [row["source_id"] for row in rows] == ["src_a", "src_b"]
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await session.execute(
                        update(knowledge_document_revision)
                        .where(knowledge_document_revision.c.revision_id == second.revision_id)
                        .values(source_id="src_a")
                    )
            async with sessions.begin() as session:
                await session.execute(
                    update(knowledge_answer_snapshot).values(
                        selected_revisions_json=[first.revision_id, second.revision_id]
                    )
                )
            assert (await repository.load_citation("citation-multi")).selected_revisions == (
                first,
                second,
            )
            next_answer = replace(answer, trace_id="race-answer", citations=())
            first_task = asyncio.create_task(
                paused.delete_source("src_a")
                if deletion_first
                else paused.save_answer_with_citations(next_answer)
            )
            await asyncio.wait_for(locked.wait(), 5)
            second_task = asyncio.create_task(
                repository.save_answer_with_citations(next_answer)
                if deletion_first
                else repository.delete_source("src_a")
            )
            await asyncio.sleep(0.05)
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first_task, second_task, return_exceptions=True), 8
            )
            assert results[0] is None
            assert (
                isinstance(results[1], DocumentStateChanged)
                if deletion_first
                else results[1] is None
            )
            assert await repository.load_citation("citation-multi") is None
            assert await repository.load_ready_revisions((second.document_id,)) == (second,)
            async with sessions() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(knowledge_answer_snapshot)
                    .where(knowledge_answer_snapshot.c.trace_id == "race-answer")
                )
                assert count == (0 if deletion_first else 1)
        finally:
            release.set()
            await engine.dispose()

    asyncio.run(run())


def test_source_scope_and_cancelled_cleanup_preserve_other_source(owned_project_mysql):
    async def run():
        from apps.backend.tests.integration.test_document_project_isolation import OTHER_SCOPE
        from sqlalchemy import insert, update
        from sqlalchemy.exc import IntegrityError

        from tap.modules.access.adapters.mysql import project
        from tap.modules.knowledge.adapters.mysql_documents import knowledge_document
        from tap.modules.knowledge.domain.sources import SourceUnavailable

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        own = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        foreign = MysqlDocumentRepository(
            sessions, scope=OTHER_SCOPE, audit_factory=create_project_audit
        )
        command = ReserveUpload(
            filename="scope.md",
            media_type="text/markdown",
            source_content_hash="sha256:" + "2" * 64,
            size=1,
            now=datetime.now(),
            staging_key="stage:scope",
        )
        try:
            async with sessions.begin() as session:
                await session.execute(
                    insert(project).values(
                        project_id=OTHER_SCOPE.project_id, enterprise_id="local", enabled=True
                    )
                )
            reserved = await own.reserve_upload(command)
            other = await foreign.reserve_upload(command)
            async with sessions() as session:
                source_id = await session.scalar(
                    select(knowledge_document.c.source_id).where(
                        knowledge_document.c.document_id == reserved.document_id
                    )
                )
            assert await foreign.get_source(source_id) is None
            with pytest.raises(SourceUnavailable):
                await foreign.delete_source(source_id)
            with pytest.raises(SourceUnavailable):
                await foreign.reserve_upload(
                    replace(command, source_id=source_id, source_content_hash="sha256:" + "3" * 64)
                )
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await session.execute(
                        update(knowledge_document)
                        .where(knowledge_document.c.document_id == other.document_id)
                        .values(source_id=source_id)
                    )
            await own.delete_source(source_id)
            assert (
                await foreign.activate_upload(other, ArtifactLocator("blob:foreign"))
            ).document_id == other.document_id
            from datetime import timedelta

            from sqlalchemy import text

            from tap.modules.knowledge.adapters.mysql_documents import knowledge_document_revision
            from tap.modules.knowledge.application.documents import DocumentService

            async with sessions() as session:
                other_source = await session.scalar(
                    select(knowledge_document.c.source_id).where(
                        knowledge_document.c.document_id == other.document_id
                    )
                )
            await foreign.delete_source(other_source)

            class ActivatedArtifacts:
                async def discard_staging(self, key):
                    assert key == "stage:scope"

                async def delete_revision_artifacts(self, target):
                    raise AssertionError("activated artifacts remain owned by the deletion job")

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_document SET "
                        "reservation_expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND "
                        "WHERE document_id=:document"
                    ),
                    {"document": other.document_id},
                )
            service = DocumentService(repository=foreign, artifacts=ActivatedArtifacts())
            assert (
                await service.recover_uploads(
                    worker_id="cleanup", lease_duration=timedelta(seconds=30), limit=10
                )
                == 1
            )
            assert (
                await service.recover_uploads(
                    worker_id="cleanup", lease_duration=timedelta(seconds=30), limit=10
                )
                == 0
            )
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(knowledge_document_revision)
                    )
                    == 1
                )

        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("failed_seam", ["audit", "outbox"], ids=["audit", "outbox"])
def test_ready_failure_rolls_back_state_receipts_event_and_audit(owned_project_mysql, failed_seam):
    async def run():
        from datetime import timedelta

        from sqlalchemy import text, update
        from sqlalchemy.exc import DBAPIError

        from tap.modules.governance.adapters.schema import project_audit
        from tap.modules.governance.domain.audit import AuditAction
        from tap.modules.knowledge.adapters.mysql_documents import (
            knowledge_document,
            knowledge_document_revision,
            knowledge_ingestion_job,
        )
        from tap.modules.knowledge.domain.sources import chunk_manifest_digest
        from tap.modules.knowledge.ports.documents import JobStage, JobStageCommit
        from tap.platform.db.schema import outbox

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )

        class Failing(MysqlDocumentRepository):
            audit_appended = False

            async def _audit(self, session, action, *args):
                if failed_seam == "audit" and action is AuditAction.REVISION_READY:
                    raise RuntimeError("synthetic ready persistence failure")
                result = await super()._audit(session, action, *args)
                if action is AuditAction.REVISION_READY:
                    self.audit_appended = True
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(project_audit)
                            .where(project_audit.c.action == "document-revision-ready")
                        )
                        == 1
                    )
                    assert (
                        await session.scalar(
                            select(knowledge_document_revision.c.projection_digest)
                        )
                        == "sha256:" + "b" * 64
                    )
                return result

        repository = Failing(sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit)
        try:
            reserved = await repository.reserve_upload(
                ReserveUpload(
                    filename="ready.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "4" * 64,
                    size=1,
                    now=datetime.now(),
                    staging_key="stage:ready",
                )
            )
            accepted = await repository.activate_upload(reserved, ArtifactLocator("blob:ready"))
            claim = (
                await repository.claim_jobs(
                    worker_id="atomic",
                    now=datetime.now(),
                    lease_duration=timedelta(seconds=30),
                    limit=1,
                )
            )[0]
            async with sessions.begin() as session:
                await session.execute(update(knowledge_ingestion_job).values(stage="ready"))
            if failed_seam == "outbox":
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "ALTER TABLE outbox ADD CONSTRAINT task6_fail_ready_outbox "
                            "CHECK (message_type <> 'knowledge.document-revision.ready')"
                        )
                    )
            commit = JobStageCommit(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                expected_stage=JobStage.READY,
                completed_at=datetime.now(),
                chunk_count=0,
                chunk_manifest_digest=chunk_manifest_digest(()),
                projection_digest="sha256:" + "b" * 64,
            )
            with pytest.raises(
                RuntimeError if failed_seam == "audit" else DBAPIError,
                match="synthetic ready" if failed_seam == "audit" else "task6_fail_ready_outbox",
            ):
                await repository.commit_stage(commit)
            assert repository.audit_appended is (failed_seam == "outbox")
            async with sessions() as session:
                assert await session.scalar(select(knowledge_document.c.status)) != "ready"
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(outbox)
                        .where(outbox.c.message_type == "knowledge.document-revision.ready")
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(project_audit)
                        .where(project_audit.c.action == "document-revision-ready")
                    )
                    == 0
                )
                assert (
                    await session.scalar(select(knowledge_document_revision.c.projection_digest))
                    is None
                )
                assert (
                    await session.scalar(
                        select(knowledge_document_revision.c.chunk_manifest_digest)
                    )
                    is None
                )
                job = (await session.execute(select(knowledge_ingestion_job))).mappings().one()
                assert job["stage"] == "ready"
                assert job["status"] == "processing"
                assert job["lease_token"] == claim.lease_token
            if failed_seam == "outbox":
                async with engine.begin() as connection:
                    await connection.execute(
                        text("ALTER TABLE outbox DROP CHECK task6_fail_ready_outbox")
                    )
            await MysqlDocumentRepository(
                sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
            ).commit_stage(commit)
            assert len(await repository.load_ready_revisions((accepted.document_id,))) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "blocked_fact",
    ["source", "revision", "document", "audit-fk", "load-source"],
    ids=["source", "revision", "document", "audit-fk", "load-source"],
)
def test_task6_lease_expiry_during_owned_fact_lock_rolls_back(owned_project_mysql, blocked_fact):
    async def run():
        from datetime import timedelta

        from sqlalchemy import text, update
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy.sql.dml import Insert, Update
        from sqlalchemy.sql.selectable import Select

        from tap.modules.governance.adapters.schema import project_audit
        from tap.modules.knowledge.adapters import mysql_documents as ledger
        from tap.modules.knowledge.domain.sources import chunk_manifest_digest
        from tap.modules.knowledge.ports.documents import JobLeaseLost, JobStage, JobStageCommit
        from tap.platform.db.schema import outbox

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        blocked = asyncio.Event()
        target = {
            "source": "knowledge_source",
            "load-source": "knowledge_source",
            "revision": "knowledge_document_revision",
            "document": "knowledge_document",
            "audit-fk": "project_audit",
        }[blocked_fact]

        class ObservedSession(AsyncSession):
            async def execute(self, statement, *args, **kwargs):
                if (
                    isinstance(statement, Select)
                    and target == "knowledge_source"
                    and statement._for_update_arg is not None
                    and any(table.name == target for table in statement.get_final_froms())
                ) or (isinstance(statement, (Update, Insert)) and statement.table.name == target):
                    blocked.set()
                return await super().execute(statement, *args, **kwargs)

        observed_sessions = async_sessionmaker(
            engine, class_=ObservedSession, expire_on_commit=False
        )

        class ObservedRepository(MysqlDocumentRepository):
            async def _audit(self, session, action, *args):
                from tap.modules.governance.ports.audit import AuditAction

                if blocked_fact == "audit-fk" and action is AuditAction.REVISION_READY:
                    blocked.set()
                return await super()._audit(session, action, *args)

        observed = ObservedRepository(
            observed_sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        task = None
        try:
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="expiry.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "5" * 64,
                    size=1,
                    now=datetime.now(),
                    staging_key="stage:expiry",
                )
            )
            accepted = await repository.activate_upload(reservation, ArtifactLocator("blob:expiry"))
            claim = (
                await repository.claim_jobs(
                    worker_id="expiry",
                    now=datetime.now(),
                    lease_duration=timedelta(seconds=30),
                    limit=1,
                )
            )[0]
            async with engine.connect() as blocker:
                transaction = await blocker.begin()
                if blocked_fact == "audit-fk":
                    await blocker.execute(
                        text(
                            "SELECT enterprise_id, actor_id FROM actor_principal "
                            "FORCE INDEX (uq_actor_principal_enterprise_actor) "
                            "WHERE enterprise_id='local' AND "
                            "actor_id='tapper-local-user' FOR UPDATE"
                        )
                    )
                else:
                    await blocker.execute(text(f"SELECT * FROM {target} FOR UPDATE"))
                async with sessions.begin() as session:
                    await session.execute(
                        update(ledger.knowledge_ingestion_job).values(
                            stage="ready", lease_until=text("UTC_TIMESTAMP(6)+INTERVAL 1 SECOND")
                        )
                    )
                    deadline = await session.scalar(
                        select(ledger.knowledge_ingestion_job.c.lease_until)
                    )
                tables = (
                    ledger.knowledge_document,
                    ledger.knowledge_document_revision,
                    ledger.knowledge_ingestion_job,
                    project_audit,
                    outbox,
                )
                async with sessions() as session:
                    before = {
                        table.name: (await session.execute(select(table))).mappings().all()
                        for table in tables
                    }
                commit = JobStageCommit(
                    job_id=claim.job_id,
                    lease_token=claim.lease_token,
                    expected_stage=JobStage.READY,
                    completed_at=datetime.now(),
                    chunk_count=0,
                    chunk_manifest_digest=chunk_manifest_digest(()),
                    projection_digest="sha256:" + "6" * 64,
                )
                task = asyncio.create_task(
                    observed.load_ingestion_work(claim.job_id, claim.lease_token, JobStage.READY)
                    if blocked_fact == "load-source"
                    else observed.commit_stage(commit)
                )
                try:
                    await asyncio.wait_for(blocked.wait(), 3)
                    async with asyncio.timeout(4):
                        while await blocker.scalar(text("SELECT UTC_TIMESTAMP(6)")) <= deadline:
                            await asyncio.sleep(0.02)
                    assert not task.done(), "the operation must actually wait on the held SQL row"
                finally:
                    await transaction.rollback()
                outcome = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5))[
                    0
                ]
            async with sessions() as session:
                after = {
                    table.name: (await session.execute(select(table))).mappings().all()
                    for table in tables
                }
            assert after == before, (
                "expired work must not advance facts, receipts, stage, Audit or Outbox"
            )
            assert isinstance(outcome, JobLeaseLost)
            assert await repository.load_ready_revisions((accepted.document_id,)) == ()
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()

    asyncio.run(run())


def test_task6_recovery_batch_allocates_all_leases_after_last_source_lock(owned_project_mysql):
    async def run():
        from datetime import timedelta

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy.sql.selectable import Select

        from tap.modules.knowledge.adapters import mysql_documents as ledger

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        blocked = asyncio.Event()
        task = None
        try:
            for index in range(2):
                await repository.reserve_upload(
                    ReserveUpload(
                        filename="batch.md",
                        media_type="text/markdown",
                        source_content_hash="sha256:" + str(index) * 64,
                        size=1,
                        now=datetime.now(),
                        staging_key=f"stage:batch-{index}",
                    )
                )
            async with sessions.begin() as session:
                await session.execute(
                    text(
                        "UPDATE knowledge_document SET "
                        "reservation_expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND"
                    )
                )
                rows = (
                    (
                        await session.execute(
                            select(ledger.knowledge_document).order_by(
                                ledger.knowledge_document.c.document_id
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                second_source = rows[1]["source_id"]

            class ObservedSession(AsyncSession):
                async def execute(self, statement, *args, **kwargs):
                    if (
                        isinstance(statement, Select)
                        and statement._for_update_arg is not None
                        and any(
                            table.name == "knowledge_source"
                            for table in statement.get_final_froms()
                        )
                        and second_source in statement.compile().params.values()
                    ):
                        blocked.set()
                    return await super().execute(statement, *args, **kwargs)

            observed = MysqlDocumentRepository(
                async_sessionmaker(engine, class_=ObservedSession, expire_on_commit=False),
                scope=VALIDATION_SCOPE,
                audit_factory=create_project_audit,
            )
            async with engine.connect() as blocker:
                transaction = await blocker.begin()
                await blocker.execute(
                    select(ledger.knowledge_source)
                    .where(ledger.knowledge_source.c.source_id == second_source)
                    .with_for_update()
                )
                task = asyncio.create_task(
                    observed.claim_upload_recoveries(
                        worker_id="batch", lease_duration=timedelta(milliseconds=500), limit=2
                    )
                )
                try:
                    await asyncio.wait_for(blocked.wait(), 3)
                    until = await blocker.scalar(text("SELECT UTC_TIMESTAMP(6)+INTERVAL 1 SECOND"))
                    async with asyncio.timeout(4):
                        while await blocker.scalar(text("SELECT UTC_TIMESTAMP(6)")) <= until:
                            await asyncio.sleep(0.02)
                    assert not task.done()
                finally:
                    await transaction.rollback()
                recoveries = await asyncio.wait_for(task, 5)
                returned_at = await blocker.scalar(text("SELECT UTC_TIMESTAMP(6)"))
            assert len(recoveries) == 2
            assert all(item.reservation.expires_at > returned_at for item in recoveries)
            async with sessions() as session:
                leases = (
                    (
                        await session.execute(
                            select(ledger.knowledge_document.c.reservation_expires_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert all(lease > returned_at for lease in leases)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()

    asyncio.run(run())
