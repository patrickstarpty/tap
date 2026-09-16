"""Stable egress-redaction boundary for model-bound text."""

from __future__ import annotations

from typing import Protocol

from tap.modules.knowledge.ports.models import RedactionResult


class RedactionUnavailable(Exception):
    """The mandatory egress-redaction decision could not be produced."""


class EgressRedactionPort(Protocol):
    async def redact(self, text: str) -> RedactionResult: ...
