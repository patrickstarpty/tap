"""Provider-neutral Athena artifact integrity contract."""

from __future__ import annotations

import gzip
import json

import pytest

from tap.modules.knowledge.adapters.blob_artifacts import (
    ARTIFACTS_CONTAINER,
    ORIGINALS_CONTAINER,
    ArtifactIntegrityError,
    artifact_locator,
    decode_chunks_artifact,
    decode_embeddings_artifact,
    decode_normalized_artifact,
    encode_chunks_artifact,
    encode_embeddings_artifact,
    encode_normalized_artifact,
)
from tap.modules.knowledge.domain.documents import (
    BlockKind,
    ChunkDraft,
    DocumentId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    canonical_sha256,
)
from tap.modules.knowledge.ports.documents import EmbeddingArtifact

REVISION = "rev_task5_contract"
SOURCE_HASH = "sha256:" + "a" * 64


def normalized_artifact() -> NormalizedArtifact:
    return NormalizedArtifact(
        filename="policy.md",
        media_type=MediaType.MARKDOWN,
        source_hash=SOURCE_HASH,
        document_id=DocumentId("doc_a"),
        revision_id=REVISION,  # type: ignore[arg-type]
        blocks=(
            NormalizedBlock(
                block_id="block-1",
                kind=BlockKind.PARAGRAPH,
                text="Athena policy.",
                heading_path=("Policy",),
                page=None,
                paragraph_index=0,
                start_offset=0,
                end_offset=14,
            ),
        ),
    )


def chunk_artifact() -> tuple[ChunkDraft, ...]:
    content = "Athena policy."
    return (
        ChunkDraft(
            chunk_id="h_" + "1" * 64,  # type: ignore[arg-type]
            logical_chunk_id="lc_" + "2" * 64,  # type: ignore[arg-type]
            root_id=DocumentId("doc_a"),
            parent_id=None,
            content=content,
            anchor_json='{"blockId":"block-1"}',
            source_content_hash=SOURCE_HASH,
            chunk_content_hash=canonical_sha256(content.encode()),
        ),
    )


def test_canonical_artifact_envelopes_are_deterministic_and_round_trip_exactly() -> None:
    """Noncanonical or timestamped encoding would break content-addressed retries."""
    normalized = normalized_artifact()
    chunks = chunk_artifact()
    embeddings = EmbeddingArtifact("athena-embedding", 3, ((0.1, 0.2, 0.3),))

    normalized_bytes = encode_normalized_artifact(REVISION, normalized)
    chunks_bytes = encode_chunks_artifact(REVISION, chunks)
    embedding_bytes = encode_embeddings_artifact(REVISION, SOURCE_HASH, embeddings)

    assert normalized_bytes.endswith(b"\n")
    assert chunks_bytes == encode_chunks_artifact(REVISION, chunks)
    assert embedding_bytes == encode_embeddings_artifact(REVISION, SOURCE_HASH, embeddings)
    assert gzip.decompress(chunks_bytes).endswith(b"\n")
    assert gzip.decompress(embedding_bytes).endswith(b"\n")
    assert decode_normalized_artifact(normalized_bytes, expected_revision=REVISION) == normalized
    assert decode_chunks_artifact(chunks_bytes, expected_revision=REVISION) == chunks
    assert decode_embeddings_artifact(embedding_bytes, expected_revision=REVISION) == embeddings


@pytest.mark.parametrize("kind", ("normalized", "chunks", "embeddings"))
def test_artifact_reads_reject_payload_hash_or_envelope_widening(kind: str) -> None:
    """A syntactically valid but rebound artifact must not cross the provider port."""
    if kind == "normalized":
        raw = encode_normalized_artifact(REVISION, normalized_artifact())
        envelope = json.loads(raw)
        envelope["payloadSha256"] = "sha256:" + "f" * 64
        corrupted = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
        decoder = decode_normalized_artifact
    elif kind == "chunks":
        raw = gzip.decompress(encode_chunks_artifact(REVISION, chunk_artifact()))
        lines = raw.splitlines()
        envelope = json.loads(lines[0])
        envelope["extra"] = True
        corrupted = gzip.compress(
            (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
            + b"\n".join(lines[1:])
            + b"\n",
            mtime=0,
        )
        decoder = decode_chunks_artifact
    else:
        artifact = EmbeddingArtifact("athena-embedding", 3, ((0.1, 0.2, 0.3),))
        raw = gzip.decompress(encode_embeddings_artifact(REVISION, SOURCE_HASH, artifact))
        lines = raw.splitlines()
        envelope = json.loads(lines[0])
        envelope["payloadSha256"] = "sha256:" + "e" * 64
        corrupted = gzip.compress(
            (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
            + b"\n".join(lines[1:])
            + b"\n",
            mtime=0,
        )
        decoder = decode_embeddings_artifact

    with pytest.raises(ArtifactIntegrityError):
        decoder(corrupted, expected_revision=REVISION)


def test_artifact_locators_are_closed_identity_only_values() -> None:
    """Credentials or SAS query text in a durable locator would leak provider authority."""
    original = artifact_locator(
        ORIGINALS_CONTAINER,
        f"revisions/{REVISION}/{SOURCE_HASH.removeprefix('sha256:')}",
    )
    artifact = artifact_locator(
        ARTIFACTS_CONTAINER,
        f"revisions/{REVISION}/normalized-v1.json",
    )

    assert str(original).startswith("athena-originals/")
    assert str(artifact).startswith("athena-artifacts/")
    for invalid in (
        "athena-originals/blob?sig=secret",
        "https://127.0.0.1/blob",
        "other/blob",
        "athena-artifacts/../escape",
    ):
        with pytest.raises(ValueError):
            artifact_locator(*invalid.split("/", 1))


def test_chunk_artifact_rejects_semantic_hash_rebinding_with_valid_envelope_hash() -> None:
    """Rehashing a malicious envelope cannot make a false chunk provenance fact valid."""
    decoded = gzip.decompress(encode_chunks_artifact(REVISION, chunk_artifact()))
    lines = decoded.splitlines()
    header = json.loads(lines[0])
    row = json.loads(lines[1])
    row["chunkContentHash"] = "sha256:" + "f" * 64
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    header["payloadSha256"] = canonical_sha256(payload)
    corrupted = gzip.compress(
        (json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n").encode() + payload,
        mtime=0,
    )

    with pytest.raises(ArtifactIntegrityError):
        decode_chunks_artifact(corrupted, expected_revision=REVISION)
