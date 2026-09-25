"""Same canonical Knowledge artifact journey for Azure and composed object storage."""

import pytest

from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    BlockKind,
    DocumentId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
    revision_id_for,
)
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
    parser_config_digest,
)
from tap.modules.knowledge.ports.documents import DeletionTarget
from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure

PAYLOAD = b"Tapper policy."
SOURCE_HASH = canonical_sha256(PAYLOAD)
DOCUMENT_ID = DocumentId("conformance_doc")
REVISION = str(revision_id_for(DOCUMENT_ID, SOURCE_HASH, PARSER_VERSION))


class Upload:
    filename = "policy.md"
    media_type = "text/markdown"

    @property
    def content(self):
        async def stream():
            yield PAYLOAD

        return stream()


def normalized_artifact():
    inventory_item = ParseInventoryItem.create(
        source_revision_id=REVISION,
        kind=ParseInventoryKind.PARAGRAPH,
        locator="paragraph:0",
        status=ParseInventoryStatus.PARSED,
        artifact_digest=SOURCE_HASH,
    )
    inventory = (inventory_item,)
    return NormalizedArtifact(
        filename="policy.md",
        media_type=MediaType.MARKDOWN,
        source_hash=SOURCE_HASH,
        document_id=DOCUMENT_ID,
        revision_id=RevisionId(REVISION),
        blocks=(
            NormalizedBlock(
                block_id="block-1",
                kind=BlockKind.PARAGRAPH,
                text="Tapper policy.",
                heading_path=("Policy",),
                page=None,
                paragraph_index=0,
                start_offset=0,
                end_offset=14,
                inventory_item_id=inventory_item.item_id,
            ),
        ),
        parse_inventory=inventory,
        parser_config_digest=parser_config_digest(MediaType.MARKDOWN.value),
        parse_inventory_digest=parse_inventory_digest(inventory),
    )


async def exercise_artifact_round_trip(store):
    from tap.modules.knowledge.domain.documents import (
        ChunkDraft,
        chunk_id_for,
        logical_chunk_id_for,
    )
    from tap.modules.knowledge.ports.documents import EmbeddingArtifact

    anchor = '{"blockId":"block-1"}'
    content_hash = canonical_sha256(PAYLOAD)
    chunks = (
        ChunkDraft(
            chunk_id=chunk_id_for(RevisionId(REVISION), anchor, content_hash),
            logical_chunk_id=logical_chunk_id_for(DOCUMENT_ID, anchor),
            root_id=DOCUMENT_ID,
            parent_id=None,
            content=PAYLOAD.decode(),
            anchor_json=anchor,
            source_content_hash=SOURCE_HASH,
            chunk_content_hash=content_hash,
        ),
    )
    vectors = EmbeddingArtifact(
        "tapper-embedding", 3, ((0.1, 0.2, 0.3),), tuple(str(chunk.chunk_id) for chunk in chunks)
    )
    staged = await store.stage_original(Upload(), max_bytes=1024)
    original = await store.commit_original(staged, REVISION)
    normalized = await store.write_normalized(REVISION, normalized_artifact())
    chunk_ref = await store.write_chunks(REVISION, chunks)
    embedding_ref = await store.write_embeddings(REVISION, vectors, source_content_hash=SOURCE_HASH)
    assert await store.read_original(original) == PAYLOAD
    excerpt = PAYLOAD[7:13]
    assert (
        await store.read_original_excerpt(
            original,
            revision_id=REVISION,
            source_digest=SOURCE_HASH,
            start_byte=7,
            end_byte=13,
            excerpt_digest=canonical_sha256(excerpt),
        )
        == excerpt
    )
    with pytest.raises(ArtifactIntegrityFailure):
        await store.read_original_excerpt(
            original,
            revision_id=REVISION,
            source_digest=SOURCE_HASH,
            start_byte=7,
            end_byte=13,
            excerpt_digest="sha256:" + "0" * 64,
        )
    assert await store.read_normalized(normalized) == normalized_artifact()
    assert await store.read_chunks(chunk_ref) == chunks
    assert await store.read_embeddings(embedding_ref) == vectors
    assert await store.write_normalized(REVISION, normalized_artifact()) == normalized
    assert await store.write_chunks(REVISION, chunks) == chunk_ref
    assert (
        await store.write_embeddings(REVISION, vectors, source_content_hash=SOURCE_HASH)
        == embedding_ref
    )
    with pytest.raises(ArtifactIntegrityFailure):
        await store.delete_revision_artifacts(
            DeletionTarget(str(DOCUMENT_ID), "foreign_revision", (), (normalized,))
        )
    assert await store.read_normalized(normalized) == normalized_artifact()
    await store.discard_staged(staged)
    await store.delete_revision_artifacts(
        DeletionTarget(
            str(DOCUMENT_ID), REVISION, (), (original, normalized, chunk_ref, embedding_ref)
        )
    )
    with pytest.raises(ArtifactIntegrityFailure):
        await store.read_original(original)
