"""PDF layout recovery over real extracted page text from public insurance terms."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PageObject

from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.adapters.pdf_layout import layout_pdf_pages
from tap.modules.knowledge.domain.documents import DocumentSource, MediaType

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "documents" / "pdf_layout"
_SEPARATOR = "_" * 95


def _pages(name: str) -> list[str]:
    return list(json.loads((_FIXTURES / f"{name}.json").read_text("utf-8"))["pages"])


def _minimal_pdf(page_count: int) -> bytes:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = [3 + index for index in range(page_count)]
    kids = " ".join(f"{item} 0 R" for item in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    for _ in range(page_count):
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")
    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, value in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    body.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets))
    body.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(body)


@pytest.fixture
def parse_fixture(monkeypatch: pytest.MonkeyPatch):
    """Parse a fixture through PdfParser with pypdf returning the recorded page text."""

    def parse(name: str):
        pages = _pages(name)
        texts = iter(pages)
        monkeypatch.setattr(PageObject, "extract_text", lambda self, *a, **k: next(texts))
        return ParserRegistry().parse(
            DocumentSource(f"{name}.pdf", MediaType.PDF, _minimal_pdf(len(pages)))
        )

    return parse


@pytest.mark.parametrize("name", ["annuity-terms", "group-medical-terms"])
def test_repeated_headers_and_separator_lines_leave_body_and_headings(name: str) -> None:
    """A page separator used as every chunk heading made all sources look like one clause."""
    layout = layout_pdf_pages(_pages(name))
    texts = [block.text for block in layout.blocks]
    headings = {item for block in layout.blocks for item in block.heading_path}

    assert layout.title is not None and "_" not in layout.title
    assert not any(set(item) <= {"_", " "} for item in headings)
    assert not any(line.strip().strip("_ ") == "" for text in texts for line in text.split("\n"))
    assert not any(line.startswith("条款编号") for text in texts for line in text.split("\n"))


def test_page_number_footer_is_not_body_text() -> None:
    """Running footers repeated on most pages are layout, not policy content."""
    pages = [
        "Refund terms\nRefunds need two approvals.\nProduct manual 1   2017",
        "Product manual 2   2017\nFinance approves first.\n- 2 -",
        "Directors approve second.\nProduct manual 3   2017",
        "Payment follows approval.\nProduct manual 4   2017\n4",
    ]

    layout = layout_pdf_pages(pages)
    body = "\n".join(block.text for block in layout.blocks)

    assert "Product manual" not in body
    assert "- 2 -" not in body and "\n4" not in body
    assert "Directors approve second." in body


def test_parser_blocks_never_use_a_separator_as_heading(parse_fixture) -> None:
    """The parser must apply the layout so chunks inherit a real document heading."""
    artifact = parse_fixture("annuity-terms")

    assert artifact.blocks
    assert all(_SEPARATOR not in block.text for block in artifact.blocks)
    assert all(
        all(set(item) - {"_", " "} for item in block.heading_path) for block in artifact.blocks
    )
    assert all(block.page is not None and block.inventory_item_id for block in artifact.blocks)
