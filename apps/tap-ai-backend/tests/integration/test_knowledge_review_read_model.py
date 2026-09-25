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
from tap.modules.knowledge.adapters.mysql_projection import knowledge_projection_state
from tap.modules.knowledge.adapters.mysql_review import (
    MysqlKnowledgeReviewRepository,
    knowledge_publication,
    knowledge_review_history,
    knowledge_review_item_decision,
    knowledge_review_revision,
)
from tap.modules.knowledge.application.review import KnowledgeReviewApplication, ReviewStateConflict
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.review import (
    KnowledgeReviewItemDecision,
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
    review_dependency_digest,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.platform.db.project_scope import scope_values
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 25, 9, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
AUTHORITY_DEPENDENCY_DIGEST = review_dependency_digest(
    (("rev_mysql_001", DIGEST_A, DIGEST_C, DIGEST_C),)
)


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
        await session.execute(
            insert(knowledge_projection_state).values(
                **scope_values(VALIDATION_SCOPE),
                alias_name="knowledge",
                generation=1,
                physical_collection="generation-001",
                updated_at=NOW.replace(tzinfo=None),
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
                dependency_digest=AUTHORITY_DEPENDENCY_DIGEST,
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
        blocked = await application.record_item_decision(
            "krv_mysql_read_001",
            item_id=failed.item_id,
            check_kind=ReviewCheckKind.EXCEPTION,
            status=ReviewDecisionStatus.BLOCKED,
            note="首次核对仍需业务确认",
            actor_id="synthetic-editor-01",
            expected_version=1,
            now=NOW,
        )
        decided = await application.record_item_decision(
            "krv_mysql_read_001",
            item_id=failed.item_id,
            check_kind=ReviewCheckKind.EXCEPTION,
            status=ReviewDecisionStatus.EXCLUDED,
            note="扫描图像不在本次发布范围",
            actor_id="synthetic-editor-01",
            expected_version=blocked.version,
            now=NOW + timedelta(seconds=1),
        )
        submitted = await application.transition_review(
            decided.review_id,
            target=ReviewStatus.REVIEWING,
            actor_id="synthetic-editor-01",
            expected_version=3,
        )
        approved = await application.approve_review(
            submitted.review_id,
            actor_id="synthetic-reviewer-02",
            expected_version=4,
            now=NOW,
        )
        published = await application.publish_review(
            approved.review_id,
            generation="generation-001",
            idempotency_key="publish-read-model-001",
            actor_id="synthetic-publisher-03",
            expected_version=5,
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
        assert [item.status.value for item in read.decision_history] == [
            "blocked",
            "excluded",
        ]
        assert all(item.decision_id for item in read.decision_history)
        assert [item.action for item in read.history] == [
            "created",
            "item_decided",
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

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_publication)
                .where(knowledge_publication.c.publication_id == published.publication_id)
                .values(
                    approved_item_ids=[
                        parsed.item_id,
                        "pi_missing_from_latest_inventory",
                    ]
                )
            )
        assert await restarted.list_published_sources(now=NOW) == ()

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_publication)
                .where(knowledge_publication.c.publication_id == published.publication_id)
                .values(approved_item_ids=[parsed.item_id, failed.item_id])
            )
        assert await restarted.list_published_sources(now=NOW) == ()

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_publication)
                .where(knowledge_publication.c.publication_id == published.publication_id)
                .values(approved_item_ids=[parsed.item_id])
            )
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == "doc_mysql_001")
                .values(deleted_at=NOW.replace(tzinfo=None))
            )
        assert await restarted.list_published_sources(now=NOW) == ()

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
        assert await restarted.list_publications("krv_mysql_read_001") == (withdrawn,)
        assert [
            item.decision_digest
            for item in await restarted.list_decision_history("krv_mysql_read_001")
        ] == [item.decision_digest for item in read.decision_history]
        assert await restarted.list_published_sources(now=NOW) == ()
    finally:
        await engine.dispose()


