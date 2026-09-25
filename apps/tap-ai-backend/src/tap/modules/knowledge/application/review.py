"""Approval, publication, and withdrawal orchestration for governed knowledge."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol

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
from tap.modules.knowledge.ports.documents import ArtifactLocator, ArtifactStore
from tap.modules.knowledge.ports.errors import ArtifactUnavailable


class ReviewStateConflict(Exception):
    """A review transition is invalid for the current immutable revision."""


class ReviewCommandConflict(Exception):
    """An idempotency key was reused for a different publication intent."""


class ReviewNotFound(Exception):
    """A review or publication is unavailable in this Project."""


class ProjectionNotReady(Exception):
    """The proposed immutable projection generation did not validate."""


class ProjectionVerifier(Protocol):
    async def verify(self, revision: KnowledgeReviewRevision, generation: str) -> bool: ...

    async def generation_for(self, revision: KnowledgeReviewRevision) -> str | None: ...


@dataclass(frozen=True, slots=True)
class KnowledgeReviewRead:
    revision: KnowledgeReviewRevision
    decisions: tuple[KnowledgeReviewItemDecision, ...]
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
    original_locator: ArtifactLocator
    normalized_locator: ArtifactLocator | None
    item_id: str


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
    async def get_review(self, review_id: str) -> KnowledgeReviewRevision | None: ...
    async def list_reviews(
        self, source_revision_id: str | None = None
    ) -> tuple[KnowledgeReviewRevision, ...]: ...
    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]: ...
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
    async def list_history(self, review_id: str) -> tuple[KnowledgeReviewHistoryEntry, ...]: ...
    async def save_item_decision(
        self,
        revision: KnowledgeReviewRevision,
        decision: KnowledgeReviewItemDecision,
        *,
        expected_version: int,
    ) -> KnowledgeReviewRevision: ...
    async def command_result(self, key: str, digest: str) -> KnowledgePublication | None: ...
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
    async def list_publications(self, review_id: str) -> tuple[KnowledgePublication, ...]: ...
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
    ) -> None:
        self._repository = repository
        self._projection = projection
        self._artifacts = artifacts

    async def transition_review(
        self,
        review_id: str,
        *,
        target: ReviewStatus,
        actor_id: str,
        expected_version: int,
    ) -> KnowledgeReviewRevision:
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        allowed = {
            ReviewStatus.DRAFT: frozenset({ReviewStatus.CHECKING}),
            ReviewStatus.CHECKING: frozenset({ReviewStatus.REVIEWING}),
            ReviewStatus.REVIEWING: frozenset({ReviewStatus.CHECKING}),
            ReviewStatus.NEEDS_REVIEW: frozenset({ReviewStatus.CHECKING}),
        }
        if target not in allowed.get(current.status, frozenset()):
            raise ReviewStateConflict("invalid-review-transition")
        if target is ReviewStatus.REVIEWING and current.blocking_item_ids:
            raise ReviewStateConflict("review-has-blockers")
        if target is ReviewStatus.REVIEWING and not current.approved_item_ids:
            raise ReviewStateConflict("review-has-no-approved-items")
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
            occurred_at=datetime.now(UTC),
        )

    async def get_review(self, review_id: str) -> KnowledgeReviewRead:
        revision = await self._required_review(review_id)
        return KnowledgeReviewRead(
            revision=revision,
            decisions=await self._repository.list_decisions(review_id),
            history=await self._repository.list_history(review_id),
        )

    async def list_reviews(
        self, source_revision_id: str | None = None
    ) -> tuple[KnowledgeReviewRead, ...]:
        revisions = await self._repository.list_reviews(source_revision_id)
        return tuple([await self.get_review(item.review_id) for item in revisions])

    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]:
        await self._required_review(review_id)
        return await self._repository.list_inventory(review_id)

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
        publication = await self._repository.current_publication(revision.project_id)
        if publication is None or publication.review_id != revision.review_id:
            return None
        return publication

    async def publication_generation(self, revision: KnowledgeReviewRevision) -> str | None:
        discover = getattr(self._projection, "generation_for", None)
        if discover is None:
            return None
        return await discover(revision)

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

    async def _original_preview(self, target: ReviewComparisonTarget) -> ReviewPreview:
        if target.media_type not in {"text/plain", "text/markdown"}:
            return ReviewPreview("unsupported", reason="preview-not-supported")
        assert self._artifacts is not None
        try:
            content = await self._artifacts.read_original(target.original_locator)
            text = content.decode("utf-8")
        except (ArtifactUnavailable, UnicodeDecodeError, OSError):
            return ReviewPreview("unavailable", reason="original-preview-unavailable")
        return ReviewPreview("available", excerpt=text[:4_000])

    async def _extracted_preview(self, target: ReviewComparisonTarget) -> ReviewPreview:
        if target.normalized_locator is None:
            return ReviewPreview("unavailable", reason="not-extracted")
        assert self._artifacts is not None
        try:
            artifact = await self._artifacts.read_normalized(target.normalized_locator)
        except ArtifactUnavailable:
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
        blocking = tuple(
            sorted(
                item.item_id
                for item in decisions.values()
                if item.status is ReviewDecisionStatus.BLOCKED
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
        replay = await self._repository.command_result(idempotency_key, command_digest)
        if replay is not None:
            return replay
        current = await self._required_review(review_id)
        _expected_version(current, expected_version)
        if current.status is not ReviewStatus.APPROVED:
            raise ReviewStateConflict("review-not-approved")
        if current.expires_at <= now:
            raise ReviewStateConflict("review-expired")
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
        replay = await self._repository.command_result(idempotency_key, command_digest)
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


class InMemoryKnowledgeReviewRepository:
    """Deterministic repository with the same atomic boundaries as the SQL adapter."""

    def __init__(self) -> None:
        self._reviews: dict[str, KnowledgeReviewRevision] = {}
        self._publications: dict[str, KnowledgePublication] = {}
        self._current: dict[str, str] = {}
        self._commands: dict[str, tuple[str, KnowledgePublication]] = {}
        self._cleanup: list[str] = []
        self._inventory: dict[str, frozenset[str]] = {}
        self._decisions: dict[str, dict[str, KnowledgeReviewItemDecision]] = {}
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
            self._decisions.setdefault(revision.review_id, {})
            self._history.setdefault(revision.review_id, [])

    async def clear(self) -> None:
        async with self._lock:
            self._reviews.clear()
            self._publications.clear()
            self._current.clear()
            self._commands.clear()
            self._cleanup.clear()
            self._inventory.clear()
            self._decisions.clear()
            self._history.clear()

    async def get_review(self, review_id: str) -> KnowledgeReviewRevision | None:
        return self._reviews.get(review_id)

    async def list_reviews(
        self, source_revision_id: str | None = None
    ) -> tuple[KnowledgeReviewRevision, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._reviews.values()
                    if source_revision_id is None or source_revision_id in item.source_revision_ids
                ),
                key=lambda item: item.review_id,
            )
        )

    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]:
        return ()

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
        return tuple(
            sorted(self._decisions.get(review_id, {}).values(), key=lambda item: item.item_id)
        )

    async def list_history(self, review_id: str) -> tuple[KnowledgeReviewHistoryEntry, ...]:
        return tuple(self._history.get(review_id, ()))

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
            if decision.item_id not in self._inventory.get(revision.review_id, frozenset()):
                raise ReviewStateConflict("review-item-not-found")
            self._reviews[revision.review_id] = revision
            self._decisions.setdefault(revision.review_id, {})[decision.item_id] = decision
            self._history.setdefault(revision.review_id, []).append(
                KnowledgeReviewHistoryEntry(
                    review_id=revision.review_id,
                    review_version=revision.version,
                    action="item_decided",
                    actor_id=decision.actor_id,
                    item_id=decision.item_id,
                    occurred_at=decision.decided_at,
                )
            )
            return revision

    async def command_result(self, key: str, digest: str) -> KnowledgePublication | None:
        existing = self._commands.get(key)
        if existing is None:
            return None
        if existing[0] != digest:
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
            self._current[publication.project_id] = publication.publication_id
            self._commands[command_key] = (command_digest, publication)
            return publication

    async def get_publication(self, publication_id: str) -> KnowledgePublication | None:
        return self._publications.get(publication_id)

    async def current_publication(
        self, project_id: str | None = None
    ) -> KnowledgePublication | None:
        if project_id is None:
            identities = tuple(self._current.values())
            identity = identities[0] if len(identities) == 1 else None
        else:
            identity = self._current.get(project_id)
        return None if identity is None else self._publications[identity]

    async def list_publications(self, review_id: str) -> tuple[KnowledgePublication, ...]:
        return tuple(
            sorted(
                (item for item in self._publications.values() if item.review_id == review_id),
                key=lambda item: item.published_at,
            )
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
            self._publications[publication.publication_id] = publication
            if self._current.get(publication.project_id) == publication.publication_id:
                del self._current[publication.project_id]
            review = self._reviews.get(publication.review_id)
            if review is not None:
                self._reviews[publication.review_id] = replace(
                    review, status=ReviewStatus.WITHDRAWN, version=review.version + 1
                )
            self._cleanup.append(publication.generation)
            self._commands[command_key] = (command_digest, publication)
            return publication

    async def pending_projection_cleanup(self) -> tuple[str, ...]:
        return tuple(self._cleanup)


def _expected_version(review: KnowledgeReviewRevision, expected: int) -> None:
    if review.version != expected:
        raise ReviewStateConflict("revision-conflict")
