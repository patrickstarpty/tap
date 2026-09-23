"""Behavioral coverage for durable document completeness inventory."""

from __future__ import annotations

import base64
import io
import json

import pytest
from docx import Document

from tap.modules.knowledge.adapters.artifact_codecs import (
    decode_normalized_artifact,
    encode_normalized_artifact,
)
from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.domain.documents import (
    BlockKind,
    DocumentId,
    DocumentParseRejected,
    DocumentSource,
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


def _pdf_with_text(*pages: str) -> bytes:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = [4 + index * 2 for index in range(len(pages))]
    kids = " ".join(f"{item} 0 R" for item in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        page_id = page_ids[index]
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "
                f"/MediaBox [0 0 612 792] /Contents {page_id + 1} 0 R >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode())
        body.extend(value)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    body.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    body.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(body)


def _identified_source(filename: str, media_type: MediaType, content: bytes) -> DocumentSource:
    document_id = DocumentId("doc_inventory")
    return DocumentSource(
        filename,
        media_type,
        content,
        document_id,
        revision_id_for(document_id, canonical_sha256(content), "tapper-parser-v1"),
    )


def test_mixed_pdf_inventory_does_not_hide_a_page_without_extractable_text() -> None:
    """Skipping a blank/scanned page would falsely mark a mixed PDF complete."""
    artifact = ParserRegistry().parse(
        _identified_source("mixed.pdf", MediaType.PDF, _pdf_with_text("Approved rule", ""))
    )

    pages = [item for item in artifact.parse_inventory if item.kind is ParseInventoryKind.PAGE]
    assert [(item.locator, item.status, item.reason) for item in pages] == [
        ("page:1", ParseInventoryStatus.PARSED, None),
        ("page:2", ParseInventoryStatus.FAILED, "ocr-required"),
    ]
    assert {block.inventory_item_id for block in artifact.blocks} == {pages[0].item_id}
    assert artifact.parser_config_digest == parser_config_digest(MediaType.PDF.value)
    assert artifact.parse_inventory_digest == parse_inventory_digest(artifact.parse_inventory)


def test_docx_inventory_records_table_and_embedded_image_separately() -> None:
    """Flattening a table or silently dropping an image destroys completeness evidence."""
    document = Document()
    document.add_paragraph("Approved rule")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Role"
    table.cell(0, 1).text = "Count"
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    document.add_picture(io.BytesIO(png))
    payload = io.BytesIO()
    document.save(payload)

    artifact = ParserRegistry().parse(
        _identified_source("mixed.docx", MediaType.DOCX, payload.getvalue())
    )

    table_item = next(
        item for item in artifact.parse_inventory if item.kind is ParseInventoryKind.TABLE
    )
    image_item = next(
        item for item in artifact.parse_inventory if item.kind is ParseInventoryKind.IMAGE
    )
    assert (table_item.locator, table_item.status) == (
        "table:1",
        ParseInventoryStatus.PARSED,
    )
    assert (image_item.status, image_item.reason) == (
        ParseInventoryStatus.NEEDS_REVIEW,
        "image-not-text-extracted",
    )
    table_block = next(block for block in artifact.blocks if block.kind is BlockKind.TABLE_TEXT)
    assert table_block.inventory_item_id == table_item.item_id


def test_invalid_text_encoding_returns_a_failed_inventory_item() -> None:
    """Rejecting bytes without a durable item would make the completeness gap disappear."""
    artifact = ParserRegistry().parse(
        _identified_source("broken.txt", MediaType.TEXT, b"valid-prefix\xff")
    )

    assert artifact.blocks == ()
    assert [(item.status, item.reason, item.locator) for item in artifact.parse_inventory] == [
        (ParseInventoryStatus.FAILED, "invalid-encoding", "document:text")
    ]
    with pytest.raises(DocumentParseRejected, match="^invalid-document$"):
        StructuralChunker().chunk(artifact)


def test_image_only_pdf_keeps_the_ocr_remediation_at_the_chunking_boundary() -> None:
    """Persisting inventory must not degrade the existing safe OCR error shown to users."""
    artifact = ParserRegistry().parse(
        _identified_source("scan.pdf", MediaType.PDF, _pdf_with_text(""))
    )

    with pytest.raises(DocumentParseRejected, match="^ocr-required$"):
        StructuralChunker().chunk(artifact)


def test_chunk_anchor_binds_to_the_inventory_item_that_produced_its_text() -> None:
    """A chunk without an inventory identity cannot prove which checked object it came from."""
    item = ParseInventoryItem.create(
        source_revision_id="rev_inventory",
        kind=ParseInventoryKind.PARAGRAPH,
        locator="paragraph:1",
        status=ParseInventoryStatus.PARSED,
        artifact_digest=canonical_sha256(b"Approved rule"),
    )
    artifact = NormalizedArtifact(
        filename="policy.txt",
        media_type=MediaType.TEXT,
        source_hash=canonical_sha256(b"Approved rule"),
        blocks=(
            NormalizedBlock(
                block_id="b_000000",
                kind=BlockKind.PARAGRAPH,
                text="Approved rule",
                heading_path=(),
                page=None,
                paragraph_index=0,
                start_offset=0,
                end_offset=13,
                inventory_item_id=item.item_id,
            ),
        ),
        document_id=DocumentId("doc_inventory"),
        revision_id=RevisionId("rev_inventory"),
        parse_inventory=(item,),
        parser_config_digest=parser_config_digest(MediaType.TEXT.value),
        parse_inventory_digest=parse_inventory_digest((item,)),
    )

    chunk = StructuralChunker().chunk(artifact)[0]

    assert f'"inventoryItemId":"{item.item_id}"' in chunk.anchor_json


def test_excluded_inventory_item_requires_both_reason_and_accountable_actor() -> None:
    """An anonymous exclusion would let missing source content evade independent review."""
    with pytest.raises(ValueError, match="actor"):
        ParseInventoryItem.create(
            source_revision_id="rev_inventory",
            kind=ParseInventoryKind.IMAGE,
            locator="image:1",
            status=ParseInventoryStatus.EXCLUDED,
            reason="decorative",
            artifact_digest=canonical_sha256(b"image"),
        )

    excluded = ParseInventoryItem.create(
        source_revision_id="rev_inventory",
        kind=ParseInventoryKind.IMAGE,
        locator="image:1",
        status=ParseInventoryStatus.EXCLUDED,
        reason="decorative",
        artifact_digest=canonical_sha256(b"image"),
        decision_actor_id="actor-reviewer",
    )

    assert excluded.status is ParseInventoryStatus.EXCLUDED
    assert excluded.decision_actor_id == "actor-reviewer"


def test_normalized_artifact_codec_preserves_inventory_and_reads_legacy_as_unreviewed() -> None:
    """An upgrade must retain new completeness facts without calling old data approved."""
    source = _identified_source("policy.txt", MediaType.TEXT, b"Approved rule")
    current = ParserRegistry().parse(source)
    assert current.revision_id is not None

    restored = decode_normalized_artifact(
        encode_normalized_artifact(str(current.revision_id), current),
        expected_revision=str(current.revision_id),
    )

    assert restored.parse_inventory == current.parse_inventory
    assert restored.parse_inventory_digest == current.parse_inventory_digest

    source_hash = canonical_sha256(b"Legacy rule")
    document_id = DocumentId("doc_legacy")
    revision_id = revision_id_for(document_id, source_hash, "tapper-parser-v1")
    payload = {
        "blocks": [
            {
                "blockId": "b_000000",
                "endOffset": 11,
                "headingPath": [],
                "kind": "paragraph",
                "page": None,
                "paragraphIndex": 0,
                "startOffset": 0,
                "text": "Legacy rule",
            }
        ],
        "documentId": str(document_id),
        "filename": "legacy.txt",
        "mediaType": "text/plain",
        "normalizedSchema": "normalized-artifact-v1",
    }
    canonical_payload = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
    )
    envelope = {
        "blockCount": 1,
        "payload": payload,
        "payloadSha256": canonical_sha256(canonical_payload),
        "revisionId": str(revision_id),
        "schemaVersion": "normalized-v1",
        "sourceContentHash": source_hash,
    }
    legacy_bytes = (
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
    )

    legacy = decode_normalized_artifact(legacy_bytes, expected_revision=str(revision_id))

    assert legacy.blocks[0].text == "Legacy rule"
    assert legacy.parse_inventory[0].status is ParseInventoryStatus.NEEDS_REVIEW
    assert legacy.parse_inventory[0].reason == "historical-unreviewed"
