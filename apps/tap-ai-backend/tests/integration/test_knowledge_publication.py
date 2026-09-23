from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.application.review import (
    KnowledgeReviewApplication,
    ReviewCommandConflict,
)
from tap.modules.knowledge.domain.review import KnowledgeReviewRevision, ReviewStatus
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 23, 9, tzinfo=UTC)


class ReadyProjection:
    async def verify(self, revision, generation):  # type: ignore[no-untyped-def]
        return generation.startswith("generation-")


def approved_review(review_id: str = "krv_mysql_001") -> KnowledgeReviewRevision:
    return KnowledgeReviewRevision(
        review_id=review_id,
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("rev_mysql_001",),
        inventory_digest="sha256:" + "a" * 64,
        chunk_manifest_digest="sha256:" + "b" * 64,
        annotation_digest="sha256:" + "c" * 64,
        dependency_digest="sha256:" + "d" * 64,
        editor_actor_ids=("synthetic-editor-01",),
        reviewer_actor_id="synthetic-reviewer-02",
        expires_at=NOW + timedelta(days=30),
        status=ReviewStatus.APPROVED,
        version=4,
        blocking_item_ids=(),
        approved_item_ids=("pi_mysql_001",),
    )


async def test_publication_cutover_replays_after_restart_and_withdraws_authority(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        first_repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await first_repository.create_review(approved_review())
        first_application = KnowledgeReviewApplication(first_repository, ReadyProjection())
        published = await first_application.publish_review(
            "krv_mysql_001",
            generation="generation-001",
            idempotency_key="publish-mysql-001",
            actor_id="synthetic-reviewer-02",
            now=NOW,
        )

        restarted_repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        restarted_application = KnowledgeReviewApplication(restarted_repository, ReadyProjection())
        replay = await restarted_application.publish_review(
            "krv_mysql_001",
            generation="generation-001",
            idempotency_key="publish-mysql-001",
            actor_id="synthetic-reviewer-02",
            now=NOW,
        )
        assert replay == published
        assert await restarted_repository.current_publication() == published

        with pytest.raises(ReviewCommandConflict, match="idempotency-conflict"):
            await restarted_application.publish_review(
                "krv_mysql_001",
                generation="generation-changed",
                idempotency_key="publish-mysql-001",
                actor_id="synthetic-reviewer-02",
                now=NOW,
            )

        await restarted_repository.create_review(approved_review("krv_mysql_002"))
        concurrent = await asyncio.gather(
            restarted_application.publish_review(
                "krv_mysql_002",
                generation="generation-002",
                idempotency_key="publish-mysql-002",
                actor_id="synthetic-reviewer-02",
                now=NOW + timedelta(seconds=1),
            ),
            restarted_application.publish_review(
                "krv_mysql_002",
                generation="generation-002",
                idempotency_key="publish-mysql-002",
                actor_id="synthetic-reviewer-02",
                now=NOW + timedelta(seconds=1),
            ),
        )
        assert concurrent[0] == concurrent[1]
        assert await restarted_repository.current_publication() == concurrent[0]

        await restarted_application.withdraw_publication(
            concurrent[0].publication_id,
            idempotency_key="withdraw-mysql-001",
            actor_id="synthetic-publisher-03",
            now=NOW + timedelta(minutes=1),
        )
        assert await restarted_repository.current_publication() is None
        assert await restarted_repository.pending_projection_cleanup() == ("generation-002",)
    finally:
        await engine.dispose()
