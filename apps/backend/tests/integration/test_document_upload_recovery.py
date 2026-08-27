from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

from sqlalchemy import text

from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload
from tap.platform.db.session import create_engine_and_session_factory

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)


def test_claimed_job_survives_repository_reconstruction() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM outbox WHERE aggregate_type='knowledge_document'")
            )
            await connection.execute(text("DELETE FROM knowledge_ingestion_job"))
            await connection.execute(text("UPDATE knowledge_document SET current_revision_id=NULL"))
            await connection.execute(text("DELETE FROM knowledge_document_revision"))
            await connection.execute(text("DELETE FROM knowledge_document"))
        try:
            first_repository = MysqlDocumentRepository(sessions)
            reservation = await first_repository.reserve_upload(
                ReserveUpload(
                    filename="restart.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "f" * 64,
                    size=12,
                    now=datetime(2026, 8, 27, 9, 0),
                )
            )
            created = await first_repository.activate_upload(
                reservation, ArtifactLocator("blob:restart")
            )
            del first_repository

            claimed = await MysqlDocumentRepository(sessions).claim_jobs(
                worker_id="worker-b",
                now=datetime(2026, 8, 27, 9, 1),
                lease_duration=timedelta(seconds=30),
                limit=10,
            )
            assert [job.job_id for job in claimed] == [created.job_id]
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM outbox WHERE aggregate_type='knowledge_document'")
                )
                await connection.execute(text("DELETE FROM knowledge_ingestion_job"))
                await connection.execute(
                    text("UPDATE knowledge_document SET current_revision_id=NULL")
                )
                await connection.execute(text("DELETE FROM knowledge_document_revision"))
                await connection.execute(text("DELETE FROM knowledge_document"))
            await engine.dispose()

    asyncio.run(scenario())
