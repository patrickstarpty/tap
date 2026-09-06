"""Owned MySQL operator claims, fencing and atomic completion evidence."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from sqlalchemy import select, text, update

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.platform.db.session import create_engine_and_session_factory


def test_operator_recovery_lease_replay_and_atomic_completion(owned_project_mysql):
    from tap.modules.governance.adapters.schema import project_audit
    from tap.modules.knowledge.adapters.mysql_operations import (
        MysqlOperationRepository,
        knowledge_operator_operation,
    )
    from tap.modules.knowledge.domain.operations import (
        OperationBusy,
        OperationLeaseLost,
        OperationRequest,
        OperationResult,
    )
    from tap.platform.db.schema import outbox

    async def run():
        engine, _ = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlOperationRepository(engine, scope=VALIDATION_SCOPE)
        request = OperationRequest(
            command="recover-uploads", limit=7, idempotency_key="key", correlation_id="original"
        )
        duration = timedelta(seconds=60)
        try:
            claim = await repository.claim(request, lease_duration=duration)
            with pytest.raises(OperationBusy):
                await repository.claim(request, lease_duration=duration)
            with pytest.raises(ValueError, match="idempotency-conflict"):
                await repository.claim(replace(request, limit=8), lease_duration=duration)
            await repository.renew(claim, lease_duration=duration)
            async with engine.begin() as connection:
                await connection.execute(
                    update(knowledge_operator_operation).values(
                        lease_until=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND")
                    )
                )
            takeover = await repository.claim(
                replace(request, correlation_id="retry"), lease_duration=duration
            )
            assert takeover.operation_id == claim.operation_id
            assert takeover.correlation_id == "original"
            assert takeover.fence == claim.fence + 1
            result = OperationResult(outcome="partial", counts={"recovered_count": 2})
            with pytest.raises(OperationLeaseLost):
                await repository.complete(claim, result)
            with pytest.raises(RuntimeError, match="rollback"):
                async with engine.begin() as connection:
                    await repository.complete_in_transaction(connection, takeover, result)
                    raise RuntimeError("rollback")
            async with engine.connect() as connection:
                for table in (project_audit, outbox):
                    assert (await connection.execute(select(table))).all() == []
                row = (
                    (await connection.execute(select(knowledge_operator_operation)))
                    .mappings()
                    .one()
                )
                assert row["result"] is None
            completed = await repository.complete(takeover, result)
            replay = await repository.claim(
                replace(request, correlation_id="new"), lease_duration=duration
            )
            assert replay == completed
            assert replay.result == result
            async with engine.connect() as connection:
                fact = (await connection.execute(select(project_audit))).mappings().one()
                event = (await connection.execute(select(outbox))).mappings().one()["envelope"]
                assert fact["correlation_id"] == event["correlation_id"] == "original"
                assert event["payload"]["resultDigest"] == result.digest
                assert event["aggregate_version"] == 1
                assert fact["outcome"] == "partial"
            competing = replace(request, idempotency_key="race")
            results = await asyncio.gather(
                *(repository.claim(competing, lease_duration=duration) for _ in range(2)),
                return_exceptions=True,
            )
            assert sum(isinstance(item, OperationBusy) for item in results) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_operator_runtime_recovery_composes_existing_reservation_lifecycle(
    owned_project_mysql, monkeypatch
):
    from datetime import datetime, timezone

    from tap.entrypoints import knowledge_operator, tapper_runtime
    from tap.modules.knowledge.adapters.mysql_documents import (
        MysqlDocumentRepository,
        knowledge_document,
    )
    from tap.modules.knowledge.domain.operations import OperationRequest
    from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload, StagedOriginal

    class Artifacts:
        scope = VALIDATION_SCOPE
        recovered = []
        discarded = []
        closed = False

        async def recover_original(self, staging_key, revision_id):
            self.recovered.append((staging_key, revision_id))
            return ArtifactLocator("operator:original")

        async def discard_staging(self, staging_key):
            self.discarded.append(staging_key)

        async def aclose(self):
            self.closed = True

    artifacts = Artifacts()
    monkeypatch.setattr(tapper_runtime, "_create_blob", lambda settings: artifacts)

    async def run():
        url = owned_project_database_url(owned_project_mysql)
        engine, sessions = create_engine_and_session_factory(url)
        documents = MysqlDocumentRepository(sessions, scope=VALIDATION_SCOPE)
        try:
            reservation = await documents.reserve_upload(
                ReserveUpload.from_staged(
                    StagedOriginal(
                        staging_key="staging/legacy-reservation",
                        filename="recovery.md",
                        media_type="text/markdown",
                        size=1,
                        source_content_hash="sha256:" + "b" * 64,
                    ),
                    now=datetime.now(timezone.utc),
                )
            )
            async with engine.begin() as connection:
                await connection.execute(
                    update(knowledge_document).values(
                        reservation_expires_at=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND")
                    )
                )
            settings = replace(tapper_runtime.TapperSettings.from_mapping({}), database_url=url)
            request = OperationRequest(
                command="recover-uploads",
                limit=1,
                idempotency_key="runtime-recovery",
                correlation_id="runtime-original",
            )
            receipt = await knowledge_operator.run(settings=settings, request=request)
            assert receipt.result.outcome == "completed"
            assert dict(receipt.result.counts) == {"recovered_count": 1}
            assert artifacts.recovered == [("staging/legacy-reservation", reservation.revision_id)]
            assert artifacts.discarded == ["staging/legacy-reservation"]
            assert artifacts.closed
            async with engine.connect() as connection:
                row = (await connection.execute(select(knowledge_document))).mappings().one()
                assert row["staging_blob_locator"] is None
                assert row["activated_at"] is not None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_operator_recovery_effect_replay_lease_loss_and_publication_gap(owned_project_mysql):
    from datetime import datetime, timezone

    from tap.modules.access.adapters.validation import ValidationAuthorizationPolicy
    from tap.modules.access.domain.authorization import ActorPrincipal
    from tap.modules.knowledge.adapters.mysql_documents import (
        MysqlDocumentRepository,
        knowledge_ingestion_job,
    )
    from tap.modules.knowledge.adapters.mysql_operations import (
        MysqlOperationRepository,
        knowledge_operator_operation,
    )
    from tap.modules.knowledge.application.operations import KnowledgeOperator
    from tap.modules.knowledge.domain.operations import (
        OperationBusy,
        OperationLeaseLost,
        OperationRequest,
    )
    from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload, StagedOriginal

    class Registry:
        async def get_principal(self, enterprise_id, project_id, actor_id):
            return ActorPrincipal(
                enterprise_id=enterprise_id,
                actor_id=actor_id,
                principal_type="VALIDATION",
                enabled=True,
            )

    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        repository = MysqlOperationRepository(engine, scope=VALIDATION_SCOPE)

        class Effects:
            scope = VALIDATION_SCOPE
            calls = 0
            block = False

            async def execute(self, command, *, limit):
                self.calls += 1
                if self.block:
                    async with engine.begin() as connection:
                        await connection.execute(
                            update(knowledge_operator_operation)
                            .where(knowledge_operator_operation.c.idempotency_key == "lease-loss")
                            .values(lease_until=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"))
                        )
                    try:
                        await asyncio.Event().wait()
                    finally:
                        self.cancelled = True
                return {"recovered_count": 2}

        effects = Effects()
        operator = KnowledgeOperator(
            scope=VALIDATION_SCOPE,
            policy=ValidationAuthorizationPolicy(Registry()),
            repository=repository,
            effects=effects,
            lease_duration=timedelta(seconds=3),
        )
        request = OperationRequest(
            command="recover-uploads", limit=7, idempotency_key="effects", correlation_id="original"
        )
        try:
            first = await operator.run(request)
            assert (await operator.run(replace(request, correlation_id="retry"))) == first
            assert effects.calls == 1
            effects.block = True
            with pytest.raises(OperationLeaseLost):
                await asyncio.wait_for(
                    operator.run(replace(request, idempotency_key="lease-loss")), 5
                )
            assert effects.cancelled
            async with engine.connect() as connection:
                result = (
                    await connection.execute(
                        select(knowledge_operator_operation.c.result).where(
                            knowledge_operator_operation.c.idempotency_key == "lease-loss"
                        )
                    )
                ).scalar_one()
                assert result is None

            documents = MysqlDocumentRepository(sessions, scope=VALIDATION_SCOPE)
            reservation = await documents.reserve_upload(
                ReserveUpload.from_staged(
                    StagedOriginal(
                        staging_key="staging/legacy",
                        filename="operator.md",
                        media_type="text/markdown",
                        size=1,
                        source_content_hash="sha256:" + "a" * 64,
                    ),
                    now=datetime.now(timezone.utc),
                )
            )
            await documents.activate_upload(reservation, ArtifactLocator("original:operator"))
            # This models the durable state after Milvus upsert releases its lock
            # but before the worker has committed SQL ready.
            async with engine.begin() as connection:
                await connection.execute(
                    update(knowledge_ingestion_job).values(stage="publishing", status="processing")
                )
            with pytest.raises(OperationBusy, match="publication-in-progress"):
                await repository.ready_work(10)
            async with engine.begin() as connection:
                await connection.execute(
                    update(knowledge_ingestion_job).values(stage="ready", status="completed")
                )
            assert await repository.ready_work(10) == ()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_operator_ready_snapshot_returns_populated_work_and_rejects_limit_plus_one(
    owned_project_mysql,
):
    from datetime import datetime, timezone

    from sqlalchemy import insert

    from tap.modules.knowledge.adapters import mysql_documents as ledger
    from tap.modules.knowledge.adapters.mysql_operations import MysqlOperationRepository
    from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload, StagedOriginal
    from tap.platform.db.project_scope import scope_values

    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        documents = ledger.MysqlDocumentRepository(sessions, scope=VALIDATION_SCOPE)
        repository = MysqlOperationRepository(engine, scope=VALIDATION_SCOPE)
        expected = {}
        try:
            for index in range(2):
                now = datetime.now(timezone.utc)
                reservation = await documents.reserve_upload(
                    ReserveUpload.from_staged(
                        StagedOriginal(
                            staging_key=f"staging/snapshot-{index}",
                            filename=f"ready-{index}.md",
                            media_type="text/markdown",
                            size=1,
                            source_content_hash="sha256:" + str(index) * 64,
                        ),
                        now=now,
                    )
                )
                record = await documents.activate_upload(
                    reservation, ArtifactLocator(f"original:{index}")
                )
                expected[record.document_id] = (
                    record.revision_id,
                    f"chunk-{index}",
                    f"chunks:{index}",
                )
                async with engine.begin() as connection:
                    await connection.execute(
                        update(ledger.knowledge_document)
                        .where(ledger.knowledge_document.c.document_id == record.document_id)
                        .values(status="ready", stage="ready")
                    )
                    await connection.execute(
                        update(ledger.knowledge_document_revision)
                        .where(
                            ledger.knowledge_document_revision.c.revision_id == record.revision_id
                        )
                        .values(
                            chunks_blob_locator=f"chunks:{index}",
                            embeddings_blob_locator=f"embeddings:{index}",
                        )
                    )
                    await connection.execute(
                        update(ledger.knowledge_ingestion_job)
                        .where(ledger.knowledge_ingestion_job.c.revision_id == record.revision_id)
                        .values(stage="ready", status="completed")
                    )
                    await connection.execute(
                        insert(ledger.knowledge_chunk_manifest).values(
                            **scope_values(VALIDATION_SCOPE),
                            chunk_id=f"chunk-{index}",
                            logical_chunk_id=f"logical-{index}",
                            revision_id=record.revision_id,
                            ordinal=0,
                            root_id=record.document_id,
                            parent_id=None,
                            anchor_json={"type": "document"},
                            chunk_content_hash="sha256:" + "a" * 64,
                            embedding_model_version="test",
                            index_version="test",
                            created_at=now,
                        )
                    )
            work = await repository.ready_work(2)
            assert len(work) == 2
            assert {
                item.document_id: (item.revision_id, item.manifest[0].chunk_id, item.chunks_locator)
                for item in work
            } == expected
            assert all(item.stage.value == "ready" and len(item.manifest) == 1 for item in work)
            with pytest.raises(ValueError, match="ready corpus exceeds rebuild bound"):
                await repository.ready_work(1)
        finally:
            await engine.dispose()

    asyncio.run(run())
