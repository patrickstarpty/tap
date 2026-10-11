"""Revisions parsed before the current PARSER_VERSION stay readable after the bump."""

from __future__ import annotations

import pytest

from tap.modules.knowledge.adapters.artifact_codecs import (
    decode_chunks_artifact,
    encode_chunks_artifact,
)
from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.adapters.parser_protocol import (
    ParserProtocolError,
    decode_request,
    encode_request,
)
from tap.modules.knowledge.domain.documents import (
    LEGACY_PARSER_VERSIONS,
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    canonical_sha256,
    revision_id_for,
)

CONTENT = b"# Refunds\n\nFinance approves refunds.\n"
DOCUMENT = DocumentId("doc_" + "1" * 32)


def _source(parser_version: str) -> DocumentSource:
    revision = revision_id_for(DOCUMENT, canonical_sha256(CONTENT), parser_version)
    return DocumentSource("refunds.md", MediaType.MARKDOWN, CONTENT, DOCUMENT, revision)


def test_parser_version_bump_keeps_the_previous_version_as_legacy() -> None:
    """Re-parsing an original needs a new revision identity for the same source hash."""
    assert PARSER_VERSION not in LEGACY_PARSER_VERSIONS
    assert "tapper-parser-v1" in LEGACY_PARSER_VERSIONS
    assert _source(PARSER_VERSION).revision_id != _source("tapper-parser-v1").revision_id


@pytest.mark.parametrize("version", [PARSER_VERSION, *sorted(LEGACY_PARSER_VERSIONS)])
def test_legacy_revision_chunks_round_trip_and_reach_the_parser(version: str) -> None:
    """Existing ready revisions must still decode, and failed ones must still retry."""
    source = _source(version)
    chunks = StructuralChunker().chunk(ParserRegistry().parse(source))

    decoded = decode_chunks_artifact(
        encode_chunks_artifact(str(source.revision_id), chunks),
        expected_revision=str(source.revision_id),
    )
    request = encode_request(source, "a" * 32)

    assert [chunk.chunk_id for chunk in decoded] == [chunk.chunk_id for chunk in chunks]
    assert request.endswith(CONTENT)


def test_parser_still_rejects_a_revision_from_an_unknown_parser_version() -> None:
    source = _source("tapper-parser-v0")
    header = {
        "version": 1,
        "requestId": "a" * 32,
        "filename": source.filename,
        "mediaType": source.media_type.value,
        "documentId": source.document_id,
        "revisionId": source.revision_id,
        "sourceHash": canonical_sha256(CONTENT),
        "contentLength": len(CONTENT),
    }

    with pytest.raises(ParserProtocolError):
        decode_request(header, CONTENT)
