"""Approval, publication, and withdrawal orchestration for governed knowledge."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast

from tap.modules.knowledge.domain.documents import (
    MAX_UPLOAD_BYTES,
    PARSER_VERSION,
    DocumentSource,
    NormalizedArtifact,
    RevisionId,
    canonical_sha256,
    revision_id_for,
)
from tap.modules.knowledge.domain.flowcharts import FlowchartRejected, normalize_flowchart
from tap.modules.knowledge.domain.parse_inventory import (
    OriginalExcerptRange,
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.review import (
    KnowledgePublication,
    KnowledgeReviewHistoryEntry,
    KnowledgeReviewItemDecision,
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
    canonical_digest,
    publication_id_for,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator, ArtifactStore, DocumentParserPort
from tap.modules.knowledge.ports.errors import ArtifactError


class ReviewStateConflict(Exception):
    """A review transition is invalid for the current immutable revision."""


class ReviewCommandConflict(Exception):
    """An idempotency key was reused for a different publication intent."""


class ReviewNotFound(Exception):
    """A review or publication is unavailable in this Project."""


class ProjectionNotReady(Exception):
    """The proposed immutable projection generation did not validate."""


def initial_review_items(
    inventory: tuple[ParseInventoryItem, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Native text keeps existing approval; visual hypotheses start blocked."""
    if not inventory:
        raise ReviewStateConflict("review-inventory-incomplete")
    if all(item.status is ParseInventoryStatus.PARSED for item in inventory):
        return tuple(sorted(item.item_id for item in inventory)), ()
    allowed_kinds = {
        ParseInventoryKind.IMAGE,
        ParseInventoryKind.FLOW_NODE,
        ParseInventoryKind.FLOW_EDGE,
    }
    if not any(item.kind is ParseInventoryKind.FLOW_NODE for item in inventory) or any(
        item.kind not in allowed_kinds
        or item.status is not ParseInventoryStatus.NEEDS_REVIEW
        or item.reason
        not in {
            "visual-analysis-required",
            "visual-confirmation-required",
            "uncertain-connection",
        }
        for item in inventory
    ):
        raise ReviewStateConflict("review-inventory-incomplete")
    return (), tuple(sorted(item.item_id for item in inventory))


class ProjectionVerifier(Protocol):
    async def verify(self, revision: KnowledgeReviewRevision, generation: str) -> bool: ...

    async def generation_for(self, revision: KnowledgeReviewRevision) -> str | None: ...


@dataclass(frozen=True, slots=True)
class KnowledgeReviewRead:
    revision: KnowledgeReviewRevision
    decisions: tuple[KnowledgeReviewItemDecision, ...]
    decision_history: tuple[KnowledgeReviewItemDecision, ...]
    history: tuple[KnowledgeReviewHistoryEntry, ...]


@dataclass(frozen=True, slots=True)
class ReviewInventoryRecord:
    source_revision_id: str
    item_id: str
    attempt: int
    kind: str
    locator: str
    status: str
    artifact_digest: str
    reason: str | None
    decision_actor_id: str | None


@dataclass(frozen=True, slots=True)
class ReviewInventoryPageRead:
    items: tuple[ReviewInventoryRecord, ...]
    total_count: int
    parsed_count: int
    failed_count: int
    needs_review_count: int
    excluded_count: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ReviewDecisionPageRead:
    items: tuple[KnowledgeReviewItemDecision, ...]
    total_count: int
    next_cursor: int | None


@dataclass(frozen=True, slots=True)
class ReviewHistoryPageRead:
    items: tuple[KnowledgeReviewHistoryEntry, ...]
    total_count: int
    next_cursor: int | None


@dataclass(frozen=True, slots=True)
class ReviewPublicationPageRead:
    items: tuple[KnowledgePublication, ...]
    total_count: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class KnowledgeReviewListItemRead:
    review: KnowledgeReviewRead
    inventory: ReviewInventoryPageRead
    decision_history: ReviewDecisionPageRead
    history: ReviewHistoryPageRead
    publications: ReviewPublicationPageRead
    current_publication: KnowledgePublication | None
    authoritative: bool
    generation: str | None = None


@dataclass(frozen=True, slots=True)
class PublishedSourceRecord:
    source_id: str
    document_id: str
    revision_id: str
    source_name: str
    filename: str
    publication_id: str
    expires_at: datetime
    approved_item_count: int
    inventory_item_count: int


@dataclass(frozen=True, slots=True)
class ReviewComparisonTarget:
    media_type: str
    source_revision_id: str
    source_digest: str
    original_locator: ArtifactLocator
    normalized_locator: ArtifactLocator | None
    item_id: str
    original_excerpt: OriginalExcerptRange | None
    original_alignment_reason: str | None
    original_alignment_valid: bool = True


@dataclass(frozen=True, slots=True)
class ReviewPreview:
    availability: Literal["available", "unavailable", "unsupported"]
    excerpt: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewItemComparisonRead:
    review_id: str
    item_id: str
    original: ReviewPreview
    extracted: ReviewPreview


