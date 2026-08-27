from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql.selectable import Select

from tap.modules.knowledge.adapters import mysql_documents
from tap.modules.knowledge.adapters.mysql_documents import (
    ANSWER_SNAPSHOT_LOCK_NAME,
    MysqlDocumentRepository,
    _NamedLockAttempt,
    _release_named_lock,
)
from tap.modules.knowledge.application.answers import AnswerService
from tap.modules.knowledge.domain.documents import DocumentId, RevisionId, chunk_id_for
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    ResourceMode,
    ResourceRef,
    SourceFamily,
)
from tap.modules.knowledge.ports.answers import (
    AnswerSnapshot,
    AnswerSnapshotUnavailable,
    CitationSnapshot,
    DocumentStateChanged,
    ReadyDocumentRevision,
)
from tap.modules.knowledge.ports.citations import CitationSnapshotCorrupt
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.skipif(
    os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1",
    reason="set TAP_RUN_MYSQL_INTEGRATION=1 for real MySQL answer snapshot tests",
)

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)
SOURCE_HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64
ANCHOR_JSON = json.dumps(
    {"endOffset": 8, "headingPath": [], "startOffset": 0, "type": "document"},
    separators=(",", ":"),
    sort_keys=True,
)


class SnapshotDocumentBarrierSession(AsyncSession):
    """Hold the selected document lock after recheck but before snapshot insert."""

    document_locked = asyncio.Event()
    allow_snapshot = asyncio.Event()

    async def execute(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = await super().execute(statement, *args, **kwargs)
        if (
            isinstance(statement, Select)
            and statement._for_update_arg is not None
            and any(table.name == "knowledge_document" for table in statement.get_final_froms())
            and not type(self).document_locked.is_set()
        ):
            type(self).document_locked.set()
            await asyncio.wait_for(type(self).allow_snapshot.wait(), timeout=5)
        return result


class DeleteDocumentAttemptSession(AsyncSession):
    """Expose the delete transaction's attempt to acquire the document lock."""

    document_attempted = asyncio.Event()

    async def execute(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        if (
            isinstance(statement, Select)
            and statement._for_update_arg is not None
            and any(table.name == "knowledge_document" for table in statement.get_final_froms())
        ):
            type(self).document_attempted.set()
        return await super().execute(statement, *args, **kwargs)


class CancelAfterNamedLockRepository(MysqlDocumentRepository):
    """Pause only after MySQL granted GET_LOCK but before its result reaches the caller."""

    lock_granted = asyncio.Event()

    async def _acquire_answer_snapshot_lock(
        self,
        connection: AsyncConnection,
        *,
        server_connection_id: int,
        lock_attempt: _NamedLockAttempt | None = None,
    ) -> object:
        acquired = await super()._acquire_answer_snapshot_lock(
            connection,
            server_connection_id=server_connection_id,
            lock_attempt=lock_attempt,
        )
        if acquired == 1:
            type(self).lock_granted.set()
            await asyncio.Event().wait()
        return acquired


class CancelWhileWaitingNamedLockRepository(MysqlDocumentRepository):
    acquisition_started = asyncio.Event()

    async def _acquire_answer_snapshot_lock(
        self,
        connection: AsyncConnection,
        *,
        server_connection_id: int,
        lock_attempt: _NamedLockAttempt | None = None,
    ) -> object:
        type(self).acquisition_started.set()
        return await super()._acquire_answer_snapshot_lock(
            connection,
            server_connection_id=server_connection_id,
            lock_attempt=lock_attempt,
        )


class NeverAnswerGateway:
    called = False

    async def answer(self, request, policy):  # type: ignore[no-untyped-def]
        del request, policy
        self.called = True
        raise AssertionError("cross-owned revision reached search/model gateway")


async def clean(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM outbox WHERE aggregate_type = 'knowledge_document'")
        )
        await connection.execute(text("UPDATE knowledge_document SET current_revision_id=NULL"))
        for table in (
            "knowledge_citation_snapshot",
            "knowledge_answer_snapshot",
            "knowledge_chunk_manifest",
            "knowledge_ingestion_job",
            "knowledge_document_revision",
            "knowledge_document",
        ):
            await connection.execute(text(f"DELETE FROM {table}"))


async def seed_ready(engine, suffix: str) -> ReadyDocumentRevision:  # type: ignore[no-untyped-def]
    document_id = f"doc_{suffix}"
    revision_id = f"rev_{suffix}"
    now = datetime(2026, 8, 28, 9, 0)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO knowledge_document "
                "(document_id,filename,media_type,current_revision_id,source_content_hash,"
                "dedupe_key,reservation_parser_version,reservation_chunker_version,"
                "reservation_pipeline_version,status,stage,chunk_count,activated_at,"
                "created_at,updated_at) VALUES "
                "(:document_id,:filename,'text/markdown',NULL,:source_hash,:dedupe_key,"
                "'athena-parser-v1','athena-structure-512-v1','athena-ingestion-v1',"
                "'ready','ready',1,:now,:now,:now)"
            ),
            {
                "document_id": document_id,
                "filename": f"{suffix}.md",
                "source_hash": SOURCE_HASH,
                "dedupe_key": "sha256:" + suffix[0] * 64,
                "now": now,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_document_revision "
                "(revision_id,document_id,source_content_hash,original_blob_locator,"
                "normalized_blob_locator,chunks_blob_locator,parser_version,chunker_version,"
                "pipeline_version,created_at) VALUES "
                "(:revision_id,:document_id,:source_hash,'original',:normalized,:chunks,"
                "'athena-parser-v1','athena-structure-512-v1','athena-ingestion-v1',:now)"
            ),
            {
                "revision_id": revision_id,
                "document_id": document_id,
                "source_hash": SOURCE_HASH,
                "normalized": f"athena-artifacts/revisions/{revision_id}/normalized-v1.json",
                "chunks": f"athena-artifacts/revisions/{revision_id}/chunks-v1.jsonl.gz",
                "now": now,
            },
        )
        await connection.execute(
            text(
                "UPDATE knowledge_document SET current_revision_id=:revision_id "
                "WHERE document_id=:document_id"
            ),
            {"revision_id": revision_id, "document_id": document_id},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_chunk_manifest "
                "(chunk_id,logical_chunk_id,revision_id,ordinal,root_id,parent_id,anchor_json,"
                "chunk_content_hash,embedding_model_version,index_version,created_at) VALUES "
                "(:chunk_id,:logical_id,:revision_id,0,:document_id,'b_000000',:anchor_json,"
                ":chunk_hash,'athena-embedding','athena-doc-v1',:now)"
            ),
            {
                "chunk_id": f"h_{suffix}",
                "logical_id": f"lc_{suffix}",
                "revision_id": revision_id,
                "document_id": document_id,
                "anchor_json": ANCHOR_JSON,
                "chunk_hash": CHUNK_HASH,
                "now": now,
            },
        )
    return ReadyDocumentRevision(document_id, revision_id, SOURCE_HASH)


