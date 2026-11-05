"""CSV/zip export cells and writers for reporting exports (Agent team view
spec section 5).

Each CSV starts with a UTF-8 BOM so Excel opens Chinese names without
mangling them; optional leading comment lines (e.g. `# runtime=unavailable`)
precede the header. Free-text cells are neutralized against spreadsheet
formula injection; `None` renders as an empty cell.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

_UTF8_BOM = "﻿"

# Spreadsheet apps evaluate a cell starting with any of these as a formula
# (OWASP "CSV injection"); free-text cells get a leading `'` so they render
# as literal text. Numeric cells never start with them and are left as-is.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def blank_cell(value: object | None) -> str:
    return "" if value is None else str(value)


def text_cell(value: str | None) -> str:
    """A free-text cell (names, job titles, actor ids), neutralized against
    formula injection."""
    if value is None:
        return ""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def iso_datetime_cell(value: datetime | None) -> str:
    """ISO-8601 UTC, `Z`-suffixed. Domain datetimes are always timezone-aware
    UTC."""
    if value is None:
        return ""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def csv_bytes(
    header: Sequence[str], rows: Iterable[Sequence[str]], *, comment_lines: Sequence[str] = ()
) -> bytes:
    text = io.StringIO()
    text.write(_UTF8_BOM)
    for line in comment_lines:
        text.write(f"{line}\n")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return text.getvalue().encode("utf-8")


def zip_bytes(files: Mapping[str, bytes]) -> bytes:
    """A deflated zip holding `files` (name -> content) in mapping order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()
