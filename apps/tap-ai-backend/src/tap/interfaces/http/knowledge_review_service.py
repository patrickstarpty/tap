"""HTTP DTO adapter for governed knowledge review commands."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from tap.contracts.http import (
    KnowledgePublicationDetail,
    KnowledgeReviewStatus,
    KnowledgeReviewSummary,
)
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
from tap.modules.knowledge.domain.review import KnowledgePublication, KnowledgeReviewRevision


class KnowledgeReviewHttpService:
    def __init__(
        self,
        application: KnowledgeReviewApplication,
        *,
        scope: ProjectScopeContext,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._application = application
        self._scope = scope
        self._clock = clock

    @property
    def scope(self) -> ProjectScopeContext:
        return self._scope

    async def approve_review(self, review_id: str, expected_version: int) -> KnowledgeReviewSummary:
        revision = await self._application.approve_review(
            review_id,
            actor_id=self._scope.actor_id,
            expected_version=expected_version,
            now=self._clock(),
        )
        return _review_summary(revision)

    async def publish_review(
        self, review_id: str, generation: str, key: str
    ) -> KnowledgePublicationDetail:
        publication = await self._application.publish_review(
            review_id,
            generation=generation,
            idempotency_key=key,
            actor_id=self._scope.actor_id,
            now=self._clock(),
        )
        return _publication_detail(publication)

    async def withdraw_publication(
        self, publication_id: str, key: str
    ) -> KnowledgePublicationDetail:
        publication = await self._application.withdraw_publication(
            publication_id,
            idempotency_key=key,
            actor_id=self._scope.actor_id,
            now=self._clock(),
        )
        return _publication_detail(publication)


def _review_summary(value: KnowledgeReviewRevision) -> KnowledgeReviewSummary:
    return KnowledgeReviewSummary(
        review_id=value.review_id,
        status=KnowledgeReviewStatus(value.status.value),
        version=value.version,
        reviewer_actor_id=value.reviewer_actor_id,
        expires_at=value.expires_at.isoformat(),
        approval_digest=value.approval_digest,
    )


def _publication_detail(value: KnowledgePublication) -> KnowledgePublicationDetail:
    return KnowledgePublicationDetail(
        publication_id=value.publication_id,
        review_id=value.review_id,
        review_version=value.review_version,
        status=value.status,
        generation=value.generation,
        approval_digest=value.approval_digest,
        source_revision_ids=list(value.source_revision_ids),
        approved_item_ids=list(value.approved_item_ids),
        published_at=value.published_at.isoformat(),
    )