def snapshot(
    trace_id: str, selected: ReadyDocumentRevision, *, with_citation: bool
) -> AnswerSnapshot:
    citations = (
        (
            CitationSnapshot(
                trace_id=trace_id,
                citation_id=f"citation-{trace_id}",
                document_id=selected.document_id,
                revision_id=selected.revision_id,
                chunk_id=f"h_{selected.document_id.removeprefix('doc_')}",
                source_content_hash=SOURCE_HASH,
                chunk_content_hash=CHUNK_HASH,
                anchor_json=ANCHOR_JSON,
            ),
        )
        if with_citation
        else ()
    )
    return AnswerSnapshot(
        trace_id=trace_id,
        query_hash="sha256:" + "c" * 64,
        selected_revisions=(selected,),
        citations=citations,
    )


def test_snapshot_and_exact_citations_commit_atomically_against_current_ready_rows() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        repository = MysqlDocumentRepository(sessions)
        try:
            selected = await seed_ready(engine, "a")
            assert await repository.load_ready_revisions((selected.document_id,)) == (selected,)

            await repository.save_answer_with_citations(
                snapshot("trace-a", selected, with_citation=True)
            )

            async with engine.connect() as connection:
                answer_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_answer_snapshot")
                )
                citation_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_citation_snapshot")
                )
            assert (answer_count, citation_count) == (1, 1)

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_document SET status='processing' "
                        "WHERE document_id=:document_id"
                    ),
                    {"document_id": selected.document_id},
                )
            with pytest.raises(DocumentStateChanged):
                await repository.save_answer_with_citations(
                    snapshot("trace-state-change", selected, with_citation=True)
                )
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM knowledge_answer_snapshot "
                            "WHERE trace_id='trace-state-change'"
                        )
                    )
                    == 0
                )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_ready_lookup_rejects_current_revision_owned_by_another_document() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        repository = MysqlDocumentRepository(sessions)
        try:
            first = await seed_ready(engine, "a")
            second = await seed_ready(engine, "b")
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_document SET current_revision_id=:foreign_revision "
                        "WHERE document_id=:document_id"
                    ),
                    {
                        "foreign_revision": second.revision_id,
                        "document_id": first.document_id,
                    },
                )

            assert await repository.load_ready_revisions((first.document_id,)) == ()
            gateway = NeverAnswerGateway()
            with pytest.raises(DocumentStateChanged):
                await AnswerService(repository=repository, knowledge=gateway).answer(
                    AnswerRequest(
                        query="What is the policy?",
                        resource_refs=(
                            ResourceRef(
                                SourceFamily.DOC,
                                first.document_id,
                                ResourceMode.SCOPE,
                            ),
                        ),
                    )
                )
            assert gateway.called is False
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_trace_or_citation_conflict_rolls_back_the_whole_snapshot() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        repository = MysqlDocumentRepository(sessions)
        try:
            selected = await seed_ready(engine, "a")
            first = snapshot("trace-conflict", selected, with_citation=True)
            await repository.save_answer_with_citations(first)
            with pytest.raises(AnswerSnapshotUnavailable):
                await repository.save_answer_with_citations(first)

            async with engine.connect() as connection:
                counts = (
                    await connection.scalar(text("SELECT COUNT(*) FROM knowledge_answer_snapshot")),
                    await connection.scalar(
                        text("SELECT COUNT(*) FROM knowledge_citation_snapshot")
                    ),
                )
            assert counts == (1, 1)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", ["missing", "anchor", "hash"])
