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
    """Fold width, case, accents, whitespace and punctuation to a canonical comparison key.

    MySQL's server default collation for ``canonical_key`` (``utf8mb4_0900_ai_ci``,
    see migration 0013) is both accent- and case-insensitive, so this key -- used to
    detect a duplicate ``canonical_key`` across batches before publish -- must be at
    least as coarse: casefold alone is not enough, since "café" and "cafe" collide in
    MySQL but would not collide on casefold alone. Decomposing (NFKD) and dropping
    combining marks (category Mn) before re-composing folds accents the same way.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    unmarked = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    folded = unicodedata.normalize("NFKC", unmarked).casefold()
    return "".join(
        ch for ch in folded if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )
