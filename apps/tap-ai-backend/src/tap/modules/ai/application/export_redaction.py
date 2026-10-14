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
_MATCH_WINDOW_SLACK = 512
_MATCH_WINDOW = _MAX_CHARS + _MATCH_WINDOW_SLACK
_CUSTOM_RULE_NAME = re.compile(r"[a-z0-9_]+")

_MAINLAND_ID = re.compile(r"\d{17}[\dXx]")
_MAINLAND_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_MAINLAND_ID_CHECK_CHARS = "10X98765432"
_MAINLAND_ID_BODY_DIGITS = 17

_HK_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{6}\(?[0-9A]\)?(?![A-Za-z0-9])")

_CARD_MIN_DIGITS = 13
_CARD_MAX_DIGITS = 19
_NUMBER_MIN_DIGITS = 8
# Digit groups joined by 1-3 separator characters; never preceded or followed by a digit.
_DIGIT_SPAN = re.compile(r"(?<!\d)\+?\d+(?:[ \t\-]{1,3}\d+)*(?!\d)")
_SPAN_NON_VALUE = re.compile(r"[ \t\-+]")
_MAINLAND_PHONE = re.compile(r"(?:86)?1[3-9]\d{9}")
_HK_PHONE = re.compile(r"(?:852)?[2-9]\d{7}")
_DATE = re.compile(r"(?:19|20)\d{2}-?\d{2}-?\d{2}")

_FULLWIDTH_OFFSET = 0xFEE0
_FULLWIDTH_START = 0xFF01
_FULLWIDTH_END = 0xFF5E
_FULLWIDTH_SPACE = "　"


@dataclass(frozen=True)
class CustomRule:
    name: str
    pattern: re.Pattern[str]


def load_custom_rules(path: Path | None) -> tuple[CustomRule, ...]:
    """Load operator-defined redaction rules from a YAML file (`rules: [{name, pattern}]`).

    Any malformed content (not a mapping, `rules` not a list, a malformed entry, a bad
    name or an invalid regex) is normalized to `ValueError`; a missing file still raises
    `FileNotFoundError` from the underlying read.
    """
    if path is None:
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"custom rules file must contain a mapping, got {type(raw).__name__}")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ValueError("custom rules 'rules' key must be a list")
    rules: list[CustomRule] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"custom rule entry must be a mapping, got {entry!r}")
        try:
            name = entry["name"]
            pattern_source = entry["pattern"]
        except KeyError as error:
            raise ValueError(f"custom rule entry missing required key: {error}") from error
        if not isinstance(name, str) or not _CUSTOM_RULE_NAME.fullmatch(name):
            raise ValueError(f"invalid custom rule name: {name!r}")
        try:
            pattern = re.compile(pattern_source)
        except re.error as error:
            raise ValueError(f"invalid custom rule pattern for {name!r}: {error}") from error
        rules.append(CustomRule(name=name, pattern=pattern))
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


def _redact_hk_ids(text: str) -> str:
    normalized = _normalize(text)
    output: list[str] = []
    offset = 0
    for match in _HK_ID.finditer(normalized):
        output.extend((text[offset : match.start()], "[ID_NUMBER]"))
        offset = match.end()
    output.append(text[offset:])
    return "".join(output)


def _classify_span(span: str) -> str | None:
    """Placeholder for a whole digit span, or None to keep it.

    A span that is exactly one typed value gets its typed label; any other span of
    8+ digits (except a bare date) becomes `[NUMBER]` as a whole. Masking whole spans
    instead of typed sub-matches means no digit of a long span can ever survive
    (宁多勿漏: over-masking is acceptable, leaking is not). Custom rules run after
    this pass, so they only see digit runs shorter than 8 digits.
    """
    value = _SPAN_NON_VALUE.sub("", span)
    if _MAINLAND_ID.fullmatch(value) and _mainland_id_valid(value):
        return "[ID_NUMBER]"
    if _MAINLAND_PHONE.fullmatch(value):
        return "[PHONE]"
    if (
        value.isdigit()
        and _CARD_MIN_DIGITS <= len(value) <= _CARD_MAX_DIGITS
        and _luhn_valid(value)
    ):
        return "[CARD_NUMBER]"
    is_date = _DATE.fullmatch(span) is not None
    if is_date and "-" in span:
        return None  # a dashed date is never an HK phone; plain 2xxxxxxx stays [PHONE]
    if _HK_PHONE.fullmatch(value):
        return "[PHONE]"
    digit_count = len(value) - (value[-1] in "Xx")
    if digit_count < _NUMBER_MIN_DIGITS or is_date:
        return None
    return "[NUMBER]"


def _redact_digit_spans(text: str) -> str:
    normalized = _normalize(text)
    output: list[str] = []
    offset = 0
    for match in _DIGIT_SPAN.finditer(normalized):
        end = match.end()
        if (
            normalized[end : end + 1] in ("X", "x")
            and not normalized[end + 1 : end + 2].isdigit()
            and sum(character.isdigit() for character in match.group()) == _MAINLAND_ID_BODY_DIGITS
        ):
            end += 1
        replacement = _classify_span(normalized[match.start() : end])
        if replacement is not None:
            output.extend((text[offset : match.start()], replacement))
            offset = end
    output.append(text[offset:])
    return "".join(output)


def _redact_custom(text: str, rules: Sequence[CustomRule]) -> str:
    for rule in rules:
        text = rule.pattern.sub(f"[CUSTOM:{rule.name}]", text)
    return text


def _truncate(text: str, original_length: int) -> str:
    if original_length <= _MAX_CHARS:
        return text
    overflow = original_length - _MAX_CHARS
    return f"{text[:_MAX_CHARS]}[TRUNCATED {overflow} chars]"


class ExportRedactor:
    def __init__(self, custom_rules: Sequence[CustomRule] = ()) -> None:
        self._custom_rules = tuple(custom_rules)

    def redact(self, text: str | None) -> str | None:
        if text is None:
            return None
        try:
            original_length = len(text)
            # Bound the scanned input so pathological unbounded text (e.g. no '@' for
            # the whole length) cannot drive quadratic regex behaviour; the slack
            # window still lets a match that starts late finish past the hard cutoff.
            bounded = text[:_MATCH_WINDOW]
            without_keys = _redact_private_key(bounded)
            if without_keys is None:
                return _FAILED
            working = without_keys
            working = _redact_credentials(working)
            working = _redact_email(working)
            working = _redact_hk_ids(working)
            working = _redact_digit_spans(working)
            working = _redact_custom(working, self._custom_rules)
            if original_length > _MATCH_WINDOW:
                # A value straddling the original scan-window edge is only half
                # inside `bounded`, so the visible half never matches any pattern
                # and would otherwise leak right before the truncation marker. Any
                # such fragment can only land within the last `_MATCH_WINDOW_SLACK`
                # characters of `working`, so dropping that slack removes it.
                working = working[: max(0, len(working) - _MATCH_WINDOW_SLACK)]
            return _truncate(working, original_length)
        except Exception:
            return _FAILED

    def redact_value(self, value: object) -> object:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {self.redact_value(key): self.redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_value(item) for item in value)
        if isinstance(value, frozenset):
            return frozenset(self.redact_value(item) for item in value)
        if isinstance(value, set):
            return {self.redact_value(item) for item in value}
        return value