async def test_latest_inventory_attempt_is_revalidated_for_submit_approve_and_publish(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        parsed, seeded_failed = await _seed_revision(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await repository.create_review(
            KnowledgeReviewRevision(
                review_id="krv_mysql_authority_001",
                project_id=VALIDATION_SCOPE.project_id,
                source_revision_ids=("rev_mysql_001",),
                inventory_digest=parse_inventory_digest((parsed, seeded_failed)),
                chunk_manifest_digest=DIGEST_B,
                annotation_digest=DIGEST_C,
                dependency_digest=AUTHORITY_DEPENDENCY_DIGEST,
                editor_actor_ids=("synthetic-editor-01",),
                reviewer_actor_id=None,
                expires_at=NOW + timedelta(days=30),
                status=ReviewStatus.CHECKING,
                version=1,
                blocking_item_ids=(),
                approved_item_ids=(parsed.item_id,),
            )
        )
        application = KnowledgeReviewApplication(repository, ReadyProjection())

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_parse_inventory)
                .where(knowledge_parse_inventory.c.inventory_row_id == "inventory-0")
                .values(artifact_digest=DIGEST_C)
            )
        with pytest.raises(ReviewStateConflict, match="review-authority-changed"):
            await application.transition_review(
                "krv_mysql_authority_001",
                target=ReviewStatus.REVIEWING,
                actor_id="synthetic-editor-01",
                expected_version=1,
            )
        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_parse_inventory)
                .where(knowledge_parse_inventory.c.inventory_row_id == "inventory-0")
                .values(artifact_digest=DIGEST_A)
            )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_mysql_001")
                .values(parse_inventory_attempt=3, parse_inventory_digest=DIGEST_A)
            )
        with pytest.raises(ReviewStateConflict, match="review-authority-changed"):
            await application.transition_review(
                "krv_mysql_authority_001",
                target=ReviewStatus.REVIEWING,
                actor_id="synthetic-editor-01",
                expected_version=1,
            )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_mysql_001")
                .values(
                    parse_inventory_attempt=2,
                    parse_inventory_digest=parse_inventory_digest((parsed, seeded_failed)),
                )
            )
        submitted = await application.transition_review(
            "krv_mysql_authority_001",
            target=ReviewStatus.REVIEWING,
            actor_id="synthetic-editor-01",
            expected_version=1,
        )

        failed = ParseInventoryItem.create(
            source_revision_id="rev_mysql_001",
            kind=ParseInventoryKind.PARAGRAPH,
            locator="paragraph:1",
            status=ParseInventoryStatus.FAILED,
            reason="parser-unavailable",
            artifact_digest=DIGEST_C,
        )
        async with sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_parse_inventory).values(
                    **scope_values(VALIDATION_SCOPE),
                    inventory_row_id="inventory-failed-latest",
                    source_revision_id="rev_mysql_001",
                    attempt=4,
                    item_id=failed.item_id,
                    ordinal=0,
                    item_kind=failed.kind.value,
                    locator=failed.locator,
                    status=failed.status.value,
                    reason=failed.reason,
                    artifact_digest=failed.artifact_digest,
                    decision_actor_id=None,
                    created_at=NOW.replace(tzinfo=None),
                )
            )
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_mysql_001")
                .values(
                    parse_inventory_attempt=4,
                    parse_inventory_digest=parse_inventory_digest((failed,)),
                )
            )
        with pytest.raises(ReviewStateConflict, match="review-authority-changed"):
            await application.approve_review(
                submitted.review_id,
                actor_id="synthetic-reviewer-02",
                expected_version=2,
                now=NOW,
            )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_mysql_001")
                .values(
                    parse_inventory_attempt=2,
                    parse_inventory_digest=parse_inventory_digest((parsed, seeded_failed)),
                )
            )
        approved = await application.approve_review(
            submitted.review_id,
            actor_id="synthetic-reviewer-02",
            expected_version=2,
            now=NOW,
        )

        replacement = ParseInventoryItem.create(
            source_revision_id="rev_mysql_001",
            kind=ParseInventoryKind.PARAGRAPH,
            locator="paragraph:replacement",
            status=ParseInventoryStatus.PARSED,
            artifact_digest=DIGEST_B,
        )
        async with sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_parse_inventory).values(
                    **scope_values(VALIDATION_SCOPE),
                    inventory_row_id="inventory-replacement-latest",
                    source_revision_id="rev_mysql_001",
                    attempt=5,
                    item_id=replacement.item_id,
                    ordinal=0,
                    item_kind=replacement.kind.value,
                    locator=replacement.locator,
                    status=replacement.status.value,
                    reason=None,
                    artifact_digest=replacement.artifact_digest,
                    decision_actor_id=None,
                    created_at=NOW.replace(tzinfo=None),
                )
            )
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_mysql_001")
                .values(
                    parse_inventory_attempt=5,
                    parse_inventory_digest=parse_inventory_digest((replacement,)),
                )
            )
        with pytest.raises(ReviewStateConflict, match="review-authority-changed"):
            await application.publish_review(
                approved.review_id,
                generation="generation-001",
                idempotency_key="publish-stale-authority",
                actor_id="synthetic-reviewer-02",
                expected_version=3,
                now=NOW,
            )
    finally:
        await engine.dispose()


