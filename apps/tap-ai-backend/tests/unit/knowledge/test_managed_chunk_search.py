from dataclasses import replace

import pytest

from tap.modules.knowledge.adapters.managed_chunk_search import collapse_parent_groups
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.models import SearchHit


def hit(identity, score, rank):
    return SearchHit(
        SourceFamily.DOC,
        identity,
        "logical-" + identity,
        None,
        "identical parent text",
        SourceRevisionRef(
            "source",
            "doc",
            RevisionKind.BLOB_VERSION,
            "revision",
            "sha256:" + "a" * 64,
            DocumentAnchor(
                heading_path=("Managed knowledge", "Chunk 1"), start_offset=0, end_offset=21
            ),
        ),
        "sha256:" + "b" * 64,
        ContentRole.SOURCE,
        IndexRevision("index", "schema", "corpus"),
        "embedding",
        score,
        rank,
    )


def test_deduplicates_trusted_parent_identity_and_preserves_highest_score():
    low, high, other = hit("child-a", 0.3, 1), hit("child-b", 0.9, 2), hit("child-c", 0.8, 3)
    groups = {
        "child-a": ("doc", "revision", "parent1"),
        "child-b": ("doc", "revision", "parent1"),
        "child-c": ("doc", "revision", "parent2"),
    }
    actual = collapse_parent_groups((low, high, other), groups)
    assert [item.chunk_id for item in actual] == ["child-b", "child-c"]
    assert [item.local_rank for item in actual] == [1, 2]
    assert actual[0].score == 0.9


def test_identical_text_or_forged_headings_never_merge_unrelated_parents():
    first, second = hit("one", 0.9, 1), hit("two", 0.8, 2)
    assert len(collapse_parent_groups((first, second), {})) == 2
    assert collapse_parent_groups((first, second), {"one": None}) == (
        replace(second, local_rank=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", ["doc-schema-v1", "doc-schema-v2"])
async def test_authority_uses_canonical_source_after_provider_normalization(schema):
    from unittest.mock import AsyncMock, MagicMock

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.knowledge.adapters.mysql_managed_chunks import MysqlManagedChunks

    row = dict(
        chunk_id="child-a",
        revision_id="revision",
        parent_id="managed_child",
        document_id="document",
        source_id="source",
        current_revision_id="revision",
        status="ready",
        deleted_at=None,
        chunk_count=1,
        index_status="ready",
        settings_json={"mode": "parent_child"},
        chunks_json=[
            {
                "chunkId": "parent",
                "enabled": True,
                "children": [{"chunkId": "child", "enabled": True}],
            }
        ],
    )
    result = MagicMock()
    result.mappings.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result
    session.__aenter__.return_value = session
    manager = object.__new__(MysqlManagedChunks)
    manager.scope = VALIDATION_SCOPE
    manager.sessions = lambda: session
    normalized = replace(
        hit("child-a", 0.9, 1), index_revision=IndexRevision("index", schema, "corpus")
    )
    groups = await manager.parent_groups((normalized,))
    assert groups == {"child-a": ("document", "revision", "parent")}
    wrong_source = replace(normalized, source=replace(normalized.source, source_id="document"))
    assert await manager.parent_groups((wrong_source,)) == {"child-a": None}
