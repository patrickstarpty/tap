from __future__ import annotations

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
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.application.review import KnowledgeReviewApplication, ReviewStateConflict
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.review import (
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.platform.db.project_scope import scope_values
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 25, 9, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


class ReadyProjection:
    async def verify(self, revision, generation):  # type: ignore[no-untyped-def]
        return generation == "generation-001"

    async def generation_for(self, revision):  # type: ignore[no-untyped-def]
        return "generation-001"


async def _seed_revision(sessions):  # type: ignore[no-untyped-def]
    parsed = ParseInventoryItem.create(
        source_revision_id="rev_mysql_001",
        kind=ParseInventoryKind.PARAGRAPH,
        locator="paragraph:1",
        status=ParseInventoryStatus.PARSED,
        artifact_digest=DIGEST_A,
    )
    failed = ParseInventoryItem.create(
        source_revision_id="rev_mysql_001",
        kind=ParseInventoryKind.IMAGE,
        locator="page:2/image:1",
        status=ParseInventoryStatus.FAILED,
        reason="ocr-required",
        artifact_digest=DIGEST_B,
    )
    async with sessions() as session, session.begin():
        await session.execute(
            insert(knowledge_source).values(
                **scope_values(VALIDATION_SCOPE),
                source_id="src_" + "1" * 32,
                name="退款规则",
                created_at=NOW.replace(tzinfo=None),
                updated_at=NOW.replace(tzinfo=None),
                deleted_at=None,
            )
        )
        await session.execute(
            insert(knowledge_document).values(
                **scope_values(VALIDATION_SCOPE),
                document_id="doc_mysql_001",
                source_id="src_" + "1" * 32,
                filename="refund.md",
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
                source_id="src_" + "1" * 32,
                document_id="doc_mysql_001",
                source_content_hash=DIGEST_A,
                original_blob_locator="tapper-originals/refund.md",
                normalized_blob_locator="tapper-artifacts/refund.normalized",
                chunks_blob_locator=None,
                embeddings_blob_locator=None,
                parser_version="tapper-parser-v1",
                chunker_version="tapper-chunker-v1",
                pipeline_version="tapper-ingestion-v1",
                parse_inventory_attempt=2,
                parser_config_digest=DIGEST_C,
                parse_inventory_digest=parse_inventory_digest((parsed, failed)),
                chunk_manifest_digest=DIGEST_B,
                projection_digest=DIGEST_C,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        await session.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == "doc_mysql_001")
            .values(current_revision_id="rev_mysql_001")
        )
        for ordinal, item in enumerate((parsed, failed)):
            await session.execute(
                insert(knowledge_parse_inventory).values(
                    **scope_values(VALIDATION_SCOPE),
                    inventory_row_id=f"inventory-{ordinal}",
                    source_revision_id=item.source_revision_id,
                    attempt=2,
                    item_id=item.item_id,
                    ordinal=ordinal,
                    item_kind=item.kind.value,
                    locator=item.locator,
                    status=item.status.value,
                    reason=item.reason,
                    artifact_digest=item.artifact_digest,
                    decision_actor_id=item.decision_actor_id,
                    created_at=NOW.replace(tzinfo=None),
                )
            )
        await session.execute(
            insert(knowledge_parse_inventory).values(
                **scope_values(VALIDATION_SCOPE),
                inventory_row_id="inventory-historical",
                source_revision_id=parsed.source_revision_id,
                attempt=1,
                item_id=parsed.item_id,
                ordinal=0,
                item_kind=parsed.kind.value,
                locator=parsed.locator,
                status=parsed.status.value,
                reason=parsed.reason,
                artifact_digest=parsed.artifact_digest,
                decision_actor_id=parsed.decision_actor_id,
                created_at=NOW.replace(tzinfo=None),
            )
        )
    return parsed, failed


async def test_review_state_decisions_history_and_picker_survive_repository_restart(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        parsed, failed = await _seed_revision(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await repository.create_review(
            KnowledgeReviewRevision(
                review_id="krv_mysql_read_001",
                project_id=VALIDATION_SCOPE.project_id,
                source_revision_ids=("rev_mysql_001",),
                inventory_digest=parse_inventory_digest((parsed, failed)),
                chunk_manifest_digest=DIGEST_B,
                annotation_digest=DIGEST_C,
                dependency_digest=DIGEST_A,
                editor_actor_ids=("synthetic-editor-01",),
                reviewer_actor_id=None,
                expires_at=NOW + timedelta(days=30),
                status=ReviewStatus.CHECKING,
                version=1,
                blocking_item_ids=(failed.item_id,),
                approved_item_ids=(parsed.item_id,),
            )
        )
        application = KnowledgeReviewApplication(repository, ReadyProjection())
        decided = await application.record_item_decision(
            "krv_mysql_read_001",
            item_id=failed.item_id,
            check_kind=ReviewCheckKind.EXCEPTION,
            status=ReviewDecisionStatus.EXCLUDED,
            note="扫描图像不在本次发布范围",
            actor_id="synthetic-editor-01",
            expected_version=1,
            now=NOW,
        )
        submitted = await application.transition_review(
            decided.review_id,
            target=ReviewStatus.REVIEWING,
            actor_id="synthetic-editor-01",
            expected_version=2,
        )
        approved = await application.approve_review(
            submitted.review_id,
            actor_id="synthetic-reviewer-02",
            expected_version=3,
            now=NOW,
        )
        published = await application.publish_review(
            approved.review_id,
            generation="generation-001",
            idempotency_key="publish-read-model-001",
            actor_id="synthetic-publisher-03",
            expected_version=4,
            now=NOW,
        )

        restarted = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        read = await KnowledgeReviewApplication(restarted, ReadyProjection()).get_review(
            "krv_mysql_read_001"
        )
        inventory = await restarted.list_inventory("krv_mysql_read_001")
        picker = await restarted.list_published_sources(now=NOW)
        comparison = await restarted.comparison_target("krv_mysql_read_001", parsed.item_id)

        assert read.revision.status is ReviewStatus.PUBLISHED
        assert [(item.item_id, item.status.value) for item in read.decisions] == [
            (failed.item_id, "excluded")
        ]
        assert [item.action for item in read.history] == [
            "created",
            "item_decided",
            "submitted",
            "approved",
            "published",
        ]
        assert [item.status for item in inventory] == ["parsed", "failed"]
        assert picker[0].publication_id == published.publication_id
        assert picker[0].approved_item_count == 1
        assert picker[0].inventory_item_count == 2
        assert comparison is not None
        assert isinstance(comparison.original_locator, ArtifactLocator)
        assert isinstance(comparison.normalized_locator, ArtifactLocator)

        with pytest.raises(ReviewStateConflict, match="revision-conflict"):
            await KnowledgeReviewApplication(restarted, ReadyProjection()).withdraw_publication(
                published.publication_id,
                idempotency_key="withdraw-stale",
                actor_id="synthetic-publisher-03",
                expected_version=2,
                now=NOW + timedelta(minutes=1),
            )
        withdrawn = await KnowledgeReviewApplication(
            restarted, ReadyProjection()
        ).withdraw_publication(
            published.publication_id,
            idempotency_key="withdraw-read-model-001",
            actor_id="synthetic-publisher-03",
            expected_version=1,
            now=NOW + timedelta(minutes=1),
        )
        assert withdrawn.status == "withdrawn" and withdrawn.version == 2
        assert await restarted.current_publication() is None
        assert (await restarted.get_publication(published.publication_id)) == withdrawn
        assert await restarted.list_published_sources(now=NOW) == ()
    finally:
        await engine.dispose()
