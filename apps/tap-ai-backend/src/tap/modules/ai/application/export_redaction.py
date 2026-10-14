"""Deterministic redaction applied to strings before they leave for Langfuse export.

Standalone from the knowledge-module query redactor: this redactor never rejects
a field outright (except an unterminated private-key block), it best-effort masks
PII/credentials and always returns a usable string so the sync worker can keep
exporting the surrounding trace.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from tap.platform.redaction_patterns import EMAIL, PRIVATE_START, SECRET

REDACTION_VERSION = "tap-export-v1"

_FAILED = "[REDACTION_FAILED]"
_MAX_CHARS = 32768
_CUSTOM_RULE_NAME = re.compile(r"[a-z0-9_]+")

_MAINLAND_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_MAINLAND_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_MAINLAND_ID_CHECK_CHARS = "10X98765432"

_HK_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{6}\(?[0-9A]\)?(?![A-Za-z0-9])")

_CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")

_MAINLAND_PHONE = re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d(?:[\s-]?\d{4}){2}(?!\d)")
_HK_PHONE = re.compile(r"(?<!\d)(?:\+?852[\s-]?)?[2-9]\d{3}[\s-]?\d{4}(?!\d)")

_FULLWIDTH_OFFSET = 0xFEE0
_FULLWIDTH_START = 0xFF01
_FULLWIDTH_END = 0xFF5E
_FULLWIDTH_SPACE = "　"


@dataclass(frozen=True)
class CustomRule:
    name: str
    pattern: re.Pattern[str]


def load_custom_rules(path: Path | None) -> tuple[CustomRule, ...]:
    """Load operator-defined redaction rules from a YAML file (`rules: [{name, pattern}]`)."""
    if path is None:
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules: list[CustomRule] = []
    for entry in raw.get("rules") or []:
        name = entry["name"]
        if not _CUSTOM_RULE_NAME.fullmatch(name):
            raise ValueError(f"invalid custom rule name: {name!r}")
        rules.append(CustomRule(name=name, pattern=re.compile(entry["pattern"])))
    return tuple(rules)


def _normalize(text: str) -> str:
    """Fullwidth ASCII variants folded to halfwidth; length is preserved character-for-character."""
    chars = [
        chr(ord(ch) - _FULLWIDTH_OFFSET)
        if _FULLWIDTH_START <= ord(ch) <= _FULLWIDTH_END
        else (" " if ch == _FULLWIDTH_SPACE else ch)
        for ch in text
    ]
    return "".join(chars)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _mainland_id_valid(candidate: str) -> bool:
    body, checksum_char = candidate[:17], candidate[17].upper()
    total = sum(int(d) * w for d, w in zip(body, _MAINLAND_ID_WEIGHTS, strict=True))
    return _MAINLAND_ID_CHECK_CHARS[total % 11] == checksum_char


def _redact_private_key(text: str) -> str | None:
    output: list[str] = []
    offset = 0
    for match in PRIVATE_START.finditer(text):
        if match.start() < offset:
            continue
        end_marker = match.group().replace("BEGIN", "END", 1)
        end = text.find(end_marker, match.end())
        if end < 0:
            return None
        output.extend((text[offset : match.start()], "[REDACTED_PRIVATE_KEY]"))
        offset = end + len(end_marker)
    output.append(text[offset:])
    return "".join(output)


def _redact_credentials(text: str) -> str:
    output: list[str] = []
    offset = 0
    for match in SECRET.finditer(text):
        if match.start() < offset:
            continue
        start = match.end()
        if start >= len(text):
            continue
        quote = text[start] if text[start] in "\"'" else ""
        end = start + bool(quote)
        if quote:
            escaped = False
            while end < len(text):
                character = text[end]
                if character == quote and not escaped:
                    break
                escaped = character == "\\" and not escaped
                end += 1
            if end == len(text):
                # Unterminated quote: mask the remainder rather than failing the field.
                output.extend((text[offset:start], quote + "[REDACTED_SECRET]"))
                offset = end
                continue
            end += 1
        else:
            while end < len(text) and not text[end].isspace():
                end += 1
        output.extend((text[offset:start], quote + "[REDACTED_SECRET]" + quote))
        offset = end
    output.append(text[offset:])
    return "".join(output)


def _redact_email(text: str) -> str:
    normalized = _normalize(text)
    output: list[str] = []
    offset = 0
    for match in EMAIL.finditer(normalized):
        output.extend((text[offset : match.start()], "[EMAIL]"))
        offset = match.end()
    output.append(text[offset:])
    return "".join(output)


def _redact_id_numbers(text: str) -> str:
    normalized = _normalize(text)
    output: list[str] = []
    offset = 0
    for match in _MAINLAND_ID.finditer(normalized):
        if match.start() < offset:
            continue
        if _mainland_id_valid(match.group()):
            output.extend((text[offset : match.start()], "[ID_NUMBER]"))
            offset = match.end()
    output.append(text[offset:])
    text = "".join(output)

    normalized = _normalize(text)
    output = []
    offset = 0
    for match in _HK_ID.finditer(normalized):
        if match.start() < offset:
            continue
        output.extend((text[offset : match.start()], "[ID_NUMBER]"))
        offset = match.end()
    output.append(text[offset:])
    return "".join(output)


def _redact_card_numbers(text: str) -> str:
    normalized = _normalize(text)
    output: list[str] = []
    offset = 0
    for match in _CARD_NUMBER.finditer(normalized):
        if match.start() < offset:
            continue
        digits = re.sub(r"[ \-]", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            output.extend((text[offset : match.start()], "[CARD_NUMBER]"))
            offset = match.end()
    output.append(text[offset:])
    return "".join(output)


def _redact_phone_numbers(text: str) -> str:
    normalized = _normalize(text)
    output: list[str] = []
    offset = 0
    matches = sorted(
        (*_MAINLAND_PHONE.finditer(normalized), *_HK_PHONE.finditer(normalized)),
        key=lambda m: m.start(),
    )
    for match in matches:
        if match.start() < offset:
            continue
        output.extend((text[offset : match.start()], "[PHONE]"))
        offset = match.end()
    output.append(text[offset:])
    return "".join(output)


def _redact_custom(text: str, rules: Sequence[CustomRule]) -> str:
    for rule in rules:
        text = rule.pattern.sub(f"[CUSTOM:{rule.name}]", text)
    return text


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CHARS:
        return text
    overflow = len(text) - _MAX_CHARS
    return f"{text[:_MAX_CHARS]}[TRUNCATED {overflow} chars]"


class ExportRedactor:
    def __init__(self, custom_rules: Sequence[CustomRule] = ()) -> None:
        self._custom_rules = tuple(custom_rules)

    def redact(self, text: str | None) -> str | None:
        if text is None:
            return None
        without_keys = _redact_private_key(text)
        if without_keys is None:
            return _FAILED
        working = without_keys
        working = _redact_credentials(working)
        working = _redact_email(working)
        working = _redact_id_numbers(working)
        working = _redact_card_numbers(working)
        working = _redact_phone_numbers(working)
        working = _redact_custom(working, self._custom_rules)
        return _truncate(working)

    def redact_value(self, value: object) -> object:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {key: self.redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        return value