def test_snapshot_rejects_citation_without_an_exact_manifest_fact(tamper: str) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        repository = MysqlDocumentRepository(sessions)
        try:
            selected = await seed_ready(engine, "a")
            anchor_json = ANCHOR_JSON
            chunk_hash = CHUNK_HASH
            chunk_id = "h_a"
            if tamper in {"missing", "anchor"}:
                anchor_json = json.dumps(
                    {
                        "endOffset": 9010,
                        "headingPath": [],
                        "startOffset": 9000,
                        "type": "document",
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            if tamper == "missing":
                chunk_id = str(
                    chunk_id_for(RevisionId(selected.revision_id), anchor_json, chunk_hash)
                )
            elif tamper == "hash":
                chunk_hash = "sha256:" + "d" * 64
            citation = CitationSnapshot(
                trace_id=f"trace-manifest-{tamper}",
                citation_id=f"citation-manifest-{tamper}",
                document_id=selected.document_id,
                revision_id=selected.revision_id,
                chunk_id=chunk_id,
                source_content_hash=selected.source_content_hash,
                chunk_content_hash=chunk_hash,
                anchor_json=anchor_json,
            )
            forged = AnswerSnapshot(
                trace_id=citation.trace_id,
                query_hash="sha256:" + "c" * 64,
                selected_revisions=(selected,),
                citations=(citation,),
            )

            with pytest.raises(AnswerSnapshotUnavailable):
                await repository.save_answer_with_citations(forged)

            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM knowledge_answer_snapshot "
                            "WHERE trace_id=:trace_id"
                        ),
                        {"trace_id": forged.trace_id},
                    )
                    == 0
                )
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM knowledge_citation_snapshot "
                            "WHERE citation_id=:citation_id"
                        ),
                        {"citation_id": citation.citation_id},
                    )
                    == 0
                )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_delete_cannot_cross_current_set_recheck_before_snapshot_commit() -> None:
    """The recheck, answer insert, and citation insert share one document-row lock."""

    async def scenario() -> None:
        engine, setup_sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            selected = await seed_ready(engine, "a")
            SnapshotDocumentBarrierSession.document_locked = asyncio.Event()
            SnapshotDocumentBarrierSession.allow_snapshot = asyncio.Event()
            DeleteDocumentAttemptSession.document_attempted = asyncio.Event()
            snapshot_sessions = async_sessionmaker(
                engine, class_=SnapshotDocumentBarrierSession, expire_on_commit=False
            )
            delete_sessions = async_sessionmaker(
                engine, class_=DeleteDocumentAttemptSession, expire_on_commit=False
            )
            saving = asyncio.create_task(
                MysqlDocumentRepository(snapshot_sessions).save_answer_with_citations(
                    snapshot("trace-delete-barrier", selected, with_citation=True)
                )
            )
            await asyncio.wait_for(SnapshotDocumentBarrierSession.document_locked.wait(), timeout=5)
            deleting = asyncio.create_task(
                MysqlDocumentRepository(delete_sessions).request_delete(
                    DocumentId(selected.document_id), datetime(2026, 8, 28, 9, 1)
                )
            )
            await asyncio.wait_for(
                DeleteDocumentAttemptSession.document_attempted.wait(), timeout=5
            )
            assert not saving.done()
            assert not deleting.done()

            SnapshotDocumentBarrierSession.allow_snapshot.set()
            await asyncio.wait_for(asyncio.gather(saving, deleting), timeout=5)

            async with engine.connect() as connection:
                answer_count = await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM knowledge_answer_snapshot "
                        "WHERE trace_id='trace-delete-barrier'"
                    )
                )
                citation_count = await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM knowledge_citation_snapshot "
                        "WHERE trace_id='trace-delete-barrier'"
                    )
                )
                status_value = await connection.scalar(
                    text("SELECT status FROM knowledge_document WHERE document_id=:document_id"),
                    {"document_id": selected.document_id},
                )
            assert (answer_count, citation_count, status_value) == (1, 1, "deleting")
        finally:
            SnapshotDocumentBarrierSession.allow_snapshot.set()
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_cancelled_snapshot_rolls_back_and_releases_connection_scoped_lock() -> None:
    async def scenario() -> None:
        engine, setup_sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            selected = await seed_ready(engine, "a")
            SnapshotDocumentBarrierSession.document_locked = asyncio.Event()
            SnapshotDocumentBarrierSession.allow_snapshot = asyncio.Event()
            snapshot_sessions = async_sessionmaker(
                engine, class_=SnapshotDocumentBarrierSession, expire_on_commit=False
            )
            saving = asyncio.create_task(
                MysqlDocumentRepository(snapshot_sessions).save_answer_with_citations(
                    snapshot("trace-cancelled", selected, with_citation=True)
                )
            )
            await asyncio.wait_for(SnapshotDocumentBarrierSession.document_locked.wait(), timeout=5)
            saving.cancel("caller disconnected")
            with pytest.raises(asyncio.CancelledError):
                await saving

            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM knowledge_answer_snapshot "
                            "WHERE trace_id='trace-cancelled'"
                        )
                    )
                    == 0
                )

            await asyncio.wait_for(
                MysqlDocumentRepository(setup_sessions).save_answer_with_citations(
                    snapshot("trace-after-cancel", selected, with_citation=False)
                ),
                timeout=5,
            )
        finally:
            SnapshotDocumentBarrierSession.allow_snapshot.set()
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_cancellation_after_server_grants_named_lock_cannot_leak_pool_ownership() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            selected = await seed_ready(engine, "a")
            CancelAfterNamedLockRepository.lock_granted = asyncio.Event()
            saving = asyncio.create_task(
                CancelAfterNamedLockRepository(sessions).save_answer_with_citations(
                    snapshot("trace-lock-cancel", selected, with_citation=False)
                )
            )
            await asyncio.wait_for(CancelAfterNamedLockRepository.lock_granted.wait(), timeout=5)
            saving.cancel("cancel after server grant")
            with pytest.raises(asyncio.CancelledError):
                await saving

            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT IS_FREE_LOCK(:lock_name)"),
                        {"lock_name": "tap:athena:answer-snapshot-retention:v1"},
                    )
                    == 1
                )

            await asyncio.wait_for(
                MysqlDocumentRepository(sessions).save_answer_with_citations(
                    snapshot("trace-after-lock-cancel", selected, with_citation=False)
                ),
                timeout=5,
            )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_cancellation_while_waiting_for_named_lock_settles_connection() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            selected = await seed_ready(engine, "a")
            async with engine.connect() as holder:
                assert (
                    await holder.scalar(
                        text("SELECT GET_LOCK(:lock_name, 5)"),
                        {"lock_name": "tap:athena:answer-snapshot-retention:v1"},
                    )
                    == 1
                )
                await holder.commit()
                CancelWhileWaitingNamedLockRepository.acquisition_started = asyncio.Event()
                saving = asyncio.create_task(
                    CancelWhileWaitingNamedLockRepository(sessions).save_answer_with_citations(
                        snapshot("trace-wait-cancel", selected, with_citation=False)
                    )
                )
                await asyncio.wait_for(
                    CancelWhileWaitingNamedLockRepository.acquisition_started.wait(),
                    timeout=5,
                )
                await asyncio.sleep(0.05)
                saving.cancel("cancel blocked GET_LOCK")
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(saving, timeout=5)
                assert (
                    await holder.scalar(
                        text("SELECT RELEASE_LOCK(:lock_name)"),
                        {"lock_name": "tap:athena:answer-snapshot-retention:v1"},
                    )
                    == 1
                )

            await asyncio.wait_for(
                MysqlDocumentRepository(sessions).save_answer_with_citations(
                    snapshot("trace-after-wait-cancel", selected, with_citation=False)
                ),
                timeout=5,
            )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_uncertain_named_lock_ownership_terminates_physical_connection_before_reuse() -> None:
    async def scenario() -> None:
        engine, _sessions = create_engine_and_session_factory(DATABASE_URL)
        first_connection_id: int | None = None
        try:
            async with engine.connect() as connection:
                first_connection_id = await connection.scalar(text("SELECT CONNECTION_ID()"))
                assert (
                    await connection.scalar(
                        text("SELECT GET_LOCK(:lock_name, 5)"),
                        {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
                    )
                    == 1
                )
                await connection.commit()

                await _release_named_lock(
                    connection,
                    ANSWER_SNAPSHOT_LOCK_NAME,
                    ownership_confirmed=False,
                    engine=engine,
                    server_connection_id=first_connection_id,
                )

                assert connection.invalidated is True

            async with engine.connect() as replacement:
                replacement_connection_id = await replacement.scalar(text("SELECT CONNECTION_ID()"))
                assert replacement_connection_id != first_connection_id
                assert (
                    await replacement.scalar(
                        text("SELECT GET_LOCK(:lock_name, 5)"),
                        {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
                    )
                    == 1
                )
                assert (
                    await replacement.scalar(
                        text("SELECT RELEASE_LOCK(:lock_name)"),
                        {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ("acquire", "release"))
def test_in_flight_named_lock_is_killed_and_verified_before_return(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client deadline must settle the server session, not only its pool handle."""

    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL, pool_size=1, max_overflow=0)
        try:
            async with engine.connect() as connection:
                connection_id = await connection.scalar(text("SELECT CONNECTION_ID()"))
                assert type(connection_id) is int
                if phase == "release":
                    assert (
                        await connection.scalar(
                            text("SELECT GET_LOCK(:lock_name, 0)"),
                            {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
                        )
                        == 1
                    )
                    await connection.commit()
                    statement = "SELECT IF(SLEEP(30)=0,RELEASE_LOCK(:lock_name),NULL)"
                    monkeypatch.setattr(
                        mysql_documents,
                        "ANSWER_SNAPSHOT_LOCK_RELEASE_SQL",
                        statement,
                        raising=False,
                    )
                    monkeypatch.setattr(
                        mysql_documents,
                        "ANSWER_SNAPSHOT_LOCK_RELEASE_DEADLINE_SECONDS",
                        0.05,
                    )
                    with pytest.raises(AnswerSnapshotUnavailable):
                        await _release_named_lock(
                            connection,
                            ANSWER_SNAPSHOT_LOCK_NAME,
                            ownership_confirmed=True,
                            engine=engine,
                            server_connection_id=connection_id,
                        )
                else:
                    statement = "SELECT IF(GET_LOCK(:lock_name,0)=1,SLEEP(30),-1)"
                    monkeypatch.setattr(
                        mysql_documents,
                        "ANSWER_SNAPSHOT_LOCK_ACQUIRE_SQL",
                        statement,
                        raising=False,
                    )
                    monkeypatch.setattr(
                        mysql_documents,
                        "ANSWER_SNAPSHOT_LOCK_ACQUIRE_DEADLINE_SECONDS",
                        0.05,
                    )
                    repository = MysqlDocumentRepository(
                        async_sessionmaker(engine, expire_on_commit=False)
                    )
                    with pytest.raises(AnswerSnapshotUnavailable):
                        await repository._acquire_answer_snapshot_lock(
                            connection,
                            server_connection_id=connection_id,
                        )

            observer_engine = create_async_engine(DATABASE_URL)
            async with observer_engine.connect() as observer:
                assert (
                    await observer.scalar(
                        text(
                            "SELECT COUNT(*) FROM information_schema.processlist "
                            "WHERE id=:connection_id"
                        ),
                        {"connection_id": connection_id},
                    )
                    == 0
                )
                assert (
                    await observer.scalar(
                        text("SELECT IS_USED_LOCK(:lock_name)"),
                        {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
                    )
                    != connection_id
                )
                assert (
                    await observer.scalar(
                        text("SELECT GET_LOCK(:lock_name, 0)"),
                        {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
                    )
                    == 1
                )
                assert (
                    await observer.scalar(
                        text("SELECT RELEASE_LOCK(:lock_name)"),
                        {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
                    )
                    == 1
                )
            await observer_engine.dispose()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_citation_lookup_joins_answer_ownership_and_rechecks_current_document() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        repository = MysqlDocumentRepository(sessions)
        try:
            selected = await seed_ready(engine, "a")
            committed = snapshot("trace-lookup", selected, with_citation=True)
            await repository.save_answer_with_citations(committed)
            citation = committed.citations[0]

            lookup = await repository.load_citation(citation.citation_id)

            assert lookup is not None
            assert lookup.answer_trace_id == "trace-lookup"
            assert lookup.selected_revisions == (selected,)
            assert lookup.document is not None
            assert lookup.document.filename == "a.md"
            assert lookup.manifest is not None
            assert lookup.manifest.chunk_id == "h_a"
            assert await repository.citation_is_current(citation) is True

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_document SET status='deleting' "
                        "WHERE document_id=:document_id"
                    ),
                    {"document_id": selected.document_id},
                )
            assert await repository.citation_is_current(citation) is False

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_answer_snapshot SET selected_revisions_json=:selected "
                        "WHERE trace_id='trace-lookup'"
                    ),
                    {
                        "selected": json.dumps(
                            [
                                {
                                    "documentId": selected.document_id,
                                    "revisionId": selected.revision_id,
                                    "sourceContentHash": SOURCE_HASH,
                                    "extra": True,
                                }
                            ]
                        )
                    },
                )
            with pytest.raises(CitationSnapshotCorrupt):
                await repository.load_citation(citation.citation_id)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_retention_is_globally_serialized_and_cascades_old_citations() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        repository = MysqlDocumentRepository(sessions)
        try:
            first = await seed_ready(engine, "a")
            second = await seed_ready(engine, "b")
            old_answers = [
                {
                    "trace_id": f"old-{index:04d}",
                    "query_hash": "sha256:" + "d" * 64,
                    "selected": json.dumps(
                        [
                            {
                                "documentId": first.document_id,
                                "revisionId": first.revision_id,
                                "sourceContentHash": SOURCE_HASH,
                            }
                        ],
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    "created_at": datetime(2020, 1, 1),
                }
                for index in range(999)
            ]
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_answer_snapshot "
                        "(trace_id,query_hash,selected_revisions_json,created_at) VALUES "
                        "(:trace_id,:query_hash,:selected,:created_at)"
                    ),
                    old_answers,
                )
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_citation_snapshot "
                        "(citation_id,trace_id,document_id,revision_id,chunk_id,"
                        "source_content_hash,chunk_content_hash,anchor_json,created_at) VALUES "
                        "('old-citation','old-0000',:document_id,:revision_id,:chunk_id,"
                        ":source_hash,:chunk_hash,:anchor_json,:created_at)"
                    ),
                    {
                        "document_id": first.document_id,
                        "revision_id": first.revision_id,
                        "chunk_id": "h_a",
                        "source_hash": SOURCE_HASH,
                        "chunk_hash": CHUNK_HASH,
                        "anchor_json": ANCHOR_JSON,
                        "created_at": datetime(2020, 1, 1),
                    },
                )

            await asyncio.gather(
                repository.save_answer_with_citations(
                    snapshot("new-a", first, with_citation=False)
                ),
                repository.save_answer_with_citations(
                    snapshot("new-b", second, with_citation=False)
                ),
            )

            async with engine.connect() as connection:
                answer_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_answer_snapshot")
                )
                oldest = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_answer_snapshot WHERE trace_id='old-0000'")
                )
                old_citation = await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM knowledge_citation_snapshot "
                        "WHERE citation_id='old-citation'"
                    )
                )
            assert answer_count == 1000
            assert oldest == 0
            assert old_citation == 0
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())
