"""Bounded deterministic query-only egress redaction; canonical evidence is untouched."""

import re

from tap.modules.knowledge.ports.models import RedactionResult
from tap.modules.knowledge.ports.redaction import RedactionUnavailable
from tap.platform.redaction_patterns import ASSIGNMENT_CANDIDATE as _ASSIGNMENT_CANDIDATE
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
        if type(text) is not str or len(text) > self._max_chars or "\x00" in text:
            raise RedactionUnavailable("query redaction rejected input")
        # Input is bounded before scans. Never consume only a credential prefix.
        output: list[str] = []
        offset = 0
        for match in _PRIVATE_START.finditer(text):
            if match.start() < offset:
                continue
            end_marker = match.group().replace("BEGIN", "END", 1)
            end = text.find(end_marker, match.end())
            if end < 0:
                raise RedactionUnavailable("query redaction rejected credential block")
            output.extend((text[offset : match.start()], "[REDACTED_PRIVATE_KEY]"))
            offset = end + len(end_marker)
        output.append(text[offset:])
        sanitized = "".join(output)
        if re.search(r"-----[^\n]*PRIVATE KEY", sanitized):
            raise RedactionUnavailable("query redaction rejected credential block")
        for candidate in _ASSIGNMENT_CANDIDATE.finditer(sanitized):
            if _SECRET.match(sanitized, candidate.start()) is None:
                raise RedactionUnavailable("query redaction rejected assignment key")
        output = []
        offset = 0
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
        output.append(sanitized[offset:])
        return _EMAIL.sub("[REDACTED_EMAIL]", "".join(output))
