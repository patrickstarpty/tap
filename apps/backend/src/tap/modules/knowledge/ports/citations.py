"""Provider-neutral citation lookup facts and repository port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tap.modules.knowledge.domain.documents import ChunkDraft, NormalizedArtifact
from tap.modules.knowledge.ports.answers import CitationSnapshot, ReadyDocumentRevision
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DocumentState,
    ManifestChunk,
)


class CitationSnapshotCorrupt(Exception):
    """Persisted resolver facts are not the closed snapshot shape."""


@dataclass(frozen=True, slots=True)
class CitationDocumentFacts:
    document_id: str
    filename: str
    status: DocumentState
    deleted: bool
    current_revision_id: str
    current_source_content_hash: str
    revision_source_content_hash: str
    normalized_locator: ArtifactLocator | None
    chunks_locator: ArtifactLocator | None


@dataclass(frozen=True, slots=True)
class CitationLookup:
    citation: CitationSnapshot
    answer_trace_id: str | None
    selected_revisions: tuple[ReadyDocumentRevision, ...]
    document: CitationDocumentFacts | None
    manifest: ManifestChunk | None


class CitationRepository(Protocol):
    async def load_citation(self, citation_id: str) -> CitationLookup | None: ...

    async def citation_is_current(self, citation: CitationSnapshot) -> bool: ...


class CitationArtifactStore(Protocol):
    async def read_normalized(self, locator: ArtifactLocator) -> NormalizedArtifact: ...

    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]: ...