async def test_current_decisions_preserve_old_blocker_beyond_501_audit_edits(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        parsed, failed = await _seed_revision(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await repository.create_review(
            KnowledgeReviewRevision(
                review_id="krv_mysql_many_decisions",
                project_id=VALIDATION_SCOPE.project_id,
                source_revision_ids=("rev_mysql_001",),
                inventory_digest=parse_inventory_digest((parsed, failed)),
                chunk_manifest_digest=DIGEST_B,
                annotation_digest=DIGEST_C,
                dependency_digest=AUTHORITY_DEPENDENCY_DIGEST,
                editor_actor_ids=("synthetic-editor-01",),
                reviewer_actor_id=None,
                expires_at=NOW + timedelta(days=30),
                status=ReviewStatus.CHECKING,
                version=1,
                blocking_item_ids=(failed.item_id,),
                approved_item_ids=(parsed.item_id,),
            )
        )
        blocker = KnowledgeReviewItemDecision(
            review_id="krv_mysql_many_decisions",
            item_id=failed.item_id,
            check_kind=ReviewCheckKind.EXCEPTION,
            status=ReviewDecisionStatus.BLOCKED,
            note="仍需业务确认",
            actor_id="synthetic-editor-01",
            review_version=2,
            decided_at=NOW,
        )
        edits = [
            KnowledgeReviewItemDecision(
                review_id="krv_mysql_many_decisions",
                item_id=parsed.item_id,
                check_kind=ReviewCheckKind.AMOUNT,
                status=ReviewDecisionStatus.ACCEPTED,
                note=f"复核 {version}",
                actor_id="synthetic-editor-01",
                review_version=version,
                decided_at=NOW + timedelta(seconds=version),
            )
            for version in range(3, 505)
        ]
        async with sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_review_item_decision),
                [
                    {
                        **scope_values(VALIDATION_SCOPE),
                        "decision_id": item.decision_id,
                        "review_id": item.review_id,
                        "item_id": item.item_id,
                        "check_kind": item.check_kind.value,
                        "status": item.status.value,
                        "note": item.note,
                        "decided_by": item.actor_id,
                        "review_version": item.review_version,
                        "decided_at": item.decided_at.replace(tzinfo=None),
                    }
                    for item in (blocker, *edits)
                ],
            )
            await session.execute(
                update(knowledge_review_revision)
                .where(knowledge_review_revision.c.review_id == "krv_mysql_many_decisions")
                .values(version=504)
            )

        updated = await KnowledgeReviewApplication(
            repository, ReadyProjection()
        ).record_item_decision(
            "krv_mysql_many_decisions",
            item_id=parsed.item_id,
            check_kind=ReviewCheckKind.AMOUNT,
            status=ReviewDecisionStatus.ACCEPTED,
            note="最终复核",
            actor_id="synthetic-editor-01",
            expected_version=504,
            now=NOW + timedelta(seconds=505),
        )

        assert updated.blocking_item_ids == (failed.item_id,)
        assert [
            (item.item_id, item.status)
            for item in await repository.list_decisions("krv_mysql_many_decisions")
        ] == [
            (failed.item_id, ReviewDecisionStatus.BLOCKED),
            (parsed.item_id, ReviewDecisionStatus.ACCEPTED),
        ]
    finally:
        await engine.dispose()


