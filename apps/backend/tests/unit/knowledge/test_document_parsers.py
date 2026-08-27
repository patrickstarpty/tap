"""Behavioral tests for the four fail-closed document parsers."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest

from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.domain.documents import (
    DocumentParseRejected,
    DocumentSource,
    MediaType,
)


def _pdf_with_text(*pages: str) -> bytes:
    """Build a minimal text PDF without making the test depend on a PDF writer."""
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


def _docx(*paragraphs: tuple[str, str], table: tuple[tuple[str, ...], ...] = ()) -> bytes:
    paragraph_xml = "".join(
        '<w:p><w:pPr><w:pStyle w:val="%s"/></w:pPr><w:r><w:t>%s</w:t></w:r></w:p>'
        % (style, escape(text))
        for style, text in paragraphs
    )
    table_xml = ""
    if table:
        rows = "".join(
            "<w:tr>"
            + "".join(
                f"<w:tc><w:p><w:r><w:t>{escape(cell)}</w:t></w:r></w:p></w:tc>" for cell in row
            )
            + "</w:tr>"
            for row in table
        )
        table_xml = f"<w:tbl>{rows}</w:tbl>"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraph_xml}{table_xml}<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _source(
    kind: str, heading: str = "Refund policy", body: str = "Two approvals."
) -> DocumentSource:
    payloads = {
        "pdf": ("policy.pdf", MediaType.PDF, _pdf_with_text(heading, body)),
        "docx": ("policy.docx", MediaType.DOCX, _docx(("Heading 1", heading), ("Normal", body))),
        "markdown": ("policy.md", MediaType.MARKDOWN, f"# {heading}\n\n{body}\n".encode()),
        "txt": ("policy.txt", MediaType.TEXT, f"{heading}\n\n{body}\n".encode()),
    }
    filename, media_type, content = payloads[kind]
    return DocumentSource(filename, media_type, content)


@pytest.mark.parametrize("kind", ["pdf", "docx", "markdown", "txt"])
def test_supported_parser_preserves_order_and_anchor(kind: str) -> None:
    """Dropping headings or paragraph position would make citations unresolvable."""
    artifact = ParserRegistry().parse(_source(kind))
    paragraph = next(
        block
        for block in artifact.blocks
        if block.kind == "paragraph" and block.text == "Two approvals."
    )

    assert paragraph.heading_path == (("Refund policy",) if kind != "txt" else ())
    assert paragraph.text == "Two approvals."
    assert paragraph.start_offset < paragraph.end_offset


def test_pdf_records_page_numbers_for_text_from_later_pages() -> None:
    """Losing page location would make a PDF citation point to the wrong page."""
    artifact = ParserRegistry().parse(
        DocumentSource("policy.pdf", MediaType.PDF, _pdf_with_text("First page", "Second page"))
    )

    second = next(block for block in artifact.blocks if block.text == "Second page")
    assert second.page == 2
    assert second.paragraph_index == 1


def test_scanned_pdf_fails_as_ocr_required() -> None:
    """Treating an image-only PDF as empty hides the required user remediation."""
    with pytest.raises(DocumentParseRejected, match="^ocr-required$"):
        ParserRegistry().parse(DocumentSource("scan.pdf", MediaType.PDF, _pdf_with_text("")))


def test_markdown_keeps_fenced_code_and_pipe_table_as_single_locatable_blocks() -> None:
    """Splitting fenced code or a table row silently destroys their structural locator."""
    content = b"# Guide\n\n```python\nprint('x')\n```\n\n| A | B |\n| - | - |\n| 1 | 2 |\n"
    artifact = ParserRegistry().parse(DocumentSource("guide.md", MediaType.MARKDOWN, content))

    code = next(block for block in artifact.blocks if block.kind == "code")
    table = next(block for block in artifact.blocks if block.kind == "table_text")
    assert code.text == "```python\nprint('x')\n```"
    assert table.text == "| A | B |\n| - | - |\n| 1 | 2 |"
    assert code.heading_path == table.heading_path == ("Guide",)


def test_docx_heading_levels_and_table_remain_structural() -> None:
    """Flattening DOCX heading levels or individual table cells breaks stable anchoring."""
    content = _docx(
        ("Heading 1", "Policy"),
        ("Heading 2", "Refunds"),
        ("Normal", "Two approvals."),
        table=(("Role", "Count"), ("Finance", "2")),
    )
    artifact = ParserRegistry().parse(DocumentSource("policy.docx", MediaType.DOCX, content))

    paragraph = next(block for block in artifact.blocks if block.text == "Two approvals.")
    table = next(block for block in artifact.blocks if block.kind == "table_text")
    assert paragraph.heading_path == ("Policy", "Refunds")
    assert table.text == "Role\tCount\nFinance\t2"
    assert sum(block.kind == "table_text" for block in artifact.blocks) == 1


def test_text_parser_normalizes_crlf_and_uses_unicode_code_point_offsets() -> None:
    """Byte offsets would point inside an emoji when the viewer slices Unicode text."""
    artifact = ParserRegistry().parse(
        DocumentSource("notes.txt", MediaType.TEXT, "第一段🙂\r\n\r\n第二段".encode())
    )

    first, second = artifact.blocks
    assert first.text == "第一段🙂"
    assert second.text == "第二段"
    assert second.start_offset == 6
    assert second.end_offset == 9


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (DocumentSource("empty.txt", MediaType.TEXT, b"\r\n\r\n"), "empty-document"),
        (DocumentSource("nul.txt", MediaType.TEXT, b"before\0after"), "invalid-document"),
        (
            DocumentSource("wrong.txt", MediaType.PDF, _pdf_with_text("text")),
            "unsupported-document",
        ),
        (DocumentSource("broken.docx", MediaType.DOCX, b"not a zip"), "invalid-document"),
    ],
)
def test_parser_rejects_invalid_inputs_with_closed_error(
    source: DocumentSource, error: str
) -> None:
    """Leaking parser exceptions would expose implementation details to upload users."""
    with pytest.raises(DocumentParseRejected, match=f"^{error}$"):
        ParserRegistry().parse(source)


def test_docx_zip_bomb_is_rejected_before_document_library_opens_it() -> None:
    """Opening a zip with excessive declared expansion would permit resource exhaustion."""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * (100 * 1024 * 1024 + 1))

    with pytest.raises(DocumentParseRejected, match="^invalid-document$"):
        ParserRegistry().parse(DocumentSource("bomb.docx", MediaType.DOCX, payload.getvalue()))
