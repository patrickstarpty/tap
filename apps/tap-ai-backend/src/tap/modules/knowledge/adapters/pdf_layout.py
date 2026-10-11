"""Recover reading structure from per-page PDF text before it becomes normalized blocks."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

# Running headers and footers sit in the first or last few lines of a page.
_EDGE_LINES = 3
_PAGE_NUMBER = re.compile(
    r"^[-–—\s]*(?:第\s*\d+\s*页(?:\s*[,，/]?\s*共\s*\d+\s*页)?|\d+\s*/\s*\d+|\d+"
    r"|page\s+\d+(?:\s+of\s+\d+)?)[-–—\s]*$",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"\d+")
_MIN_WRAP_WIDTH = 40
_FULL_LINE_RATIO = 0.85
_SENTENCE_END = ("。", "！", "？", "；", "!", "?", ";", ".")
_JOINING_MARKS = ("，", "、", ",", "（", "(", "《", "“", "‘", "-")
_LIST_ITEM = re.compile(
    r"^(?:[（(]\s*(?:\d+|[一二三四五六七八九十]+)\s*[)）]|\d+[、.．)]\D|[①-⑳]"
    r"|[一二三四五六七八九十]+、|[-•·●▪]\s)"
)
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class PdfLayoutBlock:
    page: int
    heading_path: tuple[str, ...]
    text: str


@dataclass(frozen=True, slots=True)
class PdfLayout:
    title: str | None
    title_page: int | None
    blocks: tuple[PdfLayoutBlock, ...]


def layout_pdf_pages(pages: Sequence[str]) -> PdfLayout:
    """Turn extracted page text (page 1 first) into titled, page-bound body blocks."""
    cleaned = clean_pdf_pages(pages)
    title: str | None = None
    title_page: int | None = None
    for page_number, lines in enumerate(cleaned, start=1):
        if lines:
            title, title_page = lines[0], page_number
            cleaned[page_number - 1] = lines[1:]
            break
    root = () if title is None else (title,)
    paragraphs = _paragraphs(cleaned, root)
    return PdfLayout(title, title_page, tuple(_pack(paragraphs)))


@dataclass(slots=True)
class _Paragraph:
    page: int
    heading_path: tuple[str, ...]
    text: str
    open: bool


def _paragraphs(pages: Sequence[Sequence[str]], root: tuple[str, ...]) -> list[_Paragraph]:
    """Rejoin lines that the PDF wrapped at the right margin; pages stay separate."""
    full_width = _full_line_width(pages)
    paragraphs: list[_Paragraph] = []
    for page_number, lines in enumerate(pages, start=1):
        current: _Paragraph | None = None
        for line in lines:
            if current is not None and current.open and not _LIST_ITEM.match(line):
                current.text = _join_wrapped(current.text, line)
                current.open = _continues(line, full_width)
                continue
            current = _Paragraph(page_number, root, line, _continues(line, full_width))
            paragraphs.append(current)
    return paragraphs


def _pack(paragraphs: Sequence[_Paragraph]) -> list[PdfLayoutBlock]:
    """Group the paragraphs of one page; the chunker sizes blocks at sentence ends."""
    blocks: list[PdfLayoutBlock] = []
    group: list[_Paragraph] = []

    def flush() -> None:
        if group:
            blocks.append(
                PdfLayoutBlock(
                    group[0].page,
                    _common_prefix([item.heading_path for item in group]),
                    "\n".join(item.text for item in group),
                )
            )
            group.clear()

    for paragraph in paragraphs:
        if group and paragraph.page != group[0].page:
            flush()
        group.append(paragraph)
    flush()
    return blocks


def _common_prefix(paths: Sequence[tuple[str, ...]]) -> tuple[str, ...]:
    prefix = paths[0]
    for path in paths[1:]:
        length = 0
        while length < min(len(prefix), len(path)) and prefix[length] == path[length]:
            length += 1
        prefix = prefix[:length]
    return prefix


def _full_line_width(pages: Sequence[Sequence[str]]) -> int | None:
    """The display width of a line that runs to the right margin, if lines wrap at all."""
    widths = sorted(_display_width(line) for lines in pages for line in lines)
    if len(widths) < 4:
        return None
    reference = widths[int(len(widths) * 0.9)]
    return reference if reference >= _MIN_WRAP_WIDTH else None


def _continues(line: str, full_width: int | None) -> bool:
    """Whether a line visibly runs on: it fills the line, or stops on a joining mark."""
    if line.endswith(_SENTENCE_END):
        return False
    if line.endswith(_JOINING_MARKS):
        return True
    return full_width is not None and _display_width(line) >= full_width * _FULL_LINE_RATIO


def _join_wrapped(left: str, right: str) -> str:
    """CJK text wraps without a space; Latin words wrap at a space or a hyphen."""
    if left.endswith("-") and left[-2:-1].isalpha() and right[:1].islower():
        return left[:-1] + right
    if _is_wide(left[-1]) or _is_wide(right[0]):
        return left + right
    return left + " " + right


def _display_width(line: str) -> int:
    return sum(2 if _is_wide(character) else 1 for character in line)


def _is_wide(character: str) -> bool:
    return unicodedata.east_asian_width(character) in {"W", "F"}


def clean_pdf_pages(pages: Sequence[str]) -> list[list[str]]:
    """Drop separator, page-number and running header/footer lines from every page."""
    lines = [
        [line.strip() for line in page.split("\n") if line.strip() and not _is_separator(line)]
        for page in pages
    ]
    repeated = _running_lines(lines)
    cleaned: list[list[str]] = []
    for page_lines in lines:
        last = len(page_lines)
        kept = []
        for index, line in enumerate(page_lines):
            at_edge = index < _EDGE_LINES or index >= last - _EDGE_LINES
            if at_edge and (_PAGE_NUMBER.fullmatch(line) or _edge_key(line) in repeated):
                continue
            kept.append(line)
        cleaned.append(kept)
    return cleaned


def _running_lines(pages: Sequence[Sequence[str]]) -> frozenset[str]:
    """Edge lines that recur, digits aside, on more than half of the text pages."""
    text_pages = [page for page in pages if page]
    if len(text_pages) < 2:
        return frozenset()
    counts: Counter[str] = Counter()
    for page in text_pages:
        edges = list(page[:_EDGE_LINES]) + list(page[-_EDGE_LINES:])
        counts.update({_edge_key(line) for line in edges})
    return frozenset(
        key for key, count in counts.items() if count >= 2 and count * 2 > len(text_pages)
    )


def _edge_key(line: str) -> str:
    return _SPACES.sub(" ", _DIGITS.sub("#", line)).strip()


def _is_separator(line: str) -> bool:
    """Rules, underscores and other lines with no letter, ideograph or digit."""
    return not any(unicodedata.category(character)[0] in {"L", "N"} for character in line)
