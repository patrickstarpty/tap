"""Provider-neutral answer snapshot values and durable repository port."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from tap.modules.knowledge.domain.documents import (
    DocumentId,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    logical_chunk_projection_id,
)
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerResponse,
    Citation,
    Claim,
    ContentRole,
    DocumentAnchor,
    ModelCallProvenance,
    RetrievalProfileId,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)


class DocumentStateChanged(Exception):
    """A selected ready/current document changed before an atomic snapshot commit."""


class AnswerSnapshotUnavailable(Exception):
    """The bounded answer/citation resolver state could not be committed."""


@dataclass(frozen=True, slots=True)
class ReadyDocumentRevision:
    """One current ready document fact used to close retrieval authority."""

    document_id: str
    revision_id: str
    source_content_hash: str

    def __post_init__(self) -> None:
        _bounded("ready document ID", self.document_id, maximum=64)
        _bounded("ready revision ID", self.revision_id, maximum=128)
        _digest(self.source_content_hash)


@dataclass(frozen=True, slots=True)
class CitationSnapshot:
    trace_id: str
    citation_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    source_content_hash: str
    chunk_content_hash: str
    anchor_json: str

    def __post_init__(self) -> None:
        _bounded("citation trace ID", self.trace_id, maximum=64)
        _bounded("citation ID", self.citation_id, maximum=64)
        _bounded("citation document ID", self.document_id, maximum=64)
        _bounded("citation revision ID", self.revision_id, maximum=128)
        _bounded("citation chunk ID", self.chunk_id, maximum=128)
        _digest(self.source_content_hash)
        _digest(self.chunk_content_hash)
        try:
            value = json.loads(self.anchor_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("citation anchor must be canonical JSON") from error
        if (
            not isinstance(value, dict)
            or json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            != self.anchor_json
        ):
            raise ValueError("citation anchor must be a canonical JSON object")


@dataclass(frozen=True, slots=True)
class AnswerSnapshot:
    trace_id: str
    query_hash: str
    selected_revisions: tuple[ReadyDocumentRevision, ...]
    citations: tuple[CitationSnapshot, ...]

    def __post_init__(self) -> None:
        _bounded("answer trace ID", self.trace_id, maximum=64)
        _digest(self.query_hash)
        if (
            not isinstance(self.selected_revisions, tuple)
            or not 1 <= len(self.selected_revisions) <= 20
            or not all(isinstance(item, ReadyDocumentRevision) for item in self.selected_revisions)
            or tuple(sorted(self.selected_revisions, key=lambda item: item.document_id))
            != self.selected_revisions
            or len({item.document_id for item in self.selected_revisions})
            != len(self.selected_revisions)
        ):
            raise ValueError("answer selected revisions must be sorted, unique, and bounded")
        if (
            not isinstance(self.citations, tuple)
            or len(self.citations) > 20
            or not all(isinstance(item, CitationSnapshot) for item in self.citations)
            or len({item.citation_id for item in self.citations}) != len(self.citations)
        ):
            raise ValueError("answer citations must be unique and bounded")
        selected = {
            (item.document_id, item.revision_id, item.source_content_hash)
            for item in self.selected_revisions
        }
        if any(
            item.trace_id != self.trace_id
            or (item.document_id, item.revision_id, item.source_content_hash) not in selected
            for item in self.citations
        ):
            raise ValueError("answer citation trace and selected revision must be exact")

    @classmethod
    def from_response(
        cls,
        *,
        response: AnswerResponse,
        query: str,
        selected_revisions: tuple[ReadyDocumentRevision, ...],
    ) -> AnswerSnapshot:
        _validate_gateway_response(response)
        ordered = tuple(sorted(selected_revisions, key=lambda item: item.document_id))
        selected = {
            (item.document_id, item.revision_id, item.source_content_hash) for item in ordered
        }
        citations: list[CitationSnapshot] = []
        for item in response.citations:
            source = item.source
            if not isinstance(source, SourceRevisionRef) or not isinstance(
                source.anchor, DocumentAnchor
            ):
                raise ValueError("answer citation provenance is malformed")
            anchor_json = _document_anchor_json(source.anchor)
            if (
                item.family is not SourceFamily.DOC
                or item.content_role is not ContentRole.SOURCE
                or source.source_type != "doc"
                or source.revision_kind is not RevisionKind.BLOB_VERSION
                or source.anchor.bbox
                or source.anchor.start_offset is None
                or source.anchor.end_offset is None
                or source.anchor.end_offset <= source.anchor.start_offset
                or item.chunk_id
                != str(
                    chunk_id_for(
                        RevisionId(source.revision),
                        anchor_json,
                        item.chunk_content_hash,
                    )
                )
                or item.logical_chunk_id
                != logical_chunk_projection_id(
                    logical_chunk_id_for(DocumentId(source.source_id), anchor_json)
                )
                or (
                    source.source_id,
                    source.revision,
                    source.source_content_hash,
                )
                not in selected
            ):
                raise ValueError("answer citation is outside the selected document revisions")
            citations.append(
                CitationSnapshot(
                    trace_id=response.trace_id,
                    citation_id=item.citation_id,
                    document_id=source.source_id,
                    revision_id=source.revision,
                    chunk_id=item.chunk_id,
                    source_content_hash=source.source_content_hash,
                    chunk_content_hash=item.chunk_content_hash,
                    anchor_json=anchor_json,
                )
            )
        citation_ids = {item.citation_id for item in citations}
        if not response.abstained:
            if not response.claims or any(
                not claim.citation_ids or not set(claim.citation_ids) <= citation_ids
                for claim in response.claims
            ):
                raise ValueError("non-abstained claims must reference inserted citations")
        return cls(
            trace_id=response.trace_id,
            query_hash=canonical_sha256(query.encode("utf-8")),
            selected_revisions=ordered,
            citations=tuple(citations),
        )


class AnswerSnapshotRepository(Protocol):
    async def load_ready_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]: ...

    async def save_answer_with_citations(self, snapshot: AnswerSnapshot) -> None: ...


def _document_anchor_json(anchor: DocumentAnchor) -> str:
    value: dict[str, object] = {
        "endOffset": anchor.end_offset,
        "headingPath": list(anchor.heading_path),
        "startOffset": anchor.start_offset,
        "type": "document",
    }
    if anchor.page is not None:
        value["page"] = anchor.page
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _validate_gateway_response(response: AnswerResponse) -> None:
    if not isinstance(response, AnswerResponse):
        raise TypeError("knowledge gateway must return AnswerResponse")
    _bounded("answer trace ID", response.trace_id, maximum=64)
    _bounded("answer query plan ID", response.query_plan_id, maximum=256)
    _bounded("answer context snapshot ID", response.context_snapshot_id, maximum=256)
    if (
        response.corpus_version != "athena-demo-v1"
        or response.retrieval_profile_id is not RetrievalProfileId.QUICK_HYBRID_V1
        or not isinstance(response.answer, str)
        or type(response.abstained) is not bool
        or type(response.degraded_mode) is not bool
        or not isinstance(response.degradation_reasons, tuple)
        or any(
            not isinstance(reason, str) or not reason or len(reason) > 256
            for reason in response.degradation_reasons
        )
        or not isinstance(response.embedding_provenance, ModelCallProvenance)
        or not isinstance(response.citations, tuple)
        or len(response.citations) > 20
        or not all(isinstance(item, Citation) for item in response.citations)
        or not isinstance(response.claims, tuple)
        or not all(isinstance(item, Claim) for item in response.claims)
    ):
        raise ValueError("gateway answer metadata is outside the fixed snapshot contract")
    if len({item.citation_id for item in response.citations}) != len(response.citations):
        raise ValueError("gateway answer citations must have unique identities")
    for position, citation in enumerate(response.citations, start=1):
        _bounded("citation ID", citation.citation_id, maximum=64)
        _bounded("citation chunk ID", citation.chunk_id, maximum=128)
        _bounded("citation logical chunk ID", citation.logical_chunk_id, maximum=128)
        _digest(citation.chunk_content_hash)
        if (
            not isinstance(citation.source, SourceRevisionRef)
            or not isinstance(citation.derived_from_chunk_ids, tuple)
            or any(
                not isinstance(item, str) or not item or len(item) > 128
                for item in citation.derived_from_chunk_ids
            )
        ):
            raise ValueError("gateway citation structure is malformed")
        if (
            citation.evidence_label != f"S{position}"
            or re.fullmatch(r"S(?:[1-9]|1[0-9]|20)", citation.evidence_label) is None
        ):
            raise ValueError("gateway citation evidence labels are malformed")
    citation_ids = {item.citation_id for item in response.citations}
    if response.abstained:
        if (
            response.answer
            or response.claims
            or not isinstance(response.abstention_reason, AbstentionReason)
            or response.answer_provenance is not None
        ):
            raise ValueError("gateway abstention shape is inconsistent")
        return
    if (
        not response.answer
        or not response.claims
        or response.abstention_reason is not None
        or not isinstance(response.answer_provenance, ModelCallProvenance)
    ):
        raise ValueError("gateway grounded answer shape is inconsistent")
    paragraphs = _paragraph_spans(response.answer)
    previous_end = 0
    claim_ids: set[str] = set()
    for claim in response.claims:
        _bounded("claim ID", claim.claim_id, maximum=64)
        if claim.claim_id in claim_ids:
            raise ValueError("gateway claim IDs must be unique")
        claim_ids.add(claim.claim_id)
        matches = tuple(
            span for span in paragraphs if response.answer[span[0] : span[1]] == claim.text
        )
        if (
            not claim.text
            or "\n\n" in claim.text
            or len(matches) != 1
            or matches[0] != (claim.answer_start, claim.answer_end)
            or claim.answer_start < previous_end
            or not isinstance(claim.citation_ids, tuple)
            or not 1 <= len(claim.citation_ids) <= 20
            or any(
                not isinstance(citation_id, str) or not citation_id or len(citation_id) > 64
                for citation_id in claim.citation_ids
            )
            or len(set(claim.citation_ids)) != len(claim.citation_ids)
            or not set(claim.citation_ids) <= citation_ids
        ):
            raise ValueError("gateway claim spans or citations are invalid")
        previous_end = claim.answer_end


def _paragraph_spans(answer: str) -> tuple[tuple[int, int], ...]:
    start = 0
    spans: list[tuple[int, int]] = []
    for paragraph in answer.split("\n\n"):
        end = start + len(paragraph)
        spans.append((start, end))
        start = end + 2
    return tuple(spans)


def _bounded(name: str, value: object, *, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a nonblank bounded string")


def _digest(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("snapshot digest must be canonical sha256")