async def test_child_collection_pages_reach_all_rows_beyond_501(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        parsed, failed = await _seed_revision(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await repository.create_review(
            KnowledgeReviewRevision(
                review_id="krv_mysql_pages",
                project_id=VALIDATION_SCOPE.project_id,
                source_revision_ids=("rev_mysql_001",),
                inventory_digest=parse_inventory_digest((parsed, failed)),
                chunk_manifest_digest=DIGEST_B,
                annotation_digest=DIGEST_C,
                dependency_digest=AUTHORITY_DEPENDENCY_DIGEST,
                editor_actor_ids=("synthetic-editor-01",),
                reviewer_actor_id=None,
                expires_at=NOW + timedelta(days=30),
                status=ReviewStatus.CHECKING,
                version=1,
                blocking_item_ids=(),
                approved_item_ids=(parsed.item_id,),
            )
        )
        decisions = [
            KnowledgeReviewItemDecision(
                review_id="krv_mysql_pages",
                item_id=parsed.item_id,
                check_kind=ReviewCheckKind.AMOUNT,
                status=ReviewDecisionStatus.ACCEPTED,
                note=f"审计 {version}",
                actor_id="synthetic-editor-01",
                review_version=version,
                decided_at=NOW + timedelta(seconds=version),
            )
            for version in range(2, 505)
        ]
        async with sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_review_item_decision),
                [
                    {
                        **scope_values(VALIDATION_SCOPE),
                        "decision_id": item.decision_id,
                        "review_id": item.review_id,
                        "item_id": item.item_id,
                        "check_kind": item.check_kind.value,
                        "status": item.status.value,
                        "note": item.note,
                        "decided_by": item.actor_id,
                        "review_version": item.review_version,
                        "decided_at": item.decided_at.replace(tzinfo=None),
                    }
                    for item in decisions
                ],
            )
            await session.execute(
                insert(knowledge_review_history),
                [
                    {
                        **scope_values(VALIDATION_SCOPE),
                        "history_id": f"krh_page_{item.review_version:04d}",
                        "review_id": item.review_id,
                        "review_version": item.review_version,
                        "action": "item_decided",
                        "history_actor_id": item.actor_id,
                        "item_id": item.item_id,
                        "decision_id": item.decision_id,
                        "decision_digest": item.decision_digest,
                        "occurred_at": item.decided_at.replace(tzinfo=None),
                    }
                    for item in decisions
                ],
            )
            await session.execute(
                insert(knowledge_publication),
                [
                    {
                        **scope_values(VALIDATION_SCOPE),
                        "publication_id": f"kpb_page_{index:04d}",
                        "review_id": "krv_mysql_pages",
                        "review_version": 504,
                        "version": 2,
                        "approval_digest": DIGEST_A,
                        "source_revision_ids": ["rev_mysql_001"],
                        "approved_item_ids": [parsed.item_id],
                        "generation": f"generation-page-{index:04d}",
                        "published_by": "synthetic-publisher-03",
                        "published_at": (NOW + timedelta(seconds=index)).replace(tzinfo=None),
                        "expires_at": (NOW + timedelta(days=30)).replace(tzinfo=None),
                        "status": "withdrawn",
                        "withdrawn_by": "synthetic-publisher-03",
                        "withdrawn_at": (NOW + timedelta(seconds=index + 1)).replace(tzinfo=None),
                    }
                    for index in range(502)
                ],
            )

        inventory_one = await repository.inventory_page(
            "krv_mysql_pages", limit=1, after_item_id=None
        )
        inventory_two = await repository.inventory_page(
            "krv_mysql_pages", limit=1, after_item_id=inventory_one.next_cursor
        )
        decision_one = await repository.decision_history_page(
            "krv_mysql_pages", limit=200, after_version=None
        )
        decision_two = await repository.decision_history_page(
            "krv_mysql_pages", limit=200, after_version=decision_one.next_cursor
        )
        decision_three = await repository.decision_history_page(
            "krv_mysql_pages", limit=200, after_version=decision_two.next_cursor
        )
        history_one = await repository.history_page(
            "krv_mysql_pages", limit=200, after_version=None
        )
        history_two = await repository.history_page(
            "krv_mysql_pages", limit=200, after_version=history_one.next_cursor
        )
        history_three = await repository.history_page(
            "krv_mysql_pages", limit=200, after_version=history_two.next_cursor
        )
        publication_one = await repository.publication_page(
            "krv_mysql_pages", limit=200, after_publication_id=None
        )
        publication_two = await repository.publication_page(
            "krv_mysql_pages",
            limit=200,
            after_publication_id=publication_one.next_cursor,
        )
        publication_three = await repository.publication_page(
            "krv_mysql_pages",
            limit=200,
            after_publication_id=publication_two.next_cursor,
        )

        assert inventory_one.total_count == inventory_two.total_count == 2
        assert len(inventory_one.items) == len(inventory_two.items) == 1
        assert inventory_two.next_cursor is None
        assert [len(page.items) for page in (decision_one, decision_two, decision_three)] == [
            200,
            200,
            103,
        ]
        assert decision_three.total_count == 503 and decision_three.next_cursor is None
        assert [len(page.items) for page in (history_one, history_two, history_three)] == [
            200,
            200,
            104,
        ]
        assert history_three.total_count == 504 and history_three.next_cursor is None
        assert [
            len(page.items) for page in (publication_one, publication_two, publication_three)
        ] == [200, 200, 102]
        assert publication_three.total_count == 502
        assert publication_three.next_cursor is None
    finally:
        await engine.dispose()


