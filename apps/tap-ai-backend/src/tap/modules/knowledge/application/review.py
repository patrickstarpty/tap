"""Approval, publication, and withdrawal orchestration for governed knowledge."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from tap.modules.knowledge.domain.review import (
    KnowledgePublication,
    KnowledgeReviewRevision,
    ReviewStatus,
    canonical_digest,
    publication_id_for,
)


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


class KnowledgeReviewRepository(Protocol):
    async def get_review(self, review_id: str) -> KnowledgeReviewRevision | None: ...
    async def save_review(
        self, revision: KnowledgeReviewRevision, *, expected_version: int
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
    async def withdraw(
        self,
        publication: KnowledgePublication,
        *,
        command_key: str,
        command_digest: str,
    ) -> KnowledgePublication: ...


class KnowledgeReviewApplication:
    def __init__(
        self, repository: KnowledgeReviewRepository, projection: ProjectionVerifier
    ) -> None:
        self._repository = repository
        self._projection = projection

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
        return await self._repository.save_review(transitioned, expected_version=expected_version)

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
        if current.expires_at <= now:
            raise ReviewStateConflict("review-expired")
        approved = replace(
            current,
            status=ReviewStatus.APPROVED,
            reviewer_actor_id=actor_id,
            version=current.version + 1,
        )
        return await self._repository.save_review(approved, expected_version=expected_version)

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
        return await self._repository.save_review(changed, expected_version=expected_version)

    async def publish_review(
        self,
        review_id: str,
        *,
        generation: str,
        idempotency_key: str,
        actor_id: str,
        now: datetime,
    ) -> KnowledgePublication:
        command_digest = canonical_digest(
            {
                "actorId": actor_id,
                "generation": generation,
                "operation": "publish",
                "reviewId": review_id,
            }
        )
        replay = await self._repository.command_result(idempotency_key, command_digest)
        if replay is not None:
            return replay
        current = await self._required_review(review_id)
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
        now: datetime,
    ) -> KnowledgePublication:
        command_digest = canonical_digest(
            {
                "actorId": actor_id,
                "operation": "withdraw",
                "publicationId": publication_id,
            }
        )
        replay = await self._repository.command_result(idempotency_key, command_digest)
        if replay is not None:
            return replay
        publication = await self._repository.get_publication(publication_id)
        if publication is None:
            raise ReviewNotFound("publication-not-found")
        if publication.status == "withdrawn":
            raise ReviewStateConflict("publication-withdrawn")
        withdrawn = replace(
            publication,
            status="withdrawn",
            withdrawn_by=actor_id,
            withdrawn_at=now,
        )
        return await self._repository.withdraw(
            withdrawn,
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
        self._lock = asyncio.Lock()

    async def add(self, revision: KnowledgeReviewRevision) -> None:
        async with self._lock:
            self._reviews[revision.review_id] = revision

    async def clear(self) -> None:
        async with self._lock:
            self._reviews.clear()
            self._publications.clear()
            self._current.clear()
            self._commands.clear()
            self._cleanup.clear()

    async def get_review(self, review_id: str) -> KnowledgeReviewRevision | None:
        return self._reviews.get(review_id)

    async def save_review(
        self, revision: KnowledgeReviewRevision, *, expected_version: int
    ) -> KnowledgeReviewRevision:
        async with self._lock:
            current = self._reviews.get(revision.review_id)
            if current is None or current.version != expected_version:
                raise ReviewStateConflict("revision-conflict")
            self._reviews[revision.review_id] = revision
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

    async def current_publication(self, project_id: str) -> KnowledgePublication | None:
        identity = self._current.get(project_id)
        return None if identity is None else self._publications[identity]

    async def withdraw(
        self,
        publication: KnowledgePublication,
        *,
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
