"""Recover reading structure from per-page PDF text before it becomes normalized blocks."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

# Running headers and footers sit in the first or last few lines of a page.
_EDGE_LINES = 3
_MIN_RUNNING_PAGES = 3
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
_HEADING_CHARACTERS = 40
_HEADING_CLOSERS = ("。", "！", "？", "；", "，", "、", "!", "?", ";", ",", ".", "：", ":")
_ARTICLE_RANK = 3
_SUBSECTION_BLOCK_CHARACTERS = 300
_NUMERAL = r"[一二三四五六七八九十百零〇两\d]+"
_DIVISION = re.compile(rf"^第\s*{_NUMERAL}\s*(章|部分|节|条)")
_ENUMERATED = re.compile(r"^[一二三四五六七八九十]+、\s*\S")
_DECIMAL = re.compile(r"^(\d+(?:\.\d+)+)\s+\S")


@dataclass(frozen=True, slots=True)
class PdfLayoutBlock:
    page: int
    heading_path: tuple[str, ...]
    text: str
    heading_only: bool = False


@dataclass(frozen=True, slots=True)
class PdfLayout:
    title: str | None
    title_page: int | None
    blocks: tuple[PdfLayoutBlock, ...]


def layout_pdf_pages(pages: Sequence[str]) -> PdfLayout:
    """Turn extracted page text (page 1 first) into titled, page-bound clause blocks."""
    cleaned = clean_pdf_pages(pages)
    title, title_page = _title(cleaned)
    if title is not None and title_page is not None:
        cleaned[title_page - 1].remove(title)
    root = () if title is None else (title,)
    return PdfLayout(title, title_page, tuple(_pack(_paragraphs(cleaned, root))))


@dataclass(slots=True)
class _Paragraph:
    page: int
    heading_path: tuple[str, ...]
    text: str
    open: bool
    rank: int | None = None


def _paragraphs(pages: Sequence[Sequence[str]], root: tuple[str, ...]) -> list[_Paragraph]:
    """Rejoin lines wrapped at the right margin and track the numbered heading path.

    Paragraphs never cross a page, so every block keeps one page anchor.
    """
    full_width = _full_line_width(pages)
    paragraphs: list[_Paragraph] = []
    headings: list[tuple[int, str]] = []
    for page_number, lines in enumerate(pages, start=1):
        current: _Paragraph | None = None
        for line in lines:
            heading = _heading(line)
            if (
                current is not None
                and current.open
                and not _LIST_ITEM.match(line)
                # A long line opening with a clause number continues a cross-reference.
                and (heading is None or not _title_like(line))
            ):
                current.text = _join_wrapped(current.text, line)
                current.open = _continues(line, full_width)
                continue
            if heading is not None:
                rank, label = heading
                while headings and headings[-1][0] >= rank:
                    headings.pop()
                headings.append((rank, label))
                path = root + tuple(item for _, item in headings)
                current = _Paragraph(page_number, path, line, False, rank)
            else:
                path = root + tuple(item for _, item in headings)
                current = _Paragraph(page_number, path, line, _continues(line, full_width))
            paragraphs.append(current)
    return paragraphs


def _pack(paragraphs: Sequence[_Paragraph]) -> list[PdfLayoutBlock]:
    """Group a page's paragraphs by clause; the chunker sizes blocks at sentence ends.

    An article (or a larger division) always opens a block. A lower numbered heading
    opens one only when the running block already holds enough text to stand alone.
    """
    blocks: list[PdfLayoutBlock] = []
    group: list[_Paragraph] = []
    size = 0

    def flush() -> None:
        if group:
            blocks.append(
                PdfLayoutBlock(
                    group[0].page,
                    _common_prefix([item.heading_path for item in group]),
                    "\n".join(item.text for item in group),
                    # A heading left at the foot of a page labels the next page's text.
                    all(item.rank is not None for item in group),
                )
            )
            group.clear()

    for paragraph in paragraphs:
        if group and (
            paragraph.page != group[0].page
            or (paragraph.rank is not None and paragraph.rank <= _ARTICLE_RANK)
            or (paragraph.rank is not None and size >= _SUBSECTION_BLOCK_CHARACTERS)
        ):
            flush()
            size = 0
        size += len(paragraph.text)
        group.append(paragraph)
    flush()
    return blocks


def _heading(line: str) -> tuple[int, str] | None:
    """Rank and label of a numbered clause heading; deeper clauses rank higher."""
    division = _DIVISION.match(line)
    if division is not None:
        rank = {"章": 1, "部分": 1, "节": 2, "条": _ARTICLE_RANK}[division.group(1)]
        if _title_like(line):
            return rank, _SPACES.sub(" ", line)
        return rank, division.group(0)
    if not _title_like(line):
        return None
    if _ENUMERATED.match(line):
        return _ARTICLE_RANK + 1, _SPACES.sub(" ", line)
    decimal = _DECIMAL.match(line)
    if decimal is not None:
        return _ARTICLE_RANK + 1 + decimal.group(1).count("."), _SPACES.sub(" ", line)
    return None


def _title(pages: Sequence[Sequence[str]]) -> tuple[str | None, int | None]:
    """The last title-like line before the first clause, else a title-like first line."""
    for page_number, lines in enumerate(pages, start=1):
        if not lines:
            continue
        preamble: list[str] = []
        for line in lines:
            if _heading(line) is not None:
                break
            preamble.append(line)
        candidates = [line for line in preamble if _title_like(line) and not _LIST_ITEM.match(line)]
        if len(preamble) < len(lines) and candidates:
            return candidates[-1], page_number
        if _title_like(lines[0]) and not _LIST_ITEM.match(lines[0]):
            return lines[0], page_number
        return None, None
    return None, None


def _title_like(line: str) -> bool:
    return len(line) <= _HEADING_CHARACTERS and not line.endswith(_HEADING_CLOSERS)


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
            if at_edge and (_PAGE_NUMBER.fullmatch(line) or _running(line, repeated)):
                continue
            kept.append(line)
        cleaned.append(kept)
    return cleaned


def _running_lines(pages: Sequence[Sequence[str]]) -> frozenset[str]:
    """Edge lines that recur on at least three and more than half of the text pages.

    Clause headings and complete sentences are content even when they repeat, and
    digits count as page-number noise only beside letters or ideographs, so rows of a
    numeric table never collapse into one key.
    """
    text_pages = [page for page in pages if page]
    if len(text_pages) < _MIN_RUNNING_PAGES:
        return frozenset()
    counts: Counter[str] = Counter()
    for page in text_pages:
        edges = list(page[:_EDGE_LINES]) + list(page[-_EDGE_LINES:])
        counts.update(
            {
                _edge_key(line)
                for line in edges
                if _heading(line) is None and not line.endswith(_SENTENCE_END)
            }
        )
    return frozenset(
        key
        for key, count in counts.items()
        if count >= _MIN_RUNNING_PAGES and count * 2 > len(text_pages)
    )


def _running(line: str, repeated: frozenset[str]) -> bool:
    return (
        _edge_key(line) in repeated and _heading(line) is None and not line.endswith(_SENTENCE_END)
    )


def _edge_key(line: str) -> str:
    if any(unicodedata.category(character)[0] == "L" for character in line):
        line = _DIGITS.sub("#", line)
    return _SPACES.sub(" ", line).strip()


def _is_separator(line: str) -> bool:
    """Rules, underscores and other lines with no letter, ideograph or digit."""
    return not any(unicodedata.category(character)[0] in {"L", "N"} for character in line)
