from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, update

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_parse_inventory,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_projection import knowledge_projection_state
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.application.review import (
    KnowledgeReviewApplication,
    ProjectionNotReady,
    ReviewCommandConflict,
)
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.review import (
    KnowledgeReviewRevision,
    ReviewStatus,
    review_dependency_digest,
)
from tap.platform.db.project_scope import scope_values
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 23, 9, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
ITEM = ParseInventoryItem.create(
    source_revision_id="rev_mysql_001",
    kind=ParseInventoryKind.PARAGRAPH,
    locator="paragraph:1",
    status=ParseInventoryStatus.PARSED,
    artifact_digest=DIGEST_A,
)
DEPENDENCY_DIGEST = review_dependency_digest((("rev_mysql_001", DIGEST_A, DIGEST_C, DIGEST_C),))


class ReadyProjection:
    async def verify(self, revision, generation):  # type: ignore[no-untyped-def]
        return generation.startswith("generation-")


async def seed_authority(sessions):  # type: ignore[no-untyped-def]
    async with sessions() as session, session.begin():
        await session.execute(
            insert(knowledge_source).values(
                **scope_values(VALIDATION_SCOPE),
                source_id="src_" + "2" * 32,
                name="publication-source",
                created_at=NOW.replace(tzinfo=None),
                updated_at=NOW.replace(tzinfo=None),
                deleted_at=None,
            )
        )
        await session.execute(
            insert(knowledge_document).values(
                **scope_values(VALIDATION_SCOPE),
                document_id="doc_mysql_publication",
                source_id="src_" + "2" * 32,
                filename="publication.md",
                media_type="text/markdown",
                current_revision_id=None,
                source_content_hash=DIGEST_A,
                dedupe_key=DIGEST_B,
                staging_blob_locator=None,
                promoted_blob_locator=None,
                reservation_owner_token=None,
                reservation_expires_at=None,
                reservation_parser_version="tapper-parser-v1",
                reservation_chunker_version="tapper-chunker-v1",
                reservation_pipeline_version="tapper-ingestion-v1",
                status="ready",
                stage="ready",
                chunk_count=1,
                error_code=None,
                error_summary=None,
                activated_at=NOW.replace(tzinfo=None),
                created_at=NOW.replace(tzinfo=None),
                updated_at=NOW.replace(tzinfo=None),
                deleted_at=None,
            )
        )
        await session.execute(
            insert(knowledge_document_revision).values(
                **scope_values(VALIDATION_SCOPE),
                revision_id="rev_mysql_001",
                source_id="src_" + "2" * 32,
                document_id="doc_mysql_publication",
                source_content_hash=DIGEST_A,
                original_blob_locator="tapper-originals/publication.md",
                normalized_blob_locator="tapper-artifacts/publication.normalized",
                chunks_blob_locator=None,
                embeddings_blob_locator=None,
                parser_version="tapper-parser-v1",
                chunker_version="tapper-chunker-v1",
                pipeline_version="tapper-ingestion-v1",
                parse_inventory_attempt=1,
                parser_config_digest=DIGEST_C,
                parse_inventory_digest=parse_inventory_digest((ITEM,)),
                chunk_manifest_digest=DIGEST_B,
                projection_digest=DIGEST_C,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        await session.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == "doc_mysql_publication")
            .values(current_revision_id="rev_mysql_001")
        )
        await session.execute(
            insert(knowledge_parse_inventory).values(
                **scope_values(VALIDATION_SCOPE),
                inventory_row_id="inventory-publication-001",
                source_revision_id="rev_mysql_001",
                attempt=1,
                item_id=ITEM.item_id,
                ordinal=0,
                item_kind=ITEM.kind.value,
                locator=ITEM.locator,
                status=ITEM.status.value,
                reason=None,
                artifact_digest=ITEM.artifact_digest,
                decision_actor_id=None,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        await session.execute(
            insert(knowledge_projection_state).values(
                **scope_values(VALIDATION_SCOPE),
                alias_name="knowledge",
                generation=1,
                physical_collection="generation-001",
                updated_at=NOW.replace(tzinfo=None),
            )
        )


def approved_review(review_id: str = "krv_mysql_001") -> KnowledgeReviewRevision:
    return KnowledgeReviewRevision(
        review_id=review_id,
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("rev_mysql_001",),
        inventory_digest=parse_inventory_digest((ITEM,)),
        chunk_manifest_digest=DIGEST_B,
        annotation_digest=DIGEST_C,
        dependency_digest=DEPENDENCY_DIGEST,
        editor_actor_ids=("synthetic-editor-01",),
        reviewer_actor_id="synthetic-reviewer-02",
        expires_at=NOW + timedelta(days=30),
        status=ReviewStatus.APPROVED,
        version=4,
        blocking_item_ids=(),
        approved_item_ids=(ITEM.item_id,),
    )


async def test_publication_cutover_replays_after_restart_and_withdraws_authority(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await seed_authority(sessions)
        first_repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await first_repository.create_review(approved_review())
        first_application = KnowledgeReviewApplication(first_repository, ReadyProjection())
        published = await first_application.publish_review(
            "krv_mysql_001",
            generation="generation-001",
            idempotency_key="publish-mysql-001",
            actor_id="synthetic-reviewer-02",
            expected_version=4,
            now=NOW,
        )

        restarted_repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        restarted_application = KnowledgeReviewApplication(restarted_repository, ReadyProjection())
        replay = await restarted_application.publish_review(
            "krv_mysql_001",
            generation="generation-001",
            idempotency_key="publish-mysql-001",
            actor_id="synthetic-reviewer-02",
            expected_version=4,
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
                expected_version=4,
                now=NOW,
            )

        await restarted_repository.create_review(approved_review("krv_mysql_002"))
        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_projection_state)
                .where(knowledge_projection_state.c.alias_name == "knowledge")
                .values(
                    generation=2,
                    physical_collection="generation-002",
                    updated_at=(NOW + timedelta(seconds=1)).replace(tzinfo=None),
                )
            )
        concurrent = await asyncio.gather(
            restarted_application.publish_review(
                "krv_mysql_002",
                generation="generation-002",
                idempotency_key="publish-mysql-002",
                actor_id="synthetic-reviewer-02",
                expected_version=4,
                now=NOW + timedelta(seconds=1),
            ),
            restarted_application.publish_review(
                "krv_mysql_002",
                generation="generation-002",
                idempotency_key="publish-mysql-002",
                actor_id="synthetic-reviewer-02",
                expected_version=4,
                now=NOW + timedelta(seconds=1),
            ),
        )
        assert concurrent[0] == concurrent[1]
        assert await restarted_repository.current_publication() == concurrent[0]

        await restarted_application.withdraw_publication(
            concurrent[0].publication_id,
            idempotency_key="withdraw-mysql-001",
            actor_id="synthetic-publisher-03",
            expected_version=1,
            now=NOW + timedelta(minutes=1),
        )
        assert await restarted_repository.current_publication() is None
        assert await restarted_repository.pending_projection_cleanup() == ("generation-002",)
    finally:
        await engine.dispose()


async def test_first_publish_and_publish_withdraw_interleaving_share_one_project_lock(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await seed_authority(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, ReadyProjection())
        await repository.create_review(approved_review("krv_first_a"))
        await repository.create_review(approved_review("krv_first_b"))

        first_a, first_b = await asyncio.wait_for(
            asyncio.gather(
                application.publish_review(
                    "krv_first_a",
                    generation="generation-001",
                    idempotency_key="publish-first-a",
                    actor_id="synthetic-reviewer-02",
                    expected_version=4,
                    now=NOW,
                ),
                application.publish_review(
                    "krv_first_b",
                    generation="generation-001",
                    idempotency_key="publish-first-b",
                    actor_id="synthetic-reviewer-02",
                    expected_version=4,
                    now=NOW,
                ),
            ),
            timeout=10,
        )
        current = await repository.current_publication()
        assert current in {first_a, first_b}

        await repository.create_review(approved_review("krv_interleaved"))
        published, withdrawn = await asyncio.wait_for(
            asyncio.gather(
                application.publish_review(
                    "krv_interleaved",
                    generation="generation-001",
                    idempotency_key="publish-interleaved",
                    actor_id="synthetic-reviewer-02",
                    expected_version=4,
                    now=NOW + timedelta(seconds=1),
                ),
                application.withdraw_publication(
                    current.publication_id,
                    idempotency_key="withdraw-interleaved-old",
                    actor_id="synthetic-reviewer-02",
                    expected_version=1,
                    now=NOW + timedelta(seconds=2),
                ),
            ),
            timeout=10,
        )
        assert withdrawn.status == "withdrawn"
        assert await repository.current_publication() == published
    finally:
        await engine.dispose()


async def test_publish_revalidates_projection_generation_inside_commit_transaction(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)

    class RacingProjection:
        async def verify(self, revision, generation):  # type: ignore[no-untyped-def]
            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_projection_state)
                    .where(knowledge_projection_state.c.alias_name == "knowledge")
                    .values(generation=2, physical_collection="generation-002")
                )
            return True

    try:
        await seed_authority(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await repository.create_review(approved_review("krv_projection_race"))
        application = KnowledgeReviewApplication(repository, RacingProjection())

        with pytest.raises(ProjectionNotReady, match="projection-not-ready"):
            await application.publish_review(
                "krv_projection_race",
                generation="generation-001",
                idempotency_key="publish-projection-race",
                actor_id="synthetic-reviewer-02",
                expected_version=4,
                now=NOW,
            )
        assert await repository.current_publication() is None
    finally:
        await engine.dispose()
