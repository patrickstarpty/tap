"""Bounded deterministic query-only egress redaction; canonical evidence is untouched."""

import re
from collections.abc import Mapping

from tap.modules.knowledge.ports.models import RedactionResult
from tap.modules.knowledge.ports.redaction import RedactionUnavailable
from tap.platform.redaction_patterns import ASSIGNMENT_CANDIDATE as _ASSIGNMENT_CANDIDATE
from tap.platform.redaction_patterns import CN_MOBILE as _CN_MOBILE
from tap.platform.redaction_patterns import CN_RESIDENT_ID as _CN_RESIDENT_ID
from tap.platform.redaction_patterns import EMAIL as _EMAIL
from tap.platform.redaction_patterns import PRIVATE_START as _PRIVATE_START
from tap.platform.redaction_patterns import SECRET as _SECRET

VERSION = "tapper-pattern-egress-v1"


class PatternEgressRedactor:
    def __init__(self, *, max_chars: int = 8000) -> None:
        self._max_chars = max_chars

    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(
            sanitized_text=await self.redact_text(text), redaction_version=VERSION
        )

    async def redact_text(self, text: str) -> str:
        sanitized, _counts = await self.redact_with_report(text)
        return sanitized

    async def redact_with_report(self, text: str) -> tuple[str, Mapping[str, int]]:
        if type(text) is not str or len(text) > self._max_chars or "\x00" in text:
            raise RedactionUnavailable("query redaction rejected input")
        counts: dict[str, int] = {}
        # Input is bounded before scans. Never consume only a credential prefix.
        output: list[str] = []
        offset = 0
        private_key_hits = 0
        for match in _PRIVATE_START.finditer(text):
            if match.start() < offset:
                continue
            end_marker = match.group().replace("BEGIN", "END", 1)
            end = text.find(end_marker, match.end())
            if end < 0:
                raise RedactionUnavailable("query redaction rejected credential block")
            output.extend((text[offset : match.start()], "[REDACTED_PRIVATE_KEY]"))
            offset = end + len(end_marker)
            private_key_hits += 1
        output.append(text[offset:])
        sanitized = "".join(output)
        if private_key_hits:
            counts["private_key"] = private_key_hits
        if re.search(r"-----[^\n]*PRIVATE KEY", sanitized):
            raise RedactionUnavailable("query redaction rejected credential block")
        for candidate in _ASSIGNMENT_CANDIDATE.finditer(sanitized):
            if _SECRET.match(sanitized, candidate.start()) is None:
                raise RedactionUnavailable("query redaction rejected assignment key")
        output = []
        offset = 0
        secret_hits = 0
        for match in _SECRET.finditer(sanitized):
            if match.start() < offset:
                continue
            start = match.end()
            if start == len(sanitized):
                raise RedactionUnavailable("query redaction rejected credential value")
            quote = sanitized[start] if sanitized[start] in "\"'" else ""
            end = start + bool(quote)
            if quote:
                escaped = False
                while end < len(sanitized):
                    character = sanitized[end]
                    if character == quote and not escaped:
                        break
                    escaped = character == "\\" and not escaped
                    end += 1
                if end == len(sanitized):
                    raise RedactionUnavailable("query redaction rejected credential value")
                end += 1
                if (
                    end < len(sanitized)
                    and not sanitized[end].isspace()
                    and sanitized[end] not in ",};"
                ):
                    raise RedactionUnavailable("query redaction rejected credential boundary")
            else:
                while end < len(sanitized) and not sanitized[end].isspace():
                    end += 1
            output.extend((sanitized[offset:start], quote + "[REDACTED_SECRET]" + quote))
            offset = end
            secret_hits += 1
        output.append(sanitized[offset:])
        sanitized = "".join(output)
        if secret_hits:
            counts["secret"] = secret_hits

        # Resident ID before mobile: an 18-digit ID must be fully consumed
        # first so its trailing 11 digits are never re-read as a phone number.
        sanitized, id_hits = _CN_RESIDENT_ID.subn("[REDACTED_ID]", sanitized)
        if id_hits:
            counts["id_number"] = id_hits
        sanitized, phone_hits = _CN_MOBILE.subn("[REDACTED_PHONE]", sanitized)
        if phone_hits:
            counts["phone"] = phone_hits

        sanitized, email_hits = _EMAIL.subn("[REDACTED_EMAIL]", sanitized)
        if email_hits:
            counts["email"] = email_hits

        return sanitized, counts
