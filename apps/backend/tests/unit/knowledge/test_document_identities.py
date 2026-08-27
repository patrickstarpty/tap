"""Behavioral tests for opaque document identities and normalized values."""

from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from tap.modules.knowledge.domain.documents import (
    MAX_NORMALIZED_CHARACTERS,
    MAX_UPLOAD_BYTES,
    DocumentId,
    DocumentParseRejected,
    DocumentSource,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    new_document_id,
    revision_id_for,
)


def test_new_document_id_is_opaque_and_not_derived_from_uploaded_content() -> None:
    """Changing upload bytes must not cause a caller-selected document identity."""
    first = new_document_id(lambda: "generated-upload-a")
    second = new_document_id(lambda: "generated-upload-b")

    assert first == new_document_id(lambda: "generated-upload-a")
    assert first.startswith("doc_")
    assert len(first) == 36
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


def test_document_source_enforces_upload_limit_before_any_parser_can_run() -> None:
    """Letting an oversized source exist would bypass the hard upload boundary."""
    accepted = DocumentSource("policy.txt", MediaType.TEXT, b"x" * MAX_UPLOAD_BYTES)

    assert len(accepted.content) == MAX_UPLOAD_BYTES
    with pytest.raises(DocumentParseRejected, match="^document-too-large$"):
        DocumentSource("policy.txt", MediaType.TEXT, b"x" * (MAX_UPLOAD_BYTES + 1))


def test_stable_ids_frame_embedded_separator_tuples_without_collisions() -> None:
    """NUL-delimited identity fields must not let distinct document locations collide."""
    first_hash = "sha256:" + "1" * 64
    second_hash = "sha256:" + "2" * 64

    assert revision_id_for(DocumentId(f"doc\0{second_hash}"), first_hash, "v") != revision_id_for(
        DocumentId("doc"), second_hash, f"{first_hash}\0v"
    )
    assert logical_chunk_id_for(DocumentId("doc\0anchor"), "tail") != logical_chunk_id_for(
        DocumentId("doc"), "anchor\0tail"
    )
    assert chunk_id_for(RevisionId("rev\0anchor"), second_hash, first_hash) != chunk_id_for(
        RevisionId("rev"), f"anchor\0{second_hash}", first_hash
    )


def test_revision_id_rejects_an_empty_parser_version() -> None:
    """A missing parser version would make revisions insensitive to parser behavior."""
    with pytest.raises(ValueError, match="parser version"):
        revision_id_for(
            DocumentId("doc_01JTESTDOCUMENT000000000000"),
            "sha256:" + "0" * 64,
            "",
        )


def test_stable_ids_are_repeatable_across_python_processes() -> None:
    """Process-local state or randomized hashing would make persisted IDs unrecoverable."""
    source_hash = "sha256:" + "a" * 64
    local = {
        "revision": str(revision_id_for(DocumentId("doc_stable"), source_hash, "parser-v1")),
        "logical": str(logical_chunk_id_for(DocumentId("doc_stable"), '{"type":"document"}')),
        "chunk": str(
            chunk_id_for(
                RevisionId("rev_stable"),
                '{"type":"document"}',
                "sha256:" + "b" * 64,
            )
        ),
    }
    program = """
import json
from tap.modules.knowledge.domain.documents import (
    ChunkId, DocumentId, RevisionId, chunk_id_for, logical_chunk_id_for, revision_id_for,
)
source_hash = 'sha256:' + 'a' * 64
print(json.dumps({
    'revision': str(revision_id_for(DocumentId('doc_stable'), source_hash, 'parser-v1')),
    'logical': str(logical_chunk_id_for(DocumentId('doc_stable'), '{\"type\":\"document\"}')),
    'chunk': str(chunk_id_for(
        RevisionId('rev_stable'), '{\"type\":\"document\"}', 'sha256:' + 'b' * 64,
    )),
}, sort_keys=True))
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        check=True,
        capture_output=True,
        cwd=Path(__file__).parents[3],
        text=True,
    )

    assert json.loads(result.stdout) == local


def test_normalized_block_rejects_whitespace_only_answer_content() -> None:
    """A whitespace paragraph would otherwise become a publishable empty chunk."""
    with pytest.raises(ValueError, match="safe text"):
        NormalizedBlock(
            block_id="b_1",
            kind="paragraph",
            text=" \t",
            heading_path=(),
            page=None,
            paragraph_index=0,
            start_offset=0,
            end_offset=2,
        )


def test_normalized_block_rejects_offsets_that_disagree_with_unicode_text_length() -> None:
    """An anchor spanning more code points than its text would cite the wrong source range."""
    with pytest.raises(ValueError, match="code-point length"):
        NormalizedBlock(
            block_id="b_1",
            kind="paragraph",
            text="猫",
            heading_path=(),
            page=None,
            paragraph_index=0,
            start_offset=0,
            end_offset=2,
        )


@pytest.mark.parametrize(
    "blocks",
    [
        (
            NormalizedBlock("b_same", "paragraph", "first", (), None, 0, 0, 5),
            NormalizedBlock("b_same", "paragraph", "next", (), None, 1, 7, 11),
        ),
        (
            NormalizedBlock("b_later", "paragraph", "later", (), None, 0, 7, 12),
            NormalizedBlock("b_early", "paragraph", "early", (), None, 1, 0, 5),
        ),
        (
            NormalizedBlock("b_first", "paragraph", "first", (), None, 0, 0, 5),
            NormalizedBlock("b_overlap", "paragraph", "other", (), None, 1, 3, 8),
        ),
        (
            NormalizedBlock("b_zero", "paragraph", "first", (), None, 0, 0, 5),
            NormalizedBlock("b_two", "paragraph", "next", (), None, 2, 7, 11),
        ),
    ],
    ids=("duplicate-id", "unordered-offset", "overlapping-offset", "skipped-paragraph-index"),
)
def test_normalized_artifact_rejects_mutated_structural_ordering(
    blocks: tuple[NormalizedBlock, ...],
) -> None:
    """Malformed block order must not reach chunk anchors or duplicate parent identities."""
    with pytest.raises(ValueError):
        NormalizedArtifact(
            filename="policy.txt",
            media_type=MediaType.TEXT,
            source_hash="sha256:" + "f" * 64,
            blocks=blocks,
            document_id=DocumentId("doc_01JTESTDOCUMENT000000000000"),
            revision_id=RevisionId("rev_test"),
        )


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
