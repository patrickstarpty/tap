"""Re-parse queues a new revision of a ready legacy PDF and keeps the old revision."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, insert, select, update

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    MysqlDocumentRepository,
    knowledge_document,
    knowledge_document_revision,
    knowledge_ingestion_job,
    knowledge_source,
)
from tap.modules.knowledge.domain.documents import PARSER_VERSION, DocumentId, revision_id_for
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import outbox
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 11, 9, tzinfo=UTC).replace(tzinfo=None)
SOURCE = "src_" + "3" * 32
PDF = "application/pdf"


async def seed(sessions, number: int, media_type: str, parser_version: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    document_id = f"doc_{number:032x}"
    source_hash = "sha256:" + f"{number:064x}"
    revision_id = str(revision_id_for(DocumentId(document_id), source_hash, parser_version))
    async with sessions() as session, session.begin():
        await session.execute(
            insert(knowledge_document).values(
                **scope_values(VALIDATION_SCOPE),
                document_id=document_id,
                source_id=SOURCE,
                filename=f"terms-{number}.pdf" if media_type == PDF else f"terms-{number}.md",
                media_type=media_type,
                current_revision_id=None,
                source_content_hash=source_hash,
                dedupe_key=source_hash,
                reservation_parser_version=parser_version,
                reservation_chunker_version="tapper-structure-512-v1",
                reservation_pipeline_version="tapper-ingestion-v1",
                status="ready",
                stage="ready",
                chunk_count=3,
                activated_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(knowledge_document_revision).values(
                **scope_values(VALIDATION_SCOPE),
                revision_id=revision_id,
                source_id=SOURCE,
                document_id=document_id,
                source_content_hash=source_hash,
                original_blob_locator=f"tapper-originals/{number}",
                normalized_blob_locator=f"tapper-artifacts/{number}.normalized",
                chunks_blob_locator=f"tapper-artifacts/{number}.chunks",
                embeddings_blob_locator=f"tapper-artifacts/{number}.embeddings",
                parser_version=parser_version,
                chunker_version="tapper-structure-512-v1",
                pipeline_version="tapper-ingestion-v1",
                created_at=NOW,
            )
        )
        await session.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == document_id)
            .values(current_revision_id=revision_id)
        )
    return document_id, revision_id


async def test_reparse_queues_a_new_revision_only_for_ready_legacy_pdfs(owned_project_mysql):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        async with sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_source).values(
                    **scope_values(VALIDATION_SCOPE),
                    source_id=SOURCE,
                    name="terms",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        legacy_pdf, legacy_revision = await seed(sessions, 1, PDF, "tapper-parser-v1")
        markdown, markdown_revision = await seed(sessions, 2, "text/markdown", "tapper-parser-v1")
        current_pdf, current_revision = await seed(sessions, 3, PDF, PARSER_VERSION)
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )

        assert await repository.reparse_stale_revisions(media_types=frozenset({PDF}), limit=10) == (
            1,
            0,
        )
        assert await repository.reparse_stale_revisions(media_types=frozenset({PDF}), limit=10) == (
            0,
            0,
        )

        new_revision = str(
            revision_id_for(DocumentId(legacy_pdf), "sha256:" + f"{1:064x}", PARSER_VERSION)
        )
        async with sessions() as session:
            documents = {
                row.document_id: (row.current_revision_id, row.status, row.stage)
                for row in await session.execute(select(knowledge_document))
            }
            revisions = {
                row.revision_id: row
                for row in await session.execute(select(knowledge_document_revision))
            }
            jobs = (
                await session.execute(
                    select(knowledge_ingestion_job).where(
                        knowledge_ingestion_job.c.revision_id == new_revision
                    )
                )
            ).all()
            requested = await session.scalar(
                select(func.count())
                .select_from(outbox)
                .where(
                    outbox.c.aggregate_id == legacy_pdf,
                    outbox.c.message_type == "knowledge.ingestion_requested",
                )
            )

        assert documents == {
            legacy_pdf: (new_revision, "queued", "stored"),
            markdown: (markdown_revision, "ready", "ready"),
            current_pdf: (current_revision, "ready", "ready"),
        }
        assert revisions[new_revision].parser_version == PARSER_VERSION
        assert revisions[new_revision].original_blob_locator == "tapper-originals/1"
        assert revisions[new_revision].chunks_blob_locator is None
        old = revisions[legacy_revision]
        assert (old.parser_version, old.chunks_blob_locator, old.embeddings_blob_locator) == (
            "tapper-parser-v1",
            "tapper-artifacts/1.chunks",
            "tapper-artifacts/1.embeddings",
        )
        assert [(job.kind, job.status, job.stage, job.attempt) for job in jobs] == [
            ("ingestion", "pending", "stored", 1)
        ]
        assert requested == 1
    finally:
        await engine.dispose()