class KnowledgeReviewRepository(Protocol):
    async def resolve_open_review_target(
        self, *, document_id: str, source_revision_id: str
    ) -> str: ...
    async def create_or_open_review(
        self,
        *,
        document_id: str,
        source_revision_id: str,
        authorized_review_id: str,
        actor_id: str,
        expires_at: datetime,
        command_key: str,
        command_digest: str,
        now: datetime,
    ) -> KnowledgeReviewRevision: ...
    async def get_review(self, review_id: str) -> KnowledgeReviewRevision | None: ...
    async def list_reviews(
        self,
        source_revision_id: str | None = None,
        *,
        limit: int = 50,
        after_review_id: str | None = None,
    ) -> tuple[KnowledgeReviewRevision, ...]: ...
    async def review_list_batch(
        self,
        revisions: tuple[KnowledgeReviewRevision, ...],
        *,
        child_limit: int,
    ) -> tuple[KnowledgeReviewListItemRead, ...]: ...
    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]: ...
    async def inventory_page(
        self, review_id: str, *, limit: int, after_item_id: str | None
    ) -> ReviewInventoryPageRead: ...
    async def review_authority(self, revision: KnowledgeReviewRevision) -> bool: ...
    async def save_flowchart_correction(
        self,
        revision: KnowledgeReviewRevision,
        artifact: NormalizedArtifact,
        locator: ArtifactLocator,
        *,
        expected_version: int,
        actor_id: str,
        occurred_at: datetime,
    ) -> KnowledgeReviewRevision: ...
    async def save_review(
        self,
        revision: KnowledgeReviewRevision,
        *,
        expected_version: int,
        actor_id: str,
        action: str,
        occurred_at: datetime,
    ) -> KnowledgeReviewRevision: ...
    async def has_review_item(self, review_id: str, item_id: str) -> bool: ...
    async def list_decisions(self, review_id: str) -> tuple[KnowledgeReviewItemDecision, ...]: ...
    async def list_decision_history(
        self, review_id: str
    ) -> tuple[KnowledgeReviewItemDecision, ...]: ...
    async def decision_history_page(
        self, review_id: str, *, limit: int, after_version: int | None
    ) -> ReviewDecisionPageRead: ...
    async def list_history(self, review_id: str) -> tuple[KnowledgeReviewHistoryEntry, ...]: ...
    async def history_page(
        self, review_id: str, *, limit: int, after_version: int | None
    ) -> ReviewHistoryPageRead: ...
    async def save_item_decision(
        self,
        revision: KnowledgeReviewRevision,
        decision: KnowledgeReviewItemDecision,
        *,
        expected_version: int,
    ) -> KnowledgeReviewRevision: ...
    async def command_result(
        self, key: str, digest: str, *, legacy_digest: str | None = None
    ) -> KnowledgePublication | None: ...
    async def publish(
        self,
        revision: KnowledgeReviewRevision,
        publication: KnowledgePublication,
        *,
        expected_version: int,
        command_key: str,
        command_digest: str,
    ) -> KnowledgePublication: ...
    async def get_publication(self, publication_id: str) -> KnowledgePublication | None: ...
    async def current_publication(
        self, project_id: str | None = None
    ) -> KnowledgePublication | None: ...
    async def current_publications(
        self, project_id: str | None = None
    ) -> tuple[KnowledgePublication, ...]: ...
    async def list_publications(self, review_id: str) -> tuple[KnowledgePublication, ...]: ...
    async def publication_page(
        self, review_id: str, *, limit: int, after_publication_id: str | None
    ) -> ReviewPublicationPageRead: ...
    async def list_published_sources(
        self, *, now: datetime
    ) -> tuple[PublishedSourceRecord, ...]: ...
    async def comparison_target(
        self, review_id: str, item_id: str
    ) -> ReviewComparisonTarget | None: ...
    async def withdraw(
        self,
        publication: KnowledgePublication,
        *,
        expected_version: int,
        command_key: str,
        command_digest: str,
    ) -> KnowledgePublication: ...


