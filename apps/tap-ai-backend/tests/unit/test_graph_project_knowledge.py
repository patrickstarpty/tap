from __future__ import annotations

import pytest

from tap.contracts.http import PublishedKnowledgeSource, PublishedKnowledgeSourcePage
from tap.entrypoints.graph_project_knowledge import MysqlCurrentRevisions
from tap.modules.access.adapters.validation import VALIDATION_SCOPE


def _page(count: int) -> PublishedKnowledgeSourcePage:
    return PublishedKnowledgeSourcePage(
        items=[
            PublishedKnowledgeSource(
                source_id="src_" + f"{i:032d}",
                document_id=f"doc_{i}",
                revision_id=f"rev_{i}",
                source_name=f"source-{i}",
                filename=f"file-{i}.md",
                publication_id=None,
                expires_at=None,
                approved_item_count=0,
                inventory_item_count=1,
                partial=False,
            )
            for i in range(count)
        ]
    )


class _FakeReadySources:
    def __init__(self, page: PublishedKnowledgeSourcePage) -> None:
        self._page = page

    async def list_sources(self) -> PublishedKnowledgeSourcePage:
        return self._page


@pytest.mark.asyncio
async def test_current_revision_ids_logs_a_warning_exactly_at_the_cap(caplog):
    revisions = MysqlCurrentRevisions(sessions=None, scope=VALIDATION_SCOPE)  # type: ignore[arg-type]
    revisions._ready_sources = _FakeReadySources(_page(100))

    with caplog.at_level("WARNING"):
        ids = await revisions.current_revision_ids(VALIDATION_SCOPE)

    assert len(ids) == 100
    assert any(
        VALIDATION_SCOPE.project_id in record.message and "100" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_current_revision_ids_does_not_warn_below_the_cap(caplog):
    revisions = MysqlCurrentRevisions(sessions=None, scope=VALIDATION_SCOPE)  # type: ignore[arg-type]
    revisions._ready_sources = _FakeReadySources(_page(3))

    with caplog.at_level("WARNING"):
        ids = await revisions.current_revision_ids(VALIDATION_SCOPE)

    assert len(ids) == 3
    assert caplog.records == []
