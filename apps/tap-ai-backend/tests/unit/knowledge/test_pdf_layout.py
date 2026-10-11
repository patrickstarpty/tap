"""PDF layout recovery over real extracted page text from public insurance terms."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PageObject

from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.adapters.pdf_layout import layout_pdf_pages
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    canonical_sha256,
    revision_id_for,
)

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
        content = _minimal_pdf(len(pages))
        document = DocumentId("doc_" + "0" * 32)
        revision = revision_id_for(document, canonical_sha256(content), PARSER_VERSION)
        return ParserRegistry().parse(
            DocumentSource(f"{name}.pdf", MediaType.PDF, content, document, revision)
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


def test_rate_table_rows_at_page_edges_are_kept() -> None:
    """Number-only table rows differ by value; they are content, not a running header."""
    pages = [
        "费率表\n年龄 保费\n18 1200\n19 1250\n20 1300",
        "21 1350\n22 1400\n23 1450\n24 1500",
        "25 1550\n26 1600\n27 1650\n28 1700",
    ]

    body = "\n".join(block.text for block in layout_pdf_pages(pages).blocks)

    for row in ("18 1200", "20 1300", "21 1350", "24 1500", "25 1550", "28 1700"):
        assert row in body


def test_two_page_document_keeps_lines_repeated_on_both_pages() -> None:
    """Two pages are too few to tell a running header from repeated content."""
    pages = [
        "退保说明\n第一条 退保\n投保人可以申请退保。",
        "第一条 退保\n退保后本公司退还现金价值。",
    ]

    body = "\n".join(block.text for block in layout_pdf_pages(pages).blocks)

    assert body.count("第一条 退保") == 2
    assert "退保后本公司退还现金价值。" in body


def test_clause_headings_and_sentences_opening_pages_are_not_running_headers() -> None:
    """Numbered clause titles and sentences only look alike once digits are ignored."""
    pages = [
        "Terms\n第1条 保险责任\n本公司承担第一项责任。",
        "第2条 保险责任\n本公司承担第二项责任。",
        "第3条 保险责任\n本公司承担第三项责任。",
        "第4条 保险责任\n本公司承担第四项责任。",
    ]

    layout = layout_pdf_pages(pages)
    body = "\n".join(block.text for block in layout.blocks)

    for number in range(1, 5):
        assert f"第{number}条 保险责任" in body
    assert [block.heading_path[-1] for block in layout.blocks] == [
        "第1条 保险责任",
        "第2条 保险责任",
        "第3条 保险责任",
        "第4条 保险责任",
    ]


def test_parser_blocks_never_use_a_separator_as_heading(parse_fixture) -> None:
    """The parser must apply the layout so chunks inherit a real document heading."""
    artifact = parse_fixture("annuity-terms")

    assert artifact.blocks
    assert all(_SEPARATOR not in block.text for block in artifact.blocks)
    assert all(
        all(set(item) - {"_", " "} for item in block.heading_path) for block in artifact.blocks
    )
    assert all(block.page is not None and block.inventory_item_id for block in artifact.blocks)


def _chunks(artifact):
    from tap.modules.knowledge.adapters.document_chunker import StructuralChunker

    return StructuralChunker().chunk(artifact)


def test_wrapped_chinese_lines_rejoin_without_spaces_and_list_items_stay_apart() -> None:
    """A rule wrapped at the margin must read as one sentence in one block."""
    pages = [
        "条款\n"
        "本合同的保险责任与给付规则以本条款约定为准并适用下列各项约定\n"
        "本合同的保险责任。\n"
        "若被保险人生存，则给付满期金，其金额等于满期时本合同基本保险\n"
        "金额的 10%（百分之十）。\n"
        "（1）第一项；\n"
        "（2）第二项。",
        "本页的给付规则与上一页的给付规则相互独立并分别适用下列约定\n第二页正文。",
    ]

    layout = layout_pdf_pages(pages)
    text = "\n".join(block.text for block in layout.blocks)

    assert "本合同的保险责任与给付规则以本条款约定为准并适用下列各项约定本合同的保险责任。" in text
    assert "基本保险金额的 10%（百分之十）。" in text
    assert "（1）第一项；\n（2）第二项。" in text
    assert [block.page for block in layout.blocks] == [1, 2]


def test_wrapped_english_lines_rejoin_with_a_space() -> None:
    """Latin words wrap at a space, so the joined sentence keeps one."""
    pages = [
        "Terms\n"
        "The insurer pays the maturity benefit equal to ten percent of the basic sum and\n"
        "the payment ends this contract once the policy anniversary has been reached by\n"
        "the insured person.\n"
        "Finance approves refunds."
    ]

    text = "\n".join(block.text for block in layout_pdf_pages(pages).blocks)

    assert "basic sum and the payment ends" in text
    assert "reached by the insured person.\nFinance approves refunds." in text


def test_wrapped_maturity_rule_stays_inside_one_chunk(parse_fixture) -> None:
    """Splitting "...基本保 / 险金额的 10%" across chunks made the answer drop the 10%."""
    chunks = _chunks(parse_fixture("annuity-terms"))

    assert any(
        "其金额等于满期时本合同基本保险金额的 10%（百分之十）。" in chunk.content
        for chunk in chunks
    )


@pytest.mark.parametrize("name", ["annuity-terms", "group-medical-terms"])
def test_chunk_count_and_average_length_fall_in_the_paragraph_target(
    name: str, parse_fixture
) -> None:
    """One-line chunks (about 30 characters) starve retrieval and extraction of context."""
    artifact = parse_fixture(name)
    chunks = _chunks(artifact)
    body = sum(len(block.text) for block in artifact.blocks)
    average = sum(len(chunk.content) for chunk in chunks) / len(chunks)

    # Clause blocks below the 512-token chunk limit: short clauses stay whole.
    assert body // 480 <= len(chunks) <= body // 150
    assert 180 <= average <= 480
    assert len(chunks) * 5 <= sum(len(page.split("\n")) for page in _pages(name))


@pytest.mark.parametrize("name", ["annuity-terms", "group-medical-terms"])
def test_every_chunk_anchor_is_an_exact_slice_of_one_page_block(name: str, parse_fixture) -> None:
    """Citation preview resolves a chunk to exactly one normalized block and its page."""
    artifact = parse_fixture(name)
    normalized = "\n\n".join(block.text for block in artifact.blocks)

    for chunk in _chunks(artifact):
        anchor = json.loads(chunk.anchor_json)
        assert normalized[anchor["startOffset"] : anchor["endOffset"]] == chunk.content
        (block,) = [
            block
            for block in artifact.blocks
            if block.start_offset <= anchor["startOffset"]
            and block.end_offset >= anchor["endOffset"]
        ]
        assert block.block_id == chunk.parent_id
        assert block.page == anchor["page"]
        assert list(block.heading_path) == anchor["headingPath"]
        assert block.inventory_item_id == anchor["inventoryItemId"]


def test_clause_numbers_become_the_heading_path_of_their_chunks(parse_fixture) -> None:
    """Conflict checks and citations identify a clause by its numbered heading."""
    chunks = _chunks(parse_fixture("annuity-terms"))
    paths = {tuple(json.loads(chunk.anchor_json)["headingPath"]): chunk.content for chunk in chunks}
    maturity = next(
        path for path, content in paths.items() if "基本保险金额的 10%（百分之十）" in content
    )
    exclusions = next(
        path for path, content in paths.items() if "核爆炸、核辐射或核污染" in content
    )

    assert maturity[:2] == ("友邦悦享年年年金保险", "第二条 保险责任")
    assert exclusions == ("友邦悦享年年年金保险", "第三条 责任免除")
    assert sum(1 for path in paths if len(path) >= 2) >= len(paths) * 0.8


@pytest.mark.parametrize("name", ["annuity-terms", "group-medical-terms"])
def test_no_heading_is_a_separator_header_or_page_number(name: str, parse_fixture) -> None:
    """Only the document title and numbered clauses may label a chunk."""
    artifact = parse_fixture(name)
    headings = {item for block in artifact.blocks for item in block.heading_path}

    assert headings
    assert all(any(character.isalnum() for character in item) for item in headings)
    assert not any(item.startswith("条款编号") for item in headings)
    clause_headings = headings - {artifact.blocks[0].text}
    assert all(
        item.startswith("第") or item[0] in "一二三四五六七八九十" or item[0].isdigit()
        for item in clause_headings
    )


def test_numbered_headings_nest_and_a_new_article_starts_a_new_block() -> None:
    """Sub-clauses nest under their article; the next article must not inherit them."""
    pages = [
        "Policy terms\n"
        "第一条 保险责任\n"
        "一、身故保险金\n"
        "被保险人身故的，给付身故保险金。\n"
        "1.2 给付比例\n"
        "按基本保险金额的 10% 给付。\n"
        "第二条 责任免除\n"
        "被保险人故意自伤的，不承担给付责任。"
    ]

    layout = layout_pdf_pages(pages)

    assert layout.title == "Policy terms"
    assert [block.heading_path for block in layout.blocks] == [
        ("Policy terms", "第一条 保险责任"),
        ("Policy terms", "第二条 责任免除"),
    ]
    assert layout.blocks[0].text.startswith("第一条 保险责任\n一、身故保险金\n")


def test_heading_at_the_foot_of_a_page_labels_the_next_page_without_its_own_chunk() -> None:
    """A heading-only chunk would answer with a title and no rule."""
    pages = [
        "Policy terms\n第一条 保险责任\n被保险人身故的，给付身故保险金。\n第二条 责任免除",
        "被保险人故意自伤的，不承担给付责任。",
    ]

    blocks = layout_pdf_pages(pages).blocks

    assert [(block.page, block.heading_only) for block in blocks] == [
        (1, False),
        (1, True),
        (2, False),
    ]
    assert blocks[2].heading_path == ("Policy terms", "第二条 责任免除")


def test_clause_written_on_one_line_stays_searchable_body_text() -> None:
    """A whole clause on its heading line is a rule, not a bare heading block."""
    filler = "本合同的保险责任与给付规则以本条款约定为准并适用下列各项约定内容"
    pages = [
        "Policy terms\n"
        "第一条 保险责任\n"
        f"{filler}。\n"
        "第十七条 借款 本合同不提供借款。\n"
        f"第十八条 明确说明与如实告知 订立本合同时，本公司应向投保人说明本合同的内容，{filler}\n"
        "并作出明确说明。"
    ]

    blocks = layout_pdf_pages(pages).blocks

    loan = next(block for block in blocks if "本合同不提供借款。" in block.text)
    notice = next(block for block in blocks if "第十八条" in block.text)
    assert not loan.heading_only
    assert loan.heading_path == ("Policy terms", "第十七条")
    assert not notice.heading_only
    assert f"{filler}并作出明确说明。" in notice.text
