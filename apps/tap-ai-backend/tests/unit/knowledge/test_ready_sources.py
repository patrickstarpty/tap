"""Direct availability never invents an approval or exposes disabled documents."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources


@pytest.mark.asyncio
async def test_ready_sources_reports_current_revision_without_fake_publication():
    rows = MagicMock()
    rows.mappings.return_value.all.return_value = [
        {
            "source_id": "src_" + "a" * 32,
            "document_id": "document",
            "current_revision_id": "revision",
            "source_name": "Guide",
            "filename": "guide.md",
            "chunk_count": 3,
        }
    ]
    session = AsyncMock()
    session.execute.return_value = rows
    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value = session
    result = await MysqlReadySources(sessions, VALIDATION_SCOPE).list_sources()
    assert result.items[0].publication_id is None
    assert result.items[0].expires_at is None
    assert result.items[0].approved_item_count == 0
    assert result.items[0].revision_id == "revision"
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "knowledge_document.status = 'ready'" in sql
    assert "knowledge_document.chunk_count > 0" in sql
    assert "knowledge_source.deleted_at IS NULL" in sql
    assert "knowledge_document.deleted_at IS NULL" in sql
    assert "knowledge_document.project_id" in sql
