"""The grounded answer's abstention sentinel, shared by the final-answer parser
and the streamed draft so both hide it the same way."""

from __future__ import annotations

import re

INSUFFICIENT_EVIDENCE_SENTINEL = "INSUFFICIENT_EVIDENCE"

# Alone, the sentinel is an abstention. Inside an otherwise grounded answer it
# is model noise -- bracketed like a citation marker, ending a sentence, or on
# a line of its own -- and is removed with its brackets so no `[]` is left.
_STRAY_SENTINEL = re.compile(
    rf"\[[ \t]*{INSUFFICIENT_EVIDENCE_SENTINEL}[ \t]*\]|{INSUFFICIENT_EVIDENCE_SENTINEL}"
)


def without_abstention_sentinel(text: str) -> str:
    return _STRAY_SENTINEL.sub("", text)
