"""HTTP DTO adapter for governed knowledge review commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

from tap.contracts.http import (
    KnowledgePublicationDetail,
    KnowledgePublicationPage,
    KnowledgePublicationTarget,
    KnowledgeReviewAction,
    KnowledgeReviewCheckKind,
    KnowledgeReviewDecisionPage,
    KnowledgeReviewDecisionStatus,
    KnowledgeReviewDetail,
    KnowledgeReviewHistoryDetail,
    KnowledgeReviewHistoryPage,
    KnowledgeReviewInventory,
    KnowledgeReviewInventoryItem,
    KnowledgeReviewItemComparison,
    KnowledgeReviewItemDecisionDetail,
    KnowledgeReviewPage,
    KnowledgeReviewPreview,
    KnowledgeReviewStatus,
    KnowledgeReviewSummary,
    PublishedKnowledgeSource,
    PublishedKnowledgeSourcePage,
)
from tap.modules.access.application.ports import AuthorizationPolicy
from tap.modules.access.domain.authorization import ResourceRef
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import PolicyUnavailable
from tap.modules.knowledge.application.review import (
    KnowledgeReviewApplication,
    KnowledgeReviewListItemRead,
    KnowledgeReviewRead,
    ReviewDecisionPageRead,
    ReviewHistoryPageRead,
    ReviewInventoryPageRead,
    ReviewPublicationPageRead,
)
from tap.modules.knowledge.domain.review import (
    KnowledgePublication,
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
)


class KnowledgeReviewHttpService:
    def __init__(
        self,
        application: KnowledgeReviewApplication,
        *,
        scope: ProjectScopeContext,
        authorization_policy: AuthorizationPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        source_impact_notifier: (
            Callable[[tuple[str, ...], str, str, datetime], Awaitable[object]] | None
        ) = None,
    ) -> None:
        self._application = application
        self._scope = scope
        self._authorization_policy = authorization_policy
        self._clock = clock
        self._source_impact_notifier = source_impact_notifier

    @property
    def scope(self) -> ProjectScopeContext:
        return self._scope

    async def resolve_open_review(self, document_id: str, source_revision_id: str) -> str:
        return await self._application.resolve_open_review(
            document_id=document_id,
            source_revision_id=source_revision_id,
        )

    async def open_review(
        self,
        document_id: str,
        source_revision_id: str,
        authorized_review_id: str,
        key: str,
    ) -> KnowledgeReviewDetail:
        revision = await self._application.open_review(
            document_id=document_id,
            source_revision_id=source_revision_id,
            authorized_review_id=authorized_review_id,
            actor_id=self._scope.actor_id,
            idempotency_key=key,
            now=self._clock(),
        )
        return await self.get_review(revision.review_id)

    async def list_reviews(
        self,
        source_revision_id: str | None,
        limit: int = 50,
        after_review_id: str | None = None,
    ) -> KnowledgeReviewPage:
        values = await self._application.list_review_details(
            source_revision_id,
            limit=limit + 1,
            after_review_id=after_review_id,
        )
        authorization_policy = self._request_authorization_policy()
        return KnowledgeReviewPage(
            items=[
                await self._batched_review_detail(item, authorization_policy=authorization_policy)
                for item in values[:limit]
            ],
            next_cursor=(
                values[limit - 1].review.revision.review_id if len(values) > limit else None
            ),
        )

    async def get_review(self, review_id: str) -> KnowledgeReviewDetail:
        return await self._review_detail(
            await self._application.get_review(review_id),
            authorization_policy=self._request_authorization_policy(),
        )

    async def get_review_inventory(
        self, review_id: str, limit: int = 100, after_item_id: str | None = None
    ) -> KnowledgeReviewInventory:
        page = await self._application.inventory_page(
            review_id, limit=limit, after_item_id=after_item_id
        )
        return _inventory_page_detail(page)

    async def list_review_decision_history(
        self, review_id: str, limit: int = 100, after_version: int | None = None
    ) -> KnowledgeReviewDecisionPage:
        page = await self._application.decision_history_page(
            review_id, limit=limit, after_version=after_version
        )
        return KnowledgeReviewDecisionPage(
            items=[_decision_detail(item) for item in page.items],
            total_count=page.total_count,
            next_cursor=page.next_cursor,
        )

    async def list_review_history(
        self, review_id: str, limit: int = 100, after_version: int | None = None
    ) -> KnowledgeReviewHistoryPage:
        page = await self._application.history_page(
            review_id, limit=limit, after_version=after_version
        )
        return KnowledgeReviewHistoryPage(
            items=[_history_detail(item) for item in page.items],
            total_count=page.total_count,
            next_cursor=page.next_cursor,
        )

    async def list_review_publications(
        self,
        review_id: str,
        limit: int = 100,
        after_publication_id: str | None = None,
    ) -> KnowledgePublicationPage:
        page = await self._application.publication_page(
            review_id,
            limit=limit,
            after_publication_id=after_publication_id,
        )
        return KnowledgePublicationPage(
            items=[_publication_detail(item) for item in page.items],
            total_count=page.total_count,
            next_cursor=page.next_cursor,
        )

    async def get_publication(self, publication_id: str) -> KnowledgePublicationDetail:
        return _publication_detail(await self._application.get_publication(publication_id))

    async def get_current_publication(self) -> KnowledgePublicationDetail:
        return _publication_detail(await self._application.current_publication())

    async def list_published_sources(self) -> PublishedKnowledgeSourcePage:
        return PublishedKnowledgeSourcePage(
            items=[
                PublishedKnowledgeSource(
                    source_id=item.source_id,
                    document_id=item.document_id,
                    revision_id=item.revision_id,
                    source_name=item.source_name,
                    filename=item.filename,
                    publication_id=item.publication_id,
                    expires_at=item.expires_at.isoformat(),
                    approved_item_count=item.approved_item_count,
                    inventory_item_count=item.inventory_item_count,
                    partial=item.approved_item_count < item.inventory_item_count,
                )
                for item in await self._application.list_published_sources(now=self._clock())
            ]
        )

    async def compare_review_item(
        self, review_id: str, item_id: str
    ) -> KnowledgeReviewItemComparison:
        value = await self._application.compare_review_item(review_id, item_id)
        return KnowledgeReviewItemComparison(
            review_id=value.review_id,
            item_id=value.item_id,
            original=KnowledgeReviewPreview(
                availability=value.original.availability,
                excerpt=value.original.excerpt,
                reason=value.original.reason,
            ),
            extracted=KnowledgeReviewPreview(
                availability=value.extracted.availability,
                excerpt=value.extracted.excerpt,
                reason=value.extracted.reason,
            ),
        )

    async def update_item_decision(
        self,
        review_id: str,
        item_id: str,
        body: dict[str, object],
        expected_version: int,
    ) -> KnowledgeReviewDetail:
        await self._application.record_item_decision(
            review_id,
            item_id=item_id,
            check_kind=ReviewCheckKind(str(body["checkKind"])),
            status=ReviewDecisionStatus(str(body["status"])),
            note=str(body["note"]),
            actor_id=self._scope.actor_id,
            expected_version=expected_version,
            now=self._clock(),
        )
        return await self.get_review(review_id)

    async def return_review(self, review_id: str, expected_version: int) -> KnowledgeReviewDetail:
        await self._application.transition_review(
            review_id,
            target=ReviewStatus.CHECKING,
            actor_id=self._scope.actor_id,
            expected_version=expected_version,
            now=self._clock(),
        )
        return await self.get_review(review_id)

    async def submit_review(self, review_id: str, expected_version: int) -> KnowledgeReviewDetail:
        await self._application.transition_review(
            review_id,
            target=ReviewStatus.REVIEWING,
            actor_id=self._scope.actor_id,
            expected_version=expected_version,
            now=self._clock(),
        )
        return await self.get_review(review_id)

    async def approve_review(self, review_id: str, expected_version: int) -> KnowledgeReviewSummary:
        revision = await self._application.approve_review(
            review_id,
            actor_id=self._scope.actor_id,
            expected_version=expected_version,
            now=self._clock(),
        )
        return _review_summary(revision)

    async def publish_review(
        self, review_id: str, generation: str, expected_version: int, key: str
    ) -> KnowledgePublicationDetail:
        publication = await self._application.publish_review(
            review_id,
            generation=generation,
            idempotency_key=key,
            actor_id=self._scope.actor_id,
            expected_version=expected_version,
            now=self._clock(),
        )
        return _publication_detail(publication)

    async def withdraw_publication(
        self, publication_id: str, expected_version: int, key: str
    ) -> KnowledgePublicationDetail:
        now = self._clock()
        publication = await self._application.withdraw_publication(
            publication_id,
            idempotency_key=key,
            actor_id=self._scope.actor_id,
            expected_version=expected_version,
            now=now,
        )
        if self._source_impact_notifier is not None and publication.source_revision_ids:
            await self._source_impact_notifier(
                publication.source_revision_ids,
                "approved knowledge publication withdrawn",
                f"withdraw:{key}",
                now,
            )
        return _publication_detail(publication)

    async def _review_detail(
        self,
        value: KnowledgeReviewRead,
        *,
        authorization_policy: AuthorizationPolicy,
    ) -> KnowledgeReviewDetail:
        revision = value.revision
        inventory = await self._application.inventory_page(
            revision.review_id, limit=100, after_item_id=None
        )
        decision_history = await self._application.decision_history_page(
            revision.review_id, limit=100, after_version=None
        )
        history = await self._application.history_page(
            revision.review_id, limit=100, after_version=None
        )
        publications = await self._application.publication_page(
            revision.review_id, limit=100, after_publication_id=None
        )
        current = await self._application.current_publication_for_review(revision)
        generation = await self._application.publication_generation(revision)
        allowed = await self._allowed_actions(
            revision,
            current,
            generation,
            authorization_policy=authorization_policy,
        )
        return _review_detail_from_parts(
            value,
            inventory=inventory,
            decision_history=decision_history,
            history=history,
            publications=publications,
            current=current,
            generation=generation,
            allowed=allowed,
        )

    async def _batched_review_detail(
        self,
        value: KnowledgeReviewListItemRead,
        *,
        authorization_policy: AuthorizationPolicy,
    ) -> KnowledgeReviewDetail:
        revision = value.review.revision
        allowed = await self._allowed_actions(
            revision,
            value.current_publication,
            value.generation,
            authoritative=value.authoritative,
            authorization_policy=authorization_policy,
        )
        return _review_detail_from_parts(
            value.review,
            inventory=value.inventory,
            decision_history=value.decision_history,
            history=value.history,
            publications=value.publications,
            current=value.current_publication,
            generation=value.generation,
            allowed=allowed,
        )

    async def _allowed_actions(
        self,
        revision: KnowledgeReviewRevision,
        current: KnowledgePublication | None,
        generation: str | None,
        *,
        authoritative: bool | None = None,
        authorization_policy: AuthorizationPolicy | None = None,
    ) -> list[KnowledgeReviewAction]:
        applicable = await self._application.review_capabilities(
            revision,
            current_publication=current,
            generation=generation,
            actor_id=self._scope.actor_id,
            now=self._clock(),
            authoritative=authoritative,
        )
        candidates: list[tuple[KnowledgeReviewAction, str, str, str]] = [
            (
                KnowledgeReviewAction.EDIT,
                "knowledge.review.edit",
                "knowledge-review",
                revision.review_id,
            ),
            (
                KnowledgeReviewAction.SUBMIT,
                "knowledge.review.edit",
                "knowledge-review",
                revision.review_id,
            ),
            (
                KnowledgeReviewAction.RETURN,
                "knowledge.review.edit",
                "knowledge-review",
                revision.review_id,
            ),
            (
                KnowledgeReviewAction.APPROVE,
                "knowledge.review.approve",
                "knowledge-review",
                revision.review_id,
            ),
            (
                KnowledgeReviewAction.PUBLISH,
                "knowledge.publish",
                "knowledge-publication",
                revision.review_id,
            ),
            (
                KnowledgeReviewAction.WITHDRAW,
                "knowledge.publish",
                "knowledge-publication",
                current.publication_id if current is not None else revision.review_id,
            ),
            (
                KnowledgeReviewAction.READ_ORIGINAL,
                "knowledge.original.read",
                "knowledge-original",
                revision.review_id,
            ),
        ]
        allowed: list[KnowledgeReviewAction] = []
        for public, action, kind, resource_id in candidates:
            if public.value not in applicable:
                continue
            try:
                decision = await (authorization_policy or self._authorization_policy).authorize(
                    self._scope,
                    action,
                    ResourceRef(
                        enterprise_id=self._scope.enterprise_id,
                        project_id=self._scope.project_id,
                        kind=kind,
                        resource_id=resource_id,
                    ),
                )
            except Exception as error:
                raise PolicyUnavailable("current authorization policy is unavailable") from error
            if decision.allowed:
                allowed.append(public)
        return allowed

    def _request_authorization_policy(self) -> AuthorizationPolicy:
        factory = getattr(self._authorization_policy, "for_request", None)
        if factory is None:
            return self._authorization_policy
        return cast(AuthorizationPolicy, factory())


def _review_detail_from_parts(
    value: KnowledgeReviewRead,
    *,
    inventory: ReviewInventoryPageRead,
    decision_history: ReviewDecisionPageRead,
    history: ReviewHistoryPageRead,
    publications: ReviewPublicationPageRead,
    current: KnowledgePublication | None,
    generation: str | None,
    allowed: list[KnowledgeReviewAction],
) -> KnowledgeReviewDetail:
    revision = value.revision
    return KnowledgeReviewDetail(
        **_review_summary(revision).model_dump(),
        source_revision_ids=list(revision.source_revision_ids),
        editor_actor_ids=list(revision.editor_actor_ids),
        blocking_item_ids=list(revision.blocking_item_ids),
        approved_item_ids=list(revision.approved_item_ids),
        inventory=_inventory_page_detail(inventory),
        decisions=[_decision_detail(item) for item in value.decisions[:500]],
        decision_history=[_decision_detail(item) for item in decision_history.items],
        decision_history_total_count=decision_history.total_count,
        decision_history_next_cursor=decision_history.next_cursor,
        history=[_history_detail(item) for item in history.items],
        history_total_count=history.total_count,
        history_next_cursor=history.next_cursor,
        publication_ids=[item.publication_id for item in publications.items],
        publication_total_count=publications.total_count,
        publication_next_cursor=publications.next_cursor,
        current_publication=None if current is None else _publication_detail(current),
        publication_target=KnowledgePublicationTarget(
            status="ready" if generation is not None else "unavailable",
            generation=generation,
            reason=None if generation is not None else "projection-not-ready",
        ),
        allowed_actions=allowed,
    )


def _review_summary(value: KnowledgeReviewRevision) -> KnowledgeReviewSummary:
    return KnowledgeReviewSummary(
        review_id=value.review_id,
        status=KnowledgeReviewStatus(value.status.value),
        version=value.version,
        reviewer_actor_id=value.reviewer_actor_id,
        expires_at=value.expires_at.isoformat(),
        approval_digest=value.approval_digest,
    )


def _decision_detail(value) -> KnowledgeReviewItemDecisionDetail:  # type: ignore[no-untyped-def]
    return KnowledgeReviewItemDecisionDetail(
        decision_id=value.decision_id,
        decision_digest=value.decision_digest,
        item_id=value.item_id,
        check_kind=KnowledgeReviewCheckKind(value.check_kind.value),
        status=KnowledgeReviewDecisionStatus(value.status.value),
        note=value.note,
        actor_id=value.actor_id,
        review_version=value.review_version,
        decided_at=value.decided_at.isoformat(),
    )


def _history_detail(value) -> KnowledgeReviewHistoryDetail:  # type: ignore[no-untyped-def]
    return KnowledgeReviewHistoryDetail(
        review_version=value.review_version,
        action=value.action,
        actor_id=value.actor_id,
        occurred_at=value.occurred_at.isoformat(),
        item_id=value.item_id,
        decision_id=value.decision_id,
        decision_digest=value.decision_digest,
    )


def _inventory_page_detail(value: ReviewInventoryPageRead) -> KnowledgeReviewInventory:
    return KnowledgeReviewInventory(
        items=[
            KnowledgeReviewInventoryItem(
                source_revision_id=item.source_revision_id,
                item_id=item.item_id,
                attempt=item.attempt,
                kind=item.kind,  # type: ignore[arg-type]
                locator=item.locator,
                status=item.status,  # type: ignore[arg-type]
                artifact_digest=item.artifact_digest,
                reason=item.reason,
                decision_actor_id=item.decision_actor_id,
            )
            for item in value.items
        ],
        total_count=value.total_count,
        parsed_count=value.parsed_count,
        failed_count=value.failed_count,
        needs_review_count=value.needs_review_count,
        excluded_count=value.excluded_count,
        next_cursor=value.next_cursor,
    )


def _publication_detail(value: KnowledgePublication) -> KnowledgePublicationDetail:
    return KnowledgePublicationDetail(
        publication_id=value.publication_id,
        review_id=value.review_id,
        review_version=value.review_version,
        version=value.version,
        status=value.status,
        generation=value.generation,
        approval_digest=value.approval_digest,
        source_revision_ids=list(value.source_revision_ids),
        approved_item_ids=list(value.approved_item_ids),
        published_at=value.published_at.isoformat(),
        expires_at=value.expires_at.isoformat(),
        withdrawn_by=value.withdrawn_by,
        withdrawn_at=None if value.withdrawn_at is None else value.withdrawn_at.isoformat(),
    )
