"""Incrementally decode one top-level string field from a streamed JSON object."""

from __future__ import annotations

import json

_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class IncrementalJsonField:
    """Return newly decoded text of `field` in the top-level object as chunks arrive.

    Only the first string value of `field` directly inside the top-level object is
    extracted; nested objects, arrays and non-string values are skipped.
    """

    def __init__(self, field: str) -> None:
        self._field = field
        self._depth = 0
        self._expect_key = False
        self._key: str | None = None
        # None outside strings; otherwise "key", "value" (the extracted field) or "skip".
        self._string: str | None = None
        self._raw_key: list[str] = []
        self._escape: str | None = None
        self._high_surrogate: int | None = None
        self._done = False

    def feed(self, chunk: str) -> str:
        if self._done:
            return ""
        out: list[str] = []
        for char in chunk:
            if self._string is not None:
                self._string_char(char, out)
                if self._done:
                    break
            elif char == '"':
                self._open_string()
            elif char in "{[":
                self._depth += 1
                if self._depth == 1:
                    self._expect_key = char == "{"
            elif char in "}]":
                self._depth -= 1
            elif self._depth == 1 and char == ":":
                self._expect_key = False
            elif self._depth == 1 and char == ",":
                self._expect_key = True
                self._key = None
        return "".join(out)

    def _open_string(self) -> None:
        if self._depth != 1:
            self._string = "skip"
        elif self._expect_key:
            self._string = "key"
            self._raw_key = []
        elif self._key == self._field:
            self._string = "value"
        else:
            self._string = "skip"

    def _string_char(self, char: str, out: list[str]) -> None:
        mode = self._string
        if mode != "value":
            if self._escape is not None:
                self._escape = None
            elif char == "\\":
                self._escape = ""
            elif char == '"':
                self._string = None
                if mode == "key":
                    try:
                        self._key = json.loads('"' + "".join(self._raw_key) + '"')
                    except ValueError:
                        self._key = None
                return
            if mode == "key":
                self._raw_key.append(char)
            return
        if self._escape is None:
            if char == "\\":
                self._escape = ""
                return
            self._flush_surrogate(out)
            if char == '"':
                self._string = None
                self._done = True
            else:
                out.append(char)
            return
        self._escape += char
        if self._escape[0] != "u":
            self._flush_surrogate(out)
            out.append(_SIMPLE_ESCAPES.get(char, ""))
            self._escape = None
            return
        if len(self._escape) < 5:
            return
        try:
            code = int(self._escape[1:], 16)
        except ValueError:
            code = 0xFFFD
        self._escape = None
        if self._high_surrogate is not None and 0xDC00 <= code <= 0xDFFF:
            high = self._high_surrogate
            self._high_surrogate = None
            out.append(chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00)))
            return
        self._flush_surrogate(out)
        if 0xD800 <= code <= 0xDBFF:
            self._high_surrogate = code
        else:
            out.append(chr(code))

    def _flush_surrogate(self, out: list[str]) -> None:
        if self._high_surrogate is not None:
            out.append(chr(self._high_surrogate))
            self._high_surrogate = None
