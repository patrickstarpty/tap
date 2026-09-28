"""Collapse child matches using current, project-owned chunk identities."""

from dataclasses import replace
from typing import Protocol

from tap.modules.knowledge.ports.models import SearchExecution, SearchHit
from tap.modules.knowledge.ports.search import SearchPort

ParentGroups = dict[str, tuple[str, str, str] | None]


class ParentGroupAuthority(Protocol):
    async def parent_groups(self, hits: tuple[SearchHit, ...]) -> ParentGroups: ...


def collapse_parent_groups(
    hits: tuple[SearchHit, ...], groups: ParentGroups
) -> tuple[SearchHit, ...]:
    selected: dict[tuple[str, str, str], SearchHit] = {}
    for hit in hits:
        identity = groups.get(
            hit.chunk_id, (hit.source.source_id, hit.source.revision, hit.chunk_id)
        )
        if identity is None:
            continue
        previous = selected.get(identity)
        if previous is None or (hit.score, -hit.local_rank) > (
            previous.score,
            -previous.local_rank,
        ):
            selected[identity] = hit
    ordered = sorted(selected.values(), key=lambda hit: (hit.local_rank, -hit.score, hit.chunk_id))
    return tuple(replace(hit, local_rank=rank) for rank, hit in enumerate(ordered, start=1))


class ManagedChunkSearch:
    def __init__(self, search: SearchPort, authority: ParentGroupAuthority) -> None:
        self.search_port = search
        self.authority = authority

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        hits = await self.search_port.search(execution)
        return collapse_parent_groups(hits, await self.authority.parent_groups(hits))