async def test_source_revision_filter_is_applied_before_stable_review_page_limit(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        rows = []
        for index in range(100):
            rows.append(
                {
                    **scope_values(VALIDATION_SCOPE),
                    "review_id": f"krv_{index:03d}",
                    "source_revision_ids": ["rev_other"],
                    "inventory_digest": DIGEST_A,
                    "chunk_manifest_digest": DIGEST_B,
                    "annotation_digest": DIGEST_C,
                    "dependency_digest": DIGEST_A,
                    "editor_actor_ids": ["synthetic-editor-01"],
                    "reviewer_actor_id": None,
                    "expires_at": (NOW + timedelta(days=30)).replace(tzinfo=None),
                    "status": ReviewStatus.CHECKING.value,
                    "version": 1,
                    "blocking_item_ids": [],
                    "approved_item_ids": [],
                    "created_at": NOW.replace(tzinfo=None),
                    "updated_at": NOW.replace(tzinfo=None),
                }
            )
        rows.append(
            rows[0]
            | {
                "review_id": "krv_zzz_target",
                "source_revision_ids": ["rev_target"],
            }
        )
        async with sessions() as session, session.begin():
            await session.execute(insert(knowledge_review_revision), rows)

        page = await MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE).list_reviews(
            "rev_target", limit=10
        )

        assert [item.review_id for item in page] == ["krv_zzz_target"]
    finally:
        await engine.dispose()
