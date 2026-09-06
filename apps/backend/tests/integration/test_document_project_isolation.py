"""Project authority on the real document ledger, using a disposable database."""

import os
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.mysql import project
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.ports.documents import ArtifactLocator, JobLeaseLost, ReserveUpload
from tap.platform.db.session import create_engine_and_session_factory

OTHER_SCOPE = ProjectScopeContext(
    enterprise_id="local",
    project_id="other-project",
    actor_id="tapper-local-user",
    identity_mode=IdentityMode.VALIDATION,
)


@pytest.fixture
def project_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import isolated_mysql

    with ExitStack() as resources:
        with monkeypatch.context() as environment:
            environment.delenv("TAP_DATABASE_URL", raising=False)
            environment.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
            database = resources.enter_context(isolated_mysql())
            database.upgrade("head")
        yield database.url.replace("mysql+pymysql:", "mysql+asyncmy:")


@pytest.mark.asyncio
async def test_document_project_scope_isolates_digest_lookup_recovery_and_claims(
    project_database: str,
) -> None:
    engine, sessions = create_engine_and_session_factory(project_database)
    try:
        async with sessions.begin() as session:
            await session.execute(
                insert(project).values(
                    project_id="other-project", enterprise_id="local", enabled=True
                )
            )
        first = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        second = MysqlDocumentRepository(
            sessions, scope=OTHER_SCOPE, audit_factory=create_project_audit
        )
        now = datetime(2026, 9, 5)

        def command(staging_key: str) -> ReserveUpload:
            return ReserveUpload(
                filename="project.md",
                media_type="text/markdown",
                source_content_hash="sha256:" + "a" * 64,
                size=12,
                now=now,
                staging_key=staging_key,
            )

        reserved = await first.reserve_upload(command("staging:first"))
        other = await second.reserve_upload(command("staging:second"))
        assert reserved.document_id != other.document_id
        with pytest.raises(JobLeaseLost):
            await second.activate_upload(reserved, ArtifactLocator("blob:wrong"))
        record = await first.activate_upload(reserved, ArtifactLocator("blob:first"))
        assert await second.get_document(record.document_id) is None
        assert (await second.list_documents(None, 20)).items == ()
        assert await second.load_ready_revisions((record.document_id,)) == ()
        assert (
            await second.claim_jobs(
                worker_id="other", now=now, lease_duration=timedelta(seconds=30), limit=5
            )
            == ()
        )
        claimed = await first.claim_jobs(
            worker_id="first", now=now, lease_duration=timedelta(seconds=30), limit=5
        )
        assert len(claimed) == 1
        assert claimed[0].job_id == record.job_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_document_project_scope_retains_only_its_answers_and_citations(
    project_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from sqlalchemy import select, update

    from tap.modules.knowledge.adapters import mysql_documents as ledger
    from tap.modules.knowledge.ports.answers import (
        AnswerSnapshot,
        CitationSnapshot,
        DocumentStateChanged,
        ReadyDocumentRevision,
    )
    from tap.platform.db.project_scope import scope_values

    monkeypatch.setattr(ledger, "ANSWER_SNAPSHOT_RETENTION", 1)
    engine, sessions = create_engine_and_session_factory(project_database)
    try:
        async with sessions.begin() as session:
            await session.execute(
                insert(project).values(
                    project_id="other-project", enterprise_id="local", enabled=True
                )
            )
        repositories = [
            MysqlDocumentRepository(sessions, scope=scope, audit_factory=create_project_audit)
            for scope in (VALIDATION_SCOPE, OTHER_SCOPE)
        ]
        selections = []
        for index, repository in enumerate(repositories):
            reserved = await repository.reserve_upload(
                ReserveUpload(
                    filename="ready.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + str(index) * 64,
                    size=12,
                    now=datetime(2026, 9, 5),
                    staging_key=f"staging:{index}",
                )
            )
            record = await repository.activate_upload(reserved, ArtifactLocator(f"blob:{index}"))
            anchor = json.dumps(
                {
                    "type": "document",
                    "headingPath": ["Scope"],
                    "startOffset": 0,
                    "endOffset": 12,
                    "textPreview": "bounded text",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            async with sessions.begin() as session:
                await session.execute(
                    update(ledger.knowledge_document)
                    .where(ledger.knowledge_document.c.document_id == record.document_id)
                    .values(status="ready", stage="ready")
                )
                await session.execute(
                    insert(ledger.knowledge_chunk_manifest).values(
                        **scope_values(repository.scope),
                        chunk_id=f"scope-chunk-{index}",
                        logical_chunk_id=f"scope-logical-{index}",
                        revision_id=record.revision_id,
                        ordinal=0,
                        root_id=record.document_id,
                        parent_id=None,
                        anchor_json=json.loads(anchor),
                        chunk_content_hash="sha256:" + "a" * 64,
                        embedding_model_version="test",
                        index_version="test",
                        created_at=datetime(2026, 9, 5),
                    )
                )
            selected = ReadyDocumentRevision(
                record.document_id, record.revision_id, record.source_content_hash
            )
            selections.append(selected)
            snapshot = AnswerSnapshot(
                trace_id=f"scope-answer-{index}",
                query_hash="sha256:" + "b" * 64,
                selected_revisions=(selected,),
                citations=(
                    CitationSnapshot(
                        citation_id=f"scope-citation-{index}",
                        trace_id=f"scope-answer-{index}",
                        document_id=record.document_id,
                        revision_id=record.revision_id,
                        chunk_id=f"scope-chunk-{index}",
                        source_content_hash=record.source_content_hash,
                        chunk_content_hash="sha256:" + "a" * 64,
                        anchor_json=anchor,
                    ),
                ),
            )
            await repository.save_answer_with_citations(snapshot)
        assert await repositories[0].load_citation("scope-citation-1") is None
        assert await repositories[1].load_citation("scope-citation-0") is None
        with pytest.raises(DocumentStateChanged):
            await repositories[1].save_answer_with_citations(
                AnswerSnapshot(
                    trace_id="wrong-project",
                    query_hash="sha256:" + "b" * 64,
                    selected_revisions=(selections[0],),
                    citations=(),
                )
            )
        await repositories[0].save_answer_with_citations(
            AnswerSnapshot(
                trace_id="replacement",
                query_hash="sha256:" + "b" * 64,
                selected_revisions=(selections[0],),
                citations=(),
            )
        )
        async with sessions() as session:
            answers = set(
                await session.scalars(select(ledger.knowledge_answer_snapshot.c.trace_id))
            )
        assert answers == {"replacement", "scope-answer-1"}
        assert await repositories[0].load_citation("scope-citation-0") is None
        assert await repositories[1].load_citation("scope-citation-1") is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_document_project_scope_retains_owned_history_and_refuses_orphan_citation(
    project_database: str,
) -> None:
    from apps.backend.tests.integration.test_citation_snapshot_transaction import (
        seed_ready,
        snapshot,
    )
    from sqlalchemy import select, update
    from sqlalchemy.exc import IntegrityError

    from tap.modules.knowledge.adapters.mysql_documents import (
        knowledge_citation_snapshot,
        knowledge_document,
        knowledge_document_revision,
    )

    engine, sessions = create_engine_and_session_factory(project_database)
    repository = MysqlDocumentRepository(
        sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
    )
    other = MysqlDocumentRepository(sessions, scope=OTHER_SCOPE, audit_factory=create_project_audit)
    try:
        selected = await seed_ready(engine, "retained")
        await repository.save_answer_with_citations(
            snapshot("retained", selected, with_citation=True)
        )
        async with sessions.begin() as session:
            original_revision = dict(
                (await session.execute(select(knowledge_document_revision))).mappings().one()
            )
            original_citation = dict(
                (await session.execute(select(knowledge_citation_snapshot))).mappings().one()
            )
            await session.execute(
                insert(knowledge_document_revision).values(
                    **{
                        **original_revision,
                        "revision_id": "rev_latest",
                        "parser_version": "parser-v2",
                    }
                )
            )
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == selected.document_id)
                .values(current_revision_id="rev_latest")
            )
        lookup = await repository.load_citation("citation-retained")
        assert lookup is not None
        assert lookup.citation.revision_id == selected.revision_id
        assert lookup.document is not None
        assert lookup.document.current_revision_id == "rev_latest"
        assert lookup.manifest is not None
        assert await repository.citation_is_current(lookup.citation) is False
        assert await other.load_citation("citation-retained") is None
        # Supply a real Source identity: the rejection must be the ownership FK,
        # not a missing required column before that constraint is exercised.
        with pytest.raises(IntegrityError) as rejected:
            async with sessions.begin() as session:
                await session.execute(
                    insert(knowledge_citation_snapshot).values(
                        **{
                            **original_citation,
                            "citation_id": "orphan-citation",
                            "revision_id": "missing-revision",
                        }
                    )
                )
        assert rejected.value.orig.args[0] == 1452
        async with sessions() as session:
            retained_revision = dict(
                (
                    await session.execute(
                        select(knowledge_document_revision).where(
                            knowledge_document_revision.c.revision_id == selected.revision_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            retained_citations = (
                (await session.execute(select(knowledge_citation_snapshot))).mappings().all()
            )
        assert retained_revision == original_revision
        assert retained_citations == [original_citation]
    finally:
        await engine.dispose()
