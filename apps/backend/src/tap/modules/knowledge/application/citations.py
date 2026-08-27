"""Stale-safe citation preview resolution from immutable document facts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from tap.modules.knowledge.domain.documents import (
    ChunkId,
    DocumentId,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
)
from tap.modules.knowledge.domain.models import DocumentAnchor
from tap.modules.knowledge.ports.citations import (
    CitationArtifactStore,
    CitationLookup,
    CitationRepository,
    CitationSnapshotCorrupt,
)
from tap.modules.knowledge.ports.documents import DocumentState
from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure, ArtifactUnavailable


class CitationStale(Exception):
    """The citation no longer resolves to its exact selected immutable source facts."""


class CitationUnavailable(Exception):
    """A current citation could not be resolved because a provider is unavailable."""


@dataclass(frozen=True, slots=True)
class CitationPreviewResult:
    citation_id: str
    document_id: str
    revision_id: str
    filename: str
    source_content_hash: str
    chunk_content_hash: str
    anchor: DocumentAnchor
    quote: str
    prefix: str = ""
    suffix: str = ""


class CitationResolver:
    def __init__(
        self,
        *,
        repository: CitationRepository,
        artifacts: CitationArtifactStore,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts

    async def resolve(self, citation_id: str) -> CitationPreviewResult:
        if not isinstance(citation_id, str) or not citation_id or len(citation_id) > 64:
            raise CitationStale
        try:
            lookup = await self._repository.load_citation(citation_id)
        except CitationSnapshotCorrupt as error:
            raise CitationStale from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise CitationUnavailable from error
        if lookup is None:
            raise CitationStale
        self._validate_ledger_facts(lookup)
        assert lookup.document is not None
        assert lookup.document.normalized_locator is not None
        assert lookup.document.chunks_locator is not None
        try:
            normalized = await self._artifacts.read_normalized(lookup.document.normalized_locator)
            chunks = await self._artifacts.read_chunks(lookup.document.chunks_locator)
        except ArtifactUnavailable as error:
            raise CitationUnavailable from error
        except ArtifactIntegrityFailure as error:
            raise CitationStale from error
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError) as error:
            raise CitationStale from error
        except Exception as error:
            raise CitationUnavailable from error
        try:
            preview = self._resolve_exact(lookup, normalized, chunks)
        except (TypeError, ValueError) as error:
            raise CitationStale from error
        try:
            current = await self._repository.citation_is_current(lookup.citation)
        except CitationSnapshotCorrupt as error:
            raise CitationStale from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise CitationUnavailable from error
        if not current:
            raise CitationStale
        return preview

    @staticmethod
    def _validate_ledger_facts(lookup: CitationLookup) -> None:
        citation = lookup.citation
        selected = {
            (item.document_id, item.revision_id, item.source_content_hash)
            for item in lookup.selected_revisions
        }
        document = lookup.document
        manifest = lookup.manifest
        if (
            lookup.answer_trace_id != citation.trace_id
            or (
                citation.document_id,
                citation.revision_id,
                citation.source_content_hash,
            )
            not in selected
            or document is None
            or document.document_id != citation.document_id
            or document.status is not DocumentState.READY
            or document.deleted
            or document.current_revision_id != citation.revision_id
            or document.current_source_content_hash != citation.source_content_hash
            or document.revision_source_content_hash != citation.source_content_hash
            or document.normalized_locator is None
            or document.chunks_locator is None
            or manifest is None
            or manifest.chunk_id != citation.chunk_id
            or manifest.chunk_content_hash != citation.chunk_content_hash
            or manifest.anchor_json != citation.anchor_json
        ):
            raise CitationStale

    @staticmethod
    def _resolve_exact(lookup, normalized, chunks) -> CitationPreviewResult:  # type: ignore[no-untyped-def]
        citation = lookup.citation
        document = lookup.document
        manifest = lookup.manifest
        assert document is not None and manifest is not None
        if (
            str(normalized.document_id) != citation.document_id
            or str(normalized.revision_id) != citation.revision_id
            or normalized.source_hash != citation.source_content_hash
        ):
            raise ValueError("normalized artifact envelope does not match citation")
        normalized_text = _normalized_text(normalized.blocks)
        matching = tuple(chunk for chunk in chunks if str(chunk.chunk_id) == citation.chunk_id)
        if len(matching) != 1:
            raise ValueError("chunk artifact does not contain exactly one cited chunk")
        chunk = matching[0]
        if (
            str(chunk.root_id) != citation.document_id
            or chunk.source_content_hash != citation.source_content_hash
            or chunk.chunk_content_hash != citation.chunk_content_hash
            or chunk.anchor_json != citation.anchor_json
            or str(chunk.logical_chunk_id) != manifest.logical_chunk_id
            or chunk.parent_id != manifest.parent_id
            or manifest.root_id != citation.document_id
            or canonical_sha256(chunk.content.encode("utf-8")) != citation.chunk_content_hash
            or str(logical_chunk_id_for(DocumentId(citation.document_id), citation.anchor_json))
            != str(chunk.logical_chunk_id)
            or str(
                chunk_id_for(
                    RevisionId(citation.revision_id),
                    citation.anchor_json,
                    citation.chunk_content_hash,
                )
            )
            != str(ChunkId(citation.chunk_id))
        ):
            raise ValueError("chunk artifact provenance does not match citation")
        anchor = _document_anchor(citation.anchor_json)
        assert anchor.start_offset is not None and anchor.end_offset is not None
        if anchor.end_offset > len(normalized_text):
            raise ValueError("citation anchor exceeds normalized text")
        if normalized_text[anchor.start_offset : anchor.end_offset] != chunk.content:
            raise ValueError("citation chunk is not the exact normalized anchor slice")
        containing = tuple(
            block
            for block in normalized.blocks
            if block.start_offset <= anchor.start_offset and block.end_offset >= anchor.end_offset
        )
        if (
            len(containing) != 1
            or containing[0].block_id != chunk.parent_id
            or containing[0].heading_path != anchor.heading_path
            or containing[0].page != anchor.page
        ):
            raise ValueError("citation anchor structure does not match normalized block")
        return CitationPreviewResult(
            citation_id=citation.citation_id,
            document_id=citation.document_id,
            revision_id=citation.revision_id,
            filename=document.filename,
            source_content_hash=citation.source_content_hash,
            chunk_content_hash=citation.chunk_content_hash,
            anchor=anchor,
            quote=chunk.content[:4_000],
            prefix=normalized_text[max(0, anchor.start_offset - 500) : anchor.start_offset],
            suffix=normalized_text[anchor.end_offset : anchor.end_offset + 500],
        )


def _normalized_text(blocks) -> str:  # type: ignore[no-untyped-def]
    cursor = 0
    values: list[str] = []
    for index, block in enumerate(blocks):
        if (
            block.paragraph_index != index
            or block.start_offset != cursor
            or block.end_offset != cursor + len(block.text)
        ):
            raise ValueError("normalized block layout has a gap or overlap")
        values.append(block.text)
        cursor = block.end_offset + (2 if index + 1 < len(blocks) else 0)
    return "\n\n".join(values)


def _document_anchor(anchor_json: str) -> DocumentAnchor:
    value = json.loads(anchor_json)
    if not isinstance(value, dict):
        raise ValueError("citation anchor must be an object")
    allowed = {"endOffset", "headingPath", "page", "startOffset", "type"}
    required = {"endOffset", "headingPath", "startOffset", "type"}
    if not required <= set(value) or not set(value) <= allowed or value["type"] != "document":
        raise ValueError("citation anchor shape is not the document chunk shape")
    headings = value["headingPath"]
    start = value["startOffset"]
    end = value["endOffset"]
    page = value.get("page")
    if (
        not isinstance(headings, list)
        or any(not isinstance(item, str) or not item for item in headings)
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
        or (page is not None and (type(page) is not int or page < 1))
    ):
        raise ValueError("citation document anchor values are invalid")
    return DocumentAnchor(
        heading_path=tuple(headings),
        page=page,
        start_offset=start,
        end_offset=end,
    )
