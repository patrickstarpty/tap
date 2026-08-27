"""Provider-neutral parsing and chunking ports used by document ingestion."""

from __future__ import annotations

from typing import Protocol

from tap.modules.knowledge.domain.documents import ChunkDraft, DocumentSource, NormalizedArtifact


class DocumentParser(Protocol):
    """Converts one closed media type into a safe normalized artifact."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact: ...


class DocumentChunker(Protocol):
    """Converts normalized, addressable text into vector-free manifest drafts."""

    def chunk(self, artifact: NormalizedArtifact) -> tuple[ChunkDraft, ...]: ...
