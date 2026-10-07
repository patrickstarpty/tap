"""Longest-match alias scanning over normalized text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tap.modules.graph.domain.project import Alias
from tap.modules.graph.domain.vocabulary import normalize_key


@dataclass(frozen=True, slots=True)
class AliasMatch:
    alias_norm: str
    node_id: str
    start: int
    end: int


class AliasIndex:
    def __init__(self, entries: tuple[Alias, ...]) -> None:
        self._entries = entries
        nodes_by_norm: dict[str, list[str]] = {}
        for alias in entries:
            node_ids = nodes_by_norm.setdefault(alias.alias_norm, [])
            if alias.node_id not in node_ids:
                node_ids.append(alias.node_id)
        self._nodes_by_norm = nodes_by_norm

    @classmethod
    def build(cls, aliases: Iterable[Alias]) -> "AliasIndex":
        return cls(tuple(aliases))

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[Alias, ...]:
        """Raw alias rows, for callers (e.g. prefix/contains search) that need
        more than the longest-match scan `match()` performs."""
        return self._entries

    def match(self, text: str) -> tuple[AliasMatch, ...]:
        normalized = normalize_key(text)
        masked = bytearray(len(normalized))
        ordered_norms = sorted(self._nodes_by_norm, key=lambda norm: (-len(norm), norm))
        seen_nodes: set[str] = set()
        matches: list[AliasMatch] = []

        for alias_norm in ordered_norms:
            if not alias_norm:
                continue
            search_from = 0
            while True:
                start = normalized.find(alias_norm, search_from)
                if start == -1:
                    break
                end = start + len(alias_norm)
                if any(masked[start:end]):
                    search_from = start + 1
                    continue
                for position in range(start, end):
                    masked[position] = 1
                for node_id in self._nodes_by_norm[alias_norm]:
                    if node_id not in seen_nodes:
                        seen_nodes.add(node_id)
                        matches.append(AliasMatch(alias_norm, node_id, start, end))
                search_from = end

        matches.sort(key=lambda match: match.start)
        return tuple(matches)
