"""Canonical knowledge graph vocabulary: node/relation types and key normalization."""

from __future__ import annotations

import unicodedata

NODE_TYPES: frozenset[str] = frozenset(
    {"ENTITY", "CONCEPT", "REQUIREMENT", "SYSTEM", "ACTOR", "PROCESS"}
)

RELATION_TYPES: frozenset[str] = frozenset(
    {
        "REQUIRES",
        "APPLIES_TO",
        "PART_OF",
        "EXCEPTION_OF",
        "SUPERSEDES",
        "TRIGGERS",
        "PRECEDES",
        "VALIDATED_BY",
        "RESPONSIBLE_FOR",
        "USES",
        "DEFINES",
        "CONFLICTS_WITH",
        "RELATED_TO",
    }
)

RELATION_LABEL_MAX = 64
NODE_ALIAS_MAX = 5
NODE_ALIAS_LENGTH_MAX = 128


def normalize_key(text: str) -> str:
    """Fold width, case, whitespace and punctuation to a canonical comparison key."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        ch for ch in folded if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )
