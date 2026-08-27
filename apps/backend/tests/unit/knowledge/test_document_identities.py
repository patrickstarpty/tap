"""Behavioral tests for opaque document identities and normalized values."""

from __future__ import annotations

from hashlib import sha256

import pytest

from tap.modules.knowledge.domain.documents import (
    MAX_NORMALIZED_CHARACTERS,
    MAX_UPLOAD_BYTES,
    DocumentId,
    DocumentParseRejected,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    new_document_id,
    revision_id_for,
)


def test_new_document_id_is_opaque_and_not_derived_from_uploaded_content() -> None:
    """Changing upload bytes must not cause a caller-selected document identity."""
    first = new_document_id(lambda: "generated-upload-a")
    second = new_document_id(lambda: "generated-upload-b")

    assert first == DocumentId("doc_" + sha256(b"generated-upload-a").hexdigest()[:32])
    assert first != second
    assert "Refund" not in first


def test_revision_and_chunk_ids_are_stable_but_version_sensitive() -> None:
    """Omitting parser/chunker versions would make stale citations silently resolve."""
    source_hash = canonical_sha256(b"# Refunds\nTwo approvers are required.")
    document_id = DocumentId("doc_01JTESTDOCUMENT000000000000")
    first = revision_id_for(document_id, source_hash, "parser-v1")
    second = revision_id_for(document_id, source_hash, "parser-v2")

    assert first == revision_id_for(document_id, source_hash, "parser-v1")
    assert first != second
    assert chunk_id_for(first, '{"type":"document"}', source_hash) == chunk_id_for(
        first, '{"type":"document"}', source_hash
    )
    assert chunk_id_for(first, '{"type":"document"}', source_hash) != chunk_id_for(
        second, '{"type":"document"}', source_hash
    )


def test_streamed_source_hash_accepts_exact_upload_limit_without_buffering_contract_change() -> (
    None
):
    """Rejecting a legal 25 MiB stream would contradict the upload boundary."""
    piece = b"x" * (1024 * 1024)
    expected = "sha256:" + sha256(piece * 25).hexdigest()

    assert canonical_sha256(piece for _ in range(25)) == expected
    assert MAX_UPLOAD_BYTES == 25 * 1024 * 1024


def test_artifact_rejects_normalized_text_over_hard_limit() -> None:
    """Allowing an unbounded normalized artifact would bypass worker memory limits."""
    text = "x" * (MAX_NORMALIZED_CHARACTERS + 1)
    with pytest.raises(DocumentParseRejected, match="document-too-complex"):
        NormalizedArtifact(
            filename="large.txt",
            media_type=MediaType.TEXT,
            source_hash=canonical_sha256(text.encode()),
            blocks=(
                NormalizedBlock(
                    block_id="b_1",
                    kind="paragraph",
                    text=text,
                    heading_path=(),
                    page=None,
                    paragraph_index=0,
                    start_offset=0,
                    end_offset=len(text),
                ),
            ),
            document_id=DocumentId("doc_01JTESTDOCUMENT000000000000"),
            revision_id=RevisionId("rev_test"),
        )


def test_canonical_hash_uses_public_sha256_prefix() -> None:
    """A digest without the public canonical prefix would fail provenance validation."""
    assert canonical_sha256(b"abc") == (
        "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
