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
    blocks: list[PdfLayoutBlock] = []
    for page_number, lines in enumerate(cleaned, start=1):
        for line in lines:
            if title is None:
                title, title_page = line, page_number
                continue
            blocks.append(PdfLayoutBlock(page_number, (title,), line))
    return PdfLayout(title, title_page, tuple(blocks))


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
