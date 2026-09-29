import pytest

from tap.modules.knowledge.domain.managed_chunks import (
    ChunkSettings,
    generate_chunks,
    new_chunk,
    split_text,
)


def test_separator_overlap_and_cleaning_are_shared_by_preview_and_processing():
    settings = ChunkSettings(maxLength=8, overlap=2, separator="\n\n")
    assert split_text("abcdefghijk", settings) == ["abcdefgh", "ghijk"]
    cleaned = ChunkSettings(maxLength=100, overlap=0, replaceWhitespace=True, removeUrls=True)
    assert split_text("a   b https://example.com x@y.com", cleaned) == ["a b"]


def test_parent_child_preserves_parent_and_has_smaller_children():
    settings = ChunkSettings(mode="parent_child", parentMode="full_doc", childMaxLength=5)
    chunks = generate_chunks("abcdefghijk", settings)
    assert chunks[0]["content"] == "abcdefghijk"
    assert [c["content"] for c in chunks[0]["children"]] == ["abcde", "fghij", "k"]


def test_overlap_must_be_less_than_maximum():
    with pytest.raises(ValueError):
        ChunkSettings(maxLength=4, overlap=4)


@pytest.mark.asyncio
async def test_change_saves_pending_without_calling_index(monkeypatch):
    import tiktoken

    from tap.modules.knowledge.adapters.mysql_managed_chunks import MysqlManagedChunks

    manager = object.__new__(MysqlManagedChunks)
    manager.encoding = tiktoken.get_encoding("cl100k_base")
    chunk = new_chunk("original text", 1)
    state = dict(
        version=1, chunks_json=[chunk], settings_json={}, index_status="ready", index_error=None
    )

    async def ensure(document_id):
        return state

    async def save(document_id, version, chunks, settings):
        state.update(chunks_json=chunks, index_status="pending")

    async def forbidden(document_id):
        pytest.fail("HTTP mutation must leave indexing to the durable worker")

    monkeypatch.setattr(manager, "_ensure", ensure)
    monkeypatch.setattr(manager, "_save", save)
    monkeypatch.setattr(manager, "process_document", forbidden)
    result = await manager.change("doc", chunk["chunkId"], 1, content="saved edit")
    assert result["content"] == "saved edit"
    assert result["indexStatus"] == "pending"