class KnowledgeReviewApplication:
    def __init__(
        self,
        repository: KnowledgeReviewRepository,
        projection: ProjectionVerifier,
        artifacts: ArtifactStore | None = None,
        *,
        parser: DocumentParserPort | None = None,
    ) -> None:
        self._repository = repository
        self._projection = projection
        self._artifacts = artifacts
        self._parser = parser

    async def _flowchart_artifact(
        self,
        current: KnowledgeReviewRevision,
    ) -> tuple[ReviewComparisonTarget, NormalizedArtifact]:
        if self._artifacts is None or len(current.source_revision_ids) != 1:
            raise ReviewStateConflict("flowchart-unavailable")
        items = (*current.approved_item_ids, *current.blocking_item_ids)
        if not items:
            inventory = await self._repository.list_inventory(current.review_id)
            items = tuple(item.item_id for item in inventory)
        if not items:
            raise ReviewStateConflict("flowchart-unavailable")
        target = await self._repository.comparison_target(current.review_id, items[0])
        if target is None or target.normalized_locator is None:
            raise ReviewStateConflict("flowchart-unavailable")
        artifact = await self._artifacts.read_normalized(target.normalized_locator)
        if (
            artifact.flowchart_data is None
            or str(artifact.revision_id) != current.source_revision_ids[0]
            or artifact.source_hash != target.source_digest
        ):
            raise ReviewStateConflict("flowchart-unavailable")
        return target, artifact

    async def get_flowchart(self, review_id: str) -> dict[str, object]:
        current = await self._required_review(review_id)
        _, artifact = await self._flowchart_artifact(current)
        assert artifact.flowchart_data is not None
        return cast(dict[str, object], json.loads(artifact.flowchart_data))

    async def correct_flowchart(
        self,
        review_id: str,
        *,
        graph: Mapping[str, object],
        actor_id: str,
        expected_version: int,
        now: datetime,
    ) -> str:
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        if current.status not in {
            ReviewStatus.DRAFT,
            ReviewStatus.CHECKING,
            ReviewStatus.REVIEWING,
            ReviewStatus.APPROVED,
        }:
            raise ReviewStateConflict("flowchart-correction-not-editable")
        if current.expires_at <= now or self._parser is None:
            raise ReviewStateConflict("flowchart-correction-unavailable")
        target, prior = await self._flowchart_artifact(current)
        assert self._artifacts is not None
        content = await self._artifacts.read_original(target.original_locator)
        if canonical_sha256(content) != prior.source_hash:
            raise ReviewStateConflict("review-original-changed")
        correction_digest = canonical_digest(
            {"sourceRevisionId": str(prior.revision_id), "graph": graph}
        )
        assert prior.document_id is not None
        revision_id = str(
            revision_id_for(
                prior.document_id, prior.source_hash, "human-correction-" + correction_digest[7:]
            )
        )
        original_source = DocumentSource(
            prior.filename,
            prior.media_type,
            content,
            prior.document_id,
            revision_id_for(prior.document_id, prior.source_hash, PARSER_VERSION),
        )
        parsed = await self._parser.parse(original_source)
        source = replace(original_source, revision_id=RevisionId(revision_id))
        rebased_inventory = tuple(
            ParseInventoryItem.create(
                source_revision_id=revision_id,
                kind=item.kind,
                locator=item.locator,
                status=item.status,
                artifact_digest=item.artifact_digest,
                reason=item.reason,
                original_alignment_reason=item.original_alignment_reason,
            )
            for item in parsed.parse_inventory
        )
        parsed = replace(
            parsed,
            revision_id=RevisionId(revision_id),
            parse_inventory=rebased_inventory,
            parse_inventory_digest=parse_inventory_digest(rebased_inventory),
        )
        if parsed.vision_input is None:
            raise ReviewStateConflict("flowchart-correction-unavailable")
        try:
            corrected = normalize_flowchart(
                source, parsed, parsed.vision_input.original_size, graph
            )
        except FlowchartRejected as error:
            raise ReviewStateConflict("invalid-flowchart-correction") from error
        corrected = replace(
            corrected,
            parser_version="human-correction-" + correction_digest[7:],
            correction_source_revision_id=str(prior.revision_id),
        )
        if corrected.flowchart_data == prior.flowchart_data:
            raise ReviewStateConflict("flowchart-correction-unchanged")
        locator = await self._artifacts.write_normalized(revision_id, corrected)
        invalidated = replace(
            current,
            status=ReviewStatus.NEEDS_REVIEW,
            version=current.version + 1,
            approved_item_ids=(),
            blocking_item_ids=tuple(item.item_id for item in prior.parse_inventory),
            reviewer_actor_id=None,
            annotation_digest=correction_digest,
            editor_actor_ids=tuple(sorted(set((*current.editor_actor_ids, actor_id)))),
        )
        await self._repository.save_flowchart_correction(
            invalidated,
            corrected,
            locator,
            expected_version=expected_version,
            actor_id=actor_id,
            occurred_at=now,
        )
        return revision_id

    async def resolve_open_review(self, *, document_id: str, source_revision_id: str) -> str:
        return await self._repository.resolve_open_review_target(
            document_id=document_id,
            source_revision_id=source_revision_id,
        )

    async def open_review(
        self,
        *,
        document_id: str,
        source_revision_id: str,
        authorized_review_id: str,
        actor_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> KnowledgeReviewRevision:
        command_digest = canonical_digest(
            {
                "actorId": actor_id,
                "documentId": document_id,
                "operation": "open-review",
                "sourceRevisionId": source_revision_id,
            }
        )
        return await self._repository.create_or_open_review(
            document_id=document_id,
            source_revision_id=source_revision_id,
            authorized_review_id=authorized_review_id,
            actor_id=actor_id,
            expires_at=now + timedelta(days=30),
            command_key=idempotency_key,
            command_digest=command_digest,
            now=now,
        )

    async def transition_review(
        self,
        review_id: str,
        *,
        target: ReviewStatus,
        actor_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> KnowledgeReviewRevision:
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        effective_now = datetime.now(UTC) if now is None else now
        allowed = {
            ReviewStatus.DRAFT: frozenset({ReviewStatus.CHECKING}),
            ReviewStatus.CHECKING: frozenset({ReviewStatus.REVIEWING}),
            ReviewStatus.REVIEWING: frozenset({ReviewStatus.CHECKING}),
            ReviewStatus.NEEDS_REVIEW: frozenset({ReviewStatus.CHECKING}),
        }
        if target not in allowed.get(current.status, frozenset()):
            raise ReviewStateConflict("invalid-review-transition")
        if current.expires_at <= effective_now:
            raise ReviewStateConflict("review-expired")
        if target is ReviewStatus.REVIEWING and current.blocking_item_ids:
            raise ReviewStateConflict("review-has-blockers")
        if target is ReviewStatus.REVIEWING and not current.approved_item_ids:
            raise ReviewStateConflict("review-has-no-approved-items")
        await self._require_review_authority(current)
        editors = current.editor_actor_ids
        if target is ReviewStatus.CHECKING and actor_id not in editors:
            editors = (*editors, actor_id)
        transitioned = replace(
            current,
            status=target,
            editor_actor_ids=editors,
            reviewer_actor_id=None,
            version=current.version + 1,
        )
        return await self._repository.save_review(
            transitioned,
            expected_version=expected_version,
            actor_id=actor_id,
            action="submitted" if target is ReviewStatus.REVIEWING else "returned",
            occurred_at=effective_now,
        )

    async def get_review(self, review_id: str) -> KnowledgeReviewRead:
        revision = await self._required_review(review_id)
        return KnowledgeReviewRead(
            revision=revision,
            decisions=await self._repository.list_decisions(review_id),
            decision_history=await self._repository.list_decision_history(review_id),
            history=await self._repository.list_history(review_id),
        )

    async def list_reviews(
        self,
        source_revision_id: str | None = None,
        *,
        limit: int = 50,
        after_review_id: str | None = None,
    ) -> tuple[KnowledgeReviewRead, ...]:
        revisions = await self._repository.list_reviews(
            source_revision_id,
            limit=limit,
            after_review_id=after_review_id,
        )
        values = await self._repository.review_list_batch(revisions, child_limit=501)
        return tuple(item.review for item in values)

    async def list_review_details(
        self,
        source_revision_id: str | None = None,
        *,
        limit: int = 50,
        after_review_id: str | None = None,
        child_limit: int = 100,
    ) -> tuple[KnowledgeReviewListItemRead, ...]:
        revisions = await self._repository.list_reviews(
            source_revision_id,
            limit=limit,
            after_review_id=after_review_id,
        )
        values = await self._repository.review_list_batch(revisions, child_limit=child_limit)
        discover_many = getattr(self._projection, "generations_for", None)
        if discover_many is not None:
            generations = await discover_many(revisions)
        else:
            generations = {
                revision.review_id: await self.publication_generation(revision)
                for revision in revisions
            }
        return tuple(
            replace(value, generation=generations.get(value.review.revision.review_id))
            for value in values
        )

    async def list_publications(self, review_id: str) -> tuple[KnowledgePublication, ...]:
        await self._required_review(review_id)
        return await self._repository.list_publications(review_id)

    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]:
        await self._required_review(review_id)
        return await self._repository.list_inventory(review_id)

    async def inventory_page(
        self,
        review_id: str,
        *,
        limit: int,
        after_item_id: str | None,
    ) -> ReviewInventoryPageRead:
        await self._required_review(review_id)
        return await self._repository.inventory_page(
            review_id, limit=limit, after_item_id=after_item_id
        )

    async def decision_history_page(
        self,
        review_id: str,
        *,
        limit: int,
        after_version: int | None,
    ) -> ReviewDecisionPageRead:
        await self._required_review(review_id)
        return await self._repository.decision_history_page(
            review_id, limit=limit, after_version=after_version
        )

    async def history_page(
        self,
        review_id: str,
        *,
        limit: int,
        after_version: int | None,
    ) -> ReviewHistoryPageRead:
        await self._required_review(review_id)
        return await self._repository.history_page(
            review_id, limit=limit, after_version=after_version
        )

    async def publication_page(
        self,
        review_id: str,
        *,
        limit: int,
        after_publication_id: str | None,
    ) -> ReviewPublicationPageRead:
        await self._required_review(review_id)
        return await self._repository.publication_page(
            review_id,
            limit=limit,
            after_publication_id=after_publication_id,
        )

    async def get_publication(self, publication_id: str) -> KnowledgePublication:
        publication = await self._repository.get_publication(publication_id)
        if publication is None:
            raise ReviewNotFound("publication-not-found")
        return publication

    async def current_publication(self) -> KnowledgePublication:
        publication = await self._repository.current_publication()
        if publication is None:
            raise ReviewNotFound("publication-not-found")
        return publication

    async def current_publication_for_review(
        self, revision: KnowledgeReviewRevision
    ) -> KnowledgePublication | None:
        return next(
            (
                item
                for item in await self._repository.current_publications(revision.project_id)
                if item.review_id == revision.review_id
            ),
            None,
        )

    async def publication_generation(self, revision: KnowledgeReviewRevision) -> str | None:
        discover = getattr(self._projection, "generation_for", None)
        if discover is None:
            return None
        typed_discover = cast(Callable[[KnowledgeReviewRevision], Awaitable[str | None]], discover)
        return await typed_discover(revision)

    async def review_capabilities(
        self,
        revision: KnowledgeReviewRevision,
        *,
        current_publication: KnowledgePublication | None,
        generation: str | None,
        actor_id: str,
        now: datetime,
        authoritative: bool | None = None,
    ) -> frozenset[str]:
        if authoritative is None:
            authoritative = await self._repository.review_authority(revision)
        active = revision.expires_at > now
        values: set[str] = {"read_original"}
        if (
            authoritative
            and active
            and revision.status
            in {ReviewStatus.DRAFT, ReviewStatus.CHECKING, ReviewStatus.NEEDS_REVIEW}
        ):
            values.add("edit")
        if (
            authoritative
            and active
            and revision.status is ReviewStatus.CHECKING
            and not revision.blocking_item_ids
            and bool(revision.approved_item_ids)
        ):
            values.add("submit")
        if authoritative and active and revision.status is ReviewStatus.REVIEWING:
            values.add("return")
            if (
                not revision.blocking_item_ids
                and bool(revision.approved_item_ids)
                and actor_id not in revision.editor_actor_ids
            ):
                values.add("approve")
        if (
            authoritative
            and active
            and revision.status is ReviewStatus.APPROVED
            and generation is not None
        ):
            values.add("publish")
        if current_publication is not None and current_publication.status == "published":
            values.add("withdraw")
        return frozenset(values)

    async def list_published_sources(self, *, now: datetime) -> tuple[PublishedSourceRecord, ...]:
        return await self._repository.list_published_sources(now=now)

    async def compare_review_item(self, review_id: str, item_id: str) -> ReviewItemComparisonRead:
        await self._required_review(review_id)
        target = await self._repository.comparison_target(review_id, item_id)
        if target is None:
            raise ReviewNotFound("review-item-not-found")
        if self._artifacts is None:
            original = ReviewPreview("unavailable", reason="artifact-runtime-unavailable")
            extracted = ReviewPreview("unavailable", reason="artifact-runtime-unavailable")
        else:
            original = await self._original_preview(target)
            extracted = await self._extracted_preview(target)
        return ReviewItemComparisonRead(review_id, item_id, original, extracted)

    async def read_original_image(self, review_id: str, item_id: str) -> tuple[bytes, str]:
        review = await self._required_review(review_id)
        target = await self._repository.comparison_target(review_id, item_id)
        if (
            target is None
            or target.item_id != item_id
            or target.source_revision_id not in review.source_revision_ids
            or target.media_type not in {"image/png", "image/jpeg"}
            or not target.original_alignment_valid
            or self._artifacts is None
        ):
            raise ReviewNotFound("review-original-image-not-found")
        try:
            data = await self._artifacts.read_original(target.original_locator)
        except ArtifactError:
            raise ReviewNotFound("review-original-image-not-found") from None
        expected_signature = (
            b"\x89PNG\r\n\x1a\n" if target.media_type == "image/png" else b"\xff\xd8\xff"
        )
        if (
            not data.startswith(expected_signature)
            or len(data) > MAX_UPLOAD_BYTES
            or canonical_sha256(data) != target.source_digest
        ):
            raise ReviewNotFound("review-original-image-not-found")
        return data, target.media_type

    async def read_original(self, review_id: str, item_id: str) -> tuple[bytes, str]:
        """Read the immutable source only after resolving a scoped review item."""
        from tap.modules.knowledge.domain.documents import (
            MAX_UPLOAD_BYTES,
            MediaType,
            canonical_sha256,
        )

        await self._required_review(review_id)
        target = await self._repository.comparison_target(review_id, item_id)
        if target is None or self._artifacts is None or not target.original_alignment_valid:
            raise ReviewNotFound("review-original-not-found")
        try:
            media_type = MediaType(target.media_type)
            content = await self._artifacts.read_original(target.original_locator)
        except (ArtifactError, ValueError):
            raise ReviewNotFound("review-original-not-found") from None
        if len(content) > MAX_UPLOAD_BYTES or canonical_sha256(content) != target.source_digest:
            raise ReviewNotFound("review-original-not-found")
        return content, media_type.value

    async def _original_preview(self, target: ReviewComparisonTarget) -> ReviewPreview:
        if not target.original_alignment_valid:
            return ReviewPreview("unavailable", reason="item-alignment-invalid")
        if target.original_excerpt is None:
            if target.original_alignment_reason is None:
                return ReviewPreview("unavailable", reason="item-aligned-original-unavailable")
            return ReviewPreview("unsupported", reason=target.original_alignment_reason)
        if target.original_excerpt.source_digest != target.source_digest:
            return ReviewPreview("unavailable", reason="original-preview-unavailable")
        assert self._artifacts is not None
        try:
            excerpt = await self._artifacts.read_original_excerpt(
                target.original_locator,
                revision_id=target.source_revision_id,
                source_digest=target.source_digest,
                start_byte=target.original_excerpt.start_byte,
                end_byte=target.original_excerpt.end_byte,
                excerpt_digest=target.original_excerpt.excerpt_digest,
            )
            text = excerpt.decode("utf-8")
        except (ArtifactError, UnicodeError):
            return ReviewPreview("unavailable", reason="original-preview-unavailable")
        return ReviewPreview("available", excerpt=text[:4_000])

    async def _extracted_preview(self, target: ReviewComparisonTarget) -> ReviewPreview:
        if target.normalized_locator is None:
            return ReviewPreview("unavailable", reason="not-extracted")
        assert self._artifacts is not None
        try:
            artifact = await self._artifacts.read_normalized(target.normalized_locator)
        except ArtifactError:
            return ReviewPreview("unavailable", reason="extraction-preview-unavailable")
        excerpt = "\n".join(
            block.text for block in artifact.blocks if block.inventory_item_id == target.item_id
        )[:4_000]
        if not excerpt:
            return ReviewPreview("unavailable", reason="item-text-unavailable")
        return ReviewPreview("available", excerpt=excerpt)

    async def record_item_decision(
        self,
        review_id: str,
        *,
        item_id: str,
        check_kind: ReviewCheckKind,
        status: ReviewDecisionStatus,
        note: str,
        actor_id: str,
        expected_version: int,
        now: datetime,
    ) -> KnowledgeReviewRevision:
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        if current.expires_at <= now:
            raise ReviewStateConflict("review-expired")
        await self._require_review_authority(current)
        if current.status not in {
            ReviewStatus.DRAFT,
            ReviewStatus.CHECKING,
            ReviewStatus.NEEDS_REVIEW,
        }:
            raise ReviewStateConflict("review-not-editable")
        if not await self._repository.has_review_item(review_id, item_id):
            raise ReviewStateConflict("review-item-not-found")
        decisions = {
            item.item_id: item for item in await self._repository.list_decisions(review_id)
        }
        inventory = await self._repository.list_inventory(review_id)
        unresolved = {
            item.item_id
            for item in inventory
            if item.status == "needs_review" and item.item_id not in decisions
        }
        selected = next((item for item in inventory if item.item_id == item_id), None)
        if (
            selected is not None
            and selected.reason == "uncertain-connection"
            and status is ReviewDecisionStatus.ACCEPTED
        ):
            raise ReviewStateConflict("uncertain-connection-requires-correction")
        next_version = current.version + 1
        decision = KnowledgeReviewItemDecision(
            review_id=review_id,
            item_id=item_id,
            check_kind=check_kind,
            status=status,
            note=note,
            actor_id=actor_id,
            review_version=next_version,
            decided_at=now,
        )
        decisions[item_id] = decision
        unresolved.discard(item_id)
        blocking = tuple(
            sorted(
                unresolved
                | {
                    item.item_id
                    for item in decisions.values()
                    if item.status is ReviewDecisionStatus.BLOCKED
                }
            )
        )
        approved = tuple(
            sorted(
                item.item_id
                for item in decisions.values()
                if item.status is ReviewDecisionStatus.ACCEPTED
            )
        )
        # Preserve parser-approved items that have not received an explicit decision.
        decided_ids = set(decisions)
        approved = tuple(sorted(set(approved) | (set(current.approved_item_ids) - decided_ids)))
        editors = current.editor_actor_ids
        if actor_id not in editors:
            editors = (*editors, actor_id)
        updated = replace(
            current,
            annotation_digest=canonical_digest(
                [
                    {
                        "checkKind": item.check_kind.value,
                        "itemId": item.item_id,
                        "note": item.note,
                        "status": item.status.value,
                    }
                    for item in sorted(decisions.values(), key=lambda value: value.item_id)
                ]
            ),
            editor_actor_ids=editors,
            reviewer_actor_id=None,
            status=ReviewStatus.CHECKING,
            version=next_version,
            blocking_item_ids=blocking,
            approved_item_ids=approved,
        )
        return await self._repository.save_item_decision(
            updated, decision, expected_version=expected_version
        )

    async def approve_review(
        self,
        review_id: str,
        *,
        actor_id: str,
        expected_version: int,
        now: datetime,
    ) -> KnowledgeReviewRevision:
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        if current.status is not ReviewStatus.REVIEWING:
            raise ReviewStateConflict("review-not-reviewing")
        if actor_id in current.editor_actor_ids:
            raise ReviewStateConflict("separation-of-duties")
        if current.blocking_item_ids:
            raise ReviewStateConflict("review-has-blockers")
        if not current.approved_item_ids:
            raise ReviewStateConflict("review-has-no-approved-items")
        inventory = await self._repository.list_inventory(review_id)
        visual_items = {
            item.item_id
            for item in inventory
            if item.kind in {ParseInventoryKind.FLOW_NODE.value, ParseInventoryKind.FLOW_EDGE.value}
        }
        if visual_items and not visual_items.intersection(current.approved_item_ids):
            raise ReviewStateConflict("review-has-no-approved-items")
        await self._require_review_authority(current)
        if current.expires_at <= now:
            raise ReviewStateConflict("review-expired")
        approved = replace(
            current,
            status=ReviewStatus.APPROVED,
            reviewer_actor_id=actor_id,
            version=current.version + 1,
        )
        return await self._repository.save_review(
            approved,
            expected_version=expected_version,
            actor_id=actor_id,
            action="approved",
            occurred_at=now,
        )

    async def record_dependency_change(
        self, review_id: str, *, dependency_digest: str, expected_version: int
    ) -> KnowledgeReviewRevision:
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        if dependency_digest == current.dependency_digest:
            return current
        changed = replace(
            current,
            dependency_digest=dependency_digest,
            status=ReviewStatus.NEEDS_REVIEW,
            reviewer_actor_id=None,
            version=current.version + 1,
        )
        return await self._repository.save_review(
            changed,
            expected_version=expected_version,
            actor_id="system",
            action="dependency_changed",
            occurred_at=datetime.now(UTC),
        )

    async def publish_review(
        self,
        review_id: str,
        *,
        generation: str,
        idempotency_key: str,
        actor_id: str,
        expected_version: int,
        now: datetime,
    ) -> KnowledgePublication:
        command_digest = canonical_digest(
            {
                "actorId": actor_id,
                "generation": generation,
                "operation": "publish",
                "reviewId": review_id,
                "expectedVersion": expected_version,
            }
        )
        replay = await self._repository.command_result(
            idempotency_key,
            command_digest,
            legacy_digest=canonical_digest(
                {
                    "actorId": actor_id,
                    "generation": generation,
                    "operation": "publish",
                    "reviewId": review_id,
                }
            ),
        )
        if replay is not None:
            return replay
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        if current.status is not ReviewStatus.APPROVED:
            raise ReviewStateConflict("review-not-approved")
        if current.expires_at <= now:
            raise ReviewStateConflict("review-expired")
        await self._require_review_authority(current)
        if not await self._projection.verify(current, generation):
            raise ProjectionNotReady("projection-not-ready")
        publication = KnowledgePublication(
            publication_id=publication_id_for(current, generation),
            project_id=current.project_id,
            review_id=current.review_id,
            review_version=current.version,
            approval_digest=current.approval_digest,
            source_revision_ids=current.source_revision_ids,
            approved_item_ids=current.approved_item_ids,
            generation=generation,
            published_by=actor_id,
            published_at=now,
            expires_at=current.expires_at,
        )
        return await self._repository.publish(
            current,
            publication,
            expected_version=current.version,
            command_key=idempotency_key,
            command_digest=command_digest,
        )

    async def withdraw_publication(
        self,
        publication_id: str,
        *,
        idempotency_key: str,
        actor_id: str,
        expected_version: int,
        now: datetime,
    ) -> KnowledgePublication:
        command_digest = canonical_digest(
            {
                "actorId": actor_id,
                "operation": "withdraw",
                "publicationId": publication_id,
                "expectedVersion": expected_version,
            }
        )
        replay = await self._repository.command_result(
            idempotency_key,
            command_digest,
            legacy_digest=canonical_digest(
                {
                    "actorId": actor_id,
                    "operation": "withdraw",
                    "publicationId": publication_id,
                }
            ),
        )
        if replay is not None:
            return replay
        publication = await self._repository.get_publication(publication_id)
        if publication is None:
            raise ReviewNotFound("publication-not-found")
        if publication.version != expected_version:
            raise ReviewStateConflict("revision-conflict")
        if publication.status == "withdrawn":
            raise ReviewStateConflict("publication-withdrawn")
        withdrawn = replace(
            publication,
            status="withdrawn",
            withdrawn_by=actor_id,
            withdrawn_at=now,
            version=publication.version + 1,
        )
        return await self._repository.withdraw(
            withdrawn,
            expected_version=expected_version,
            command_key=idempotency_key,
            command_digest=command_digest,
        )

    async def _required_review(self, review_id: str) -> KnowledgeReviewRevision:
        review = await self._repository.get_review(review_id)
        if review is None:
            raise ReviewNotFound("review-not-found")
        return review

    async def _require_review_authority(self, revision: KnowledgeReviewRevision) -> None:
        if not await self._repository.review_authority(revision):
            raise ReviewStateConflict("review-authority-changed")


class InMemoryKnowledgeReviewRepository:
    """Deterministic repository with the same atomic boundaries as the SQL adapter."""

    def __init__(self) -> None:
        self._reviews: dict[str, KnowledgeReviewRevision] = {}
        self._publications: dict[str, KnowledgePublication] = {}
        self._current: dict[str, str] = {}
        self._commands: dict[str, tuple[str, KnowledgePublication]] = {}
        self._cleanup: list[str] = []
        self._inventory: dict[str, frozenset[str]] = {}
        self._authority_valid: dict[str, bool] = {}
        self._decisions: dict[str, list[KnowledgeReviewItemDecision]] = {}
        self._history: dict[str, list[KnowledgeReviewHistoryEntry]] = {}
        self._lock = asyncio.Lock()

    async def add(
        self,
        revision: KnowledgeReviewRevision,
        *,
        inventory_item_ids: tuple[str, ...] = (),
    ) -> None:
        async with self._lock:
            self._reviews[revision.review_id] = revision
            self._inventory[revision.review_id] = frozenset(
                inventory_item_ids or (*revision.approved_item_ids, *revision.blocking_item_ids)
            )
            self._authority_valid[revision.review_id] = True
            self._decisions.setdefault(revision.review_id, [])
            self._history.setdefault(revision.review_id, [])

    async def clear(self) -> None:
        async with self._lock:
            self._reviews.clear()
            self._publications.clear()
            self._current.clear()
            self._commands.clear()
            self._cleanup.clear()
            self._inventory.clear()
            self._authority_valid.clear()
            self._decisions.clear()
            self._history.clear()

    async def get_review(self, review_id: str) -> KnowledgeReviewRevision | None:
        return self._reviews.get(review_id)

    async def list_reviews(
        self,
        source_revision_id: str | None = None,
        *,
        limit: int = 50,
        after_review_id: str | None = None,
    ) -> tuple[KnowledgeReviewRevision, ...]:
        values = tuple(
            sorted(
                (
                    item
                    for item in self._reviews.values()
                    if source_revision_id is None or source_revision_id in item.source_revision_ids
                ),
                key=lambda item: item.review_id,
            )
        )
        if after_review_id is not None:
            values = tuple(item for item in values if item.review_id > after_review_id)
        return values[:limit]

    async def review_list_batch(
        self,
        revisions: tuple[KnowledgeReviewRevision, ...],
        *,
        child_limit: int,
    ) -> tuple[KnowledgeReviewListItemRead, ...]:
        current_by_project = {
            revision.project_id: await self.current_publications(revision.project_id)
            for revision in revisions
        }
        values: list[KnowledgeReviewListItemRead] = []
        for revision in revisions:
            current = next(
                (
                    item
                    for item in current_by_project[revision.project_id]
                    if item.review_id == revision.review_id
                ),
                None,
            )
            values.append(
                KnowledgeReviewListItemRead(
                    review=KnowledgeReviewRead(
                        revision=revision,
                        decisions=await self.list_decisions(revision.review_id),
                        decision_history=await self.list_decision_history(revision.review_id),
                        history=await self.list_history(revision.review_id),
                    ),
                    inventory=await self.inventory_page(
                        revision.review_id, limit=child_limit, after_item_id=None
                    ),
                    decision_history=await self.decision_history_page(
                        revision.review_id, limit=child_limit, after_version=None
                    ),
                    history=await self.history_page(
                        revision.review_id, limit=child_limit, after_version=None
                    ),
                    publications=await self.publication_page(
                        revision.review_id,
                        limit=child_limit,
                        after_publication_id=None,
                    ),
                    current_publication=current,
                    authoritative=await self.review_authority(revision),
                )
            )
        return tuple(values)

    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]:
        return ()

    async def inventory_page(
        self, review_id: str, *, limit: int, after_item_id: str | None
    ) -> ReviewInventoryPageRead:
        all_values = await self.list_inventory(review_id)
        values = all_values
        if after_item_id is not None:
            values = tuple(item for item in values if item.item_id > after_item_id)
        page = values[: limit + 1]
        items = page[:limit]
        statuses = [item.status for item in all_values]
        return ReviewInventoryPageRead(
            items=items,
            total_count=len(all_values),
            parsed_count=statuses.count("parsed"),
            failed_count=statuses.count("failed"),
            needs_review_count=statuses.count("needs_review"),
            excluded_count=statuses.count("excluded"),
            next_cursor=items[-1].item_id if len(page) > limit else None,
        )

    async def review_authority(self, revision: KnowledgeReviewRevision) -> bool:
        inventory = self._inventory.get(revision.review_id, frozenset())
        return (
            self._authority_valid.get(revision.review_id, False)
            and bool(inventory)
            and set(revision.approved_item_ids) <= inventory
        )

    async def save_review(
        self,
        revision: KnowledgeReviewRevision,
        *,
        expected_version: int,
        actor_id: str,
        action: str,
        occurred_at: datetime,
    ) -> KnowledgeReviewRevision:
        async with self._lock:
            current = self._reviews.get(revision.review_id)
            if current is None or current.version != expected_version:
                raise ReviewStateConflict("revision-conflict")
            if action in {"submitted", "approved"} and not await self.review_authority(current):
                raise ReviewStateConflict("review-authority-changed")
            self._reviews[revision.review_id] = revision
            self._history.setdefault(revision.review_id, []).append(
                KnowledgeReviewHistoryEntry(
                    review_id=revision.review_id,
                    review_version=revision.version,
                    action=action,
                    actor_id=actor_id,
                    occurred_at=occurred_at,
                )
            )
            return revision

    async def has_review_item(self, review_id: str, item_id: str) -> bool:
        return item_id in self._inventory.get(review_id, frozenset())

    async def list_decisions(self, review_id: str) -> tuple[KnowledgeReviewItemDecision, ...]:
        latest: dict[str, KnowledgeReviewItemDecision] = {}
        for decision in self._decisions.get(review_id, []):
            latest[decision.item_id] = decision
        return tuple(latest[item_id] for item_id in sorted(latest))

    async def list_decision_history(
        self, review_id: str
    ) -> tuple[KnowledgeReviewItemDecision, ...]:
        return tuple(self._decisions.get(review_id, []))

    async def decision_history_page(
        self, review_id: str, *, limit: int, after_version: int | None
    ) -> ReviewDecisionPageRead:
        all_values = tuple(self._decisions.get(review_id, []))
        values = tuple(
            item
            for item in all_values
            if after_version is None or item.review_version > after_version
        )
        page = values[: limit + 1]
        items = page[:limit]
        return ReviewDecisionPageRead(
            items=items,
            total_count=len(all_values),
            next_cursor=items[-1].review_version if len(page) > limit else None,
        )

    async def list_history(self, review_id: str) -> tuple[KnowledgeReviewHistoryEntry, ...]:
        return tuple(self._history.get(review_id, ()))

    async def history_page(
        self, review_id: str, *, limit: int, after_version: int | None
    ) -> ReviewHistoryPageRead:
        all_values = tuple(self._history.get(review_id, ()))
        values = tuple(
            item
            for item in all_values
            if after_version is None or item.review_version > after_version
        )
        page = values[: limit + 1]
        items = page[:limit]
        return ReviewHistoryPageRead(
            items=items,
            total_count=len(all_values),
            next_cursor=items[-1].review_version if len(page) > limit else None,
        )

    async def save_item_decision(
        self,
        revision: KnowledgeReviewRevision,
        decision: KnowledgeReviewItemDecision,
        *,
        expected_version: int,
    ) -> KnowledgeReviewRevision:
        async with self._lock:
            current = self._reviews.get(revision.review_id)
            if current is None or current.version != expected_version:
                raise ReviewStateConflict("revision-conflict")
            if not await self.review_authority(current):
                raise ReviewStateConflict("review-authority-changed")
            if decision.item_id not in self._inventory.get(revision.review_id, frozenset()):
                raise ReviewStateConflict("review-item-not-found")
            self._reviews[revision.review_id] = revision
            self._decisions.setdefault(revision.review_id, []).append(decision)
            self._history.setdefault(revision.review_id, []).append(
                KnowledgeReviewHistoryEntry(
                    review_id=revision.review_id,
                    review_version=revision.version,
                    action="item_decided",
                    actor_id=decision.actor_id,
                    item_id=decision.item_id,
                    decision_id=decision.decision_id,
                    decision_digest=decision.decision_digest,
                    occurred_at=decision.decided_at,
                )
            )
            return revision

    async def command_result(
        self, key: str, digest: str, *, legacy_digest: str | None = None
    ) -> KnowledgePublication | None:
        existing = self._commands.get(key)
        if existing is None:
            return None
        if existing[0] not in {digest, legacy_digest}:
            raise ReviewCommandConflict("idempotency-conflict")
        return existing[1]

    async def publish(
        self,
        revision: KnowledgeReviewRevision,
        publication: KnowledgePublication,
        *,
        expected_version: int,
        command_key: str,
        command_digest: str,
    ) -> KnowledgePublication:
        async with self._lock:
            existing = self._commands.get(command_key)
            if existing is not None:
                if existing[0] != command_digest:
                    raise ReviewCommandConflict("idempotency-conflict")
                return existing[1]
            current = self._reviews.get(revision.review_id)
            if current is None or current.version != expected_version:
                raise ReviewStateConflict("revision-conflict")
            self._reviews[revision.review_id] = replace(
                current, status=ReviewStatus.PUBLISHED, version=current.version + 1
            )
            self._publications[publication.publication_id] = publication
            for key, identity in tuple(self._current.items()):
                previous = self._publications[identity]
                if previous.project_id == publication.project_id and set(
                    previous.source_revision_ids
                ).intersection(publication.source_revision_ids):
                    del self._current[key]
            self._current[publication.publication_id] = publication.publication_id
            self._commands[command_key] = (command_digest, publication)
            return publication

    async def get_publication(self, publication_id: str) -> KnowledgePublication | None:
        return self._publications.get(publication_id)

    async def current_publication(
        self, project_id: str | None = None
    ) -> KnowledgePublication | None:
        values = await self.current_publications(project_id)
        return max(values, key=lambda item: (item.published_at, item.publication_id), default=None)

    async def current_publications(
        self, project_id: str | None = None
    ) -> tuple[KnowledgePublication, ...]:
        return tuple(
            self._publications[identity]
            for identity in self._current.values()
            if project_id is None or self._publications[identity].project_id == project_id
        )

    async def list_publications(self, review_id: str) -> tuple[KnowledgePublication, ...]:
        return tuple(
            sorted(
                (item for item in self._publications.values() if item.review_id == review_id),
                key=lambda item: item.published_at,
            )
        )

    async def publication_page(
        self, review_id: str, *, limit: int, after_publication_id: str | None
    ) -> ReviewPublicationPageRead:
        all_values = tuple(
            sorted(
                (item for item in self._publications.values() if item.review_id == review_id),
                key=lambda item: item.publication_id,
            )
        )
        values = tuple(
            item
            for item in all_values
            if after_publication_id is None or item.publication_id > after_publication_id
        )
        page = values[: limit + 1]
        items = page[:limit]
        return ReviewPublicationPageRead(
            items=items,
            total_count=len(all_values),
            next_cursor=items[-1].publication_id if len(page) > limit else None,
        )

    async def list_published_sources(self, *, now: datetime) -> tuple[PublishedSourceRecord, ...]:
        del now
        return ()

    async def comparison_target(
        self, review_id: str, item_id: str
    ) -> ReviewComparisonTarget | None:
        del review_id, item_id
        return None

    async def withdraw(
        self,
        publication: KnowledgePublication,
        *,
        expected_version: int,
        command_key: str,
        command_digest: str,
    ) -> KnowledgePublication:
        async with self._lock:
            existing = self._commands.get(command_key)
            if existing is not None:
                if existing[0] != command_digest:
                    raise ReviewCommandConflict("idempotency-conflict")
                return existing[1]
            current = self._publications.get(publication.publication_id)
            if current is None:
                raise ReviewNotFound("publication-not-found")
            if current.version != expected_version:
                raise ReviewStateConflict("revision-conflict")
            if publication.publication_id not in self._current.values():
                raise ReviewStateConflict("publication-not-current")
            self._publications[publication.publication_id] = publication
            for key, identity in tuple(self._current.items()):
                if identity == publication.publication_id:
                    del self._current[key]
            review = self._reviews.get(publication.review_id)
            if review is not None:
                self._reviews[publication.review_id] = replace(
                    review, status=ReviewStatus.WITHDRAWN, version=review.version + 1
                )
            self._cleanup.append(publication.generation)
            self._commands[command_key] = (command_digest, publication)
            return publication

    async def pending_projection_cleanup(self) -> tuple[str, ...]:
        active = {item.generation for item in await self.current_publications()}
        return tuple(generation for generation in self._cleanup if generation not in active)


def _expected_version(review: KnowledgeReviewRevision, expected: int) -> None:
    if review.version != expected:
        raise ReviewStateConflict("revision-conflict")
