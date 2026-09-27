from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import delete, func, insert, select, update

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.knowledge_review_service import KnowledgeReviewHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE, ValidationScopeProvider
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_parse_inventory,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_projection import knowledge_projection_state
from tap.modules.knowledge.adapters.mysql_review import (
    MysqlKnowledgeReviewRepository,
    knowledge_current_publication,
    knowledge_publication,
    knowledge_review_command,
    knowledge_review_history,
    knowledge_review_revision,
)
from tap.modules.knowledge.application.review import (
    KnowledgeReviewApplication,
    ReviewCommandConflict,
    ReviewStateConflict,
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
    canonical_digest,
    review_dependency_digest,
    review_id_for,
)
from tap.platform.db.project_scope import scope_values
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging.mysql_outbox import scoped_outbox_id

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 25, 9, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _authorized_review_id(source_revision_id: str) -> str:
    return review_id_for(VALIDATION_SCOPE.project_id, (source_revision_id,))


def _legacy_review(review_id: str, source_revision_ids: tuple[str, ...]) -> KnowledgeReviewRevision:
    return KnowledgeReviewRevision(
        review_id=review_id,
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=source_revision_ids,
        inventory_digest=DIGEST_A,
        chunk_manifest_digest=DIGEST_B,
        annotation_digest=canonical_digest([]),
        dependency_digest=DIGEST_C,
        editor_actor_ids=(VALIDATION_SCOPE.actor_id,),
        reviewer_actor_id=None,
        expires_at=NOW.replace(year=2027),
        status=ReviewStatus.CHECKING,
        version=1,
        blocking_item_ids=(),
        approved_item_ids=("pi_legacy",),
    )


class UnusedProjection:
    async def verify(self, revision, generation):  # type: ignore[no-untyped-def]
        raise AssertionError("review creation must not accept a client projection generation")


class AllowPolicy:
    async def authorize(self, scope, action, resource):  # type: ignore[no-untyped-def]
        return AuthorizationDecision(True, "test-allowed")


class PausingAuthorityRepository(MysqlKnowledgeReviewRepository):
    def __init__(self, *args, authority_locked: asyncio.Event, release: asyncio.Event, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._authority_locked = authority_locked
        self._release = release

    async def _review_authority(self, session, revision, *, lock):  # type: ignore[no-untyped-def]
        if lock:
            self._authority_locked.set()
            await self._release.wait()
        return await super()._review_authority(session, revision, lock=lock)


async def _seed_ready_document(sessions):  # type: ignore[no-untyped-def]
    old_failed = ParseInventoryItem.create(
        source_revision_id="rev_create_001",
        kind=ParseInventoryKind.DOCUMENT,
        locator="document:parser",
        status=ParseInventoryStatus.FAILED,
        reason="invalid-document",
        artifact_digest=DIGEST_A,
    )
    latest = ParseInventoryItem.create(
        source_revision_id="rev_create_001",
        kind=ParseInventoryKind.PARAGRAPH,
        locator="paragraph:1",
        status=ParseInventoryStatus.PARSED,
        artifact_digest=DIGEST_B,
    )
    async with sessions() as session, session.begin():
        await session.execute(
            insert(knowledge_source).values(
                **scope_values(VALIDATION_SCOPE),
                source_id="src_" + "7" * 32,
                name="退款规则",
                created_at=NOW.replace(tzinfo=None),
                updated_at=NOW.replace(tzinfo=None),
                deleted_at=None,
            )
        )
        await session.execute(
            insert(knowledge_document).values(
                **scope_values(VALIDATION_SCOPE),
                document_id="doc_create_001",
                source_id="src_" + "7" * 32,
                filename="refund.md",
                media_type="text/markdown",
                current_revision_id=None,
                source_content_hash=DIGEST_A,
                dedupe_key=DIGEST_C,
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
                revision_id="rev_create_001",
                source_id="src_" + "7" * 32,
                document_id="doc_create_001",
                source_content_hash=DIGEST_A,
                original_blob_locator="tapper-originals/refund.md",
                normalized_blob_locator="tapper-artifacts/refund.normalized",
                chunks_blob_locator="tapper-artifacts/refund.chunks",
                embeddings_blob_locator="tapper-artifacts/refund.embeddings",
                parser_version="tapper-parser-v1",
                chunker_version="tapper-chunker-v1",
                pipeline_version="tapper-ingestion-v1",
                parse_inventory_attempt=2,
                parser_config_digest=DIGEST_C,
                parse_inventory_digest=parse_inventory_digest((latest,)),
                chunk_manifest_digest=DIGEST_B,
                projection_digest=DIGEST_C,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        await session.execute(
            knowledge_document.update()
            .where(knowledge_document.c.document_id == "doc_create_001")
            .values(current_revision_id="rev_create_001")
        )
        for attempt, item in ((1, old_failed), (2, latest)):
            await session.execute(
                insert(knowledge_parse_inventory).values(
                    **scope_values(VALIDATION_SCOPE),
                    inventory_row_id=f"inventory-create-{attempt}",
                    source_revision_id=item.source_revision_id,
                    attempt=attempt,
                    item_id=item.item_id,
                    ordinal=0,
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
            insert(knowledge_projection_state).values(
                **scope_values(VALIDATION_SCOPE),
                alias_name="knowledge",
                generation=1,
                physical_collection="generation-create-001",
                updated_at=NOW.replace(tzinfo=None),
            )
        )
    return latest


async def _activate_replacement_revision(sessions):  # type: ignore[no-untyped-def]
    replacement = ParseInventoryItem.create(
        source_revision_id="rev_create_002",
        kind=ParseInventoryKind.PARAGRAPH,
        locator="paragraph:1",
        status=ParseInventoryStatus.PARSED,
        artifact_digest=DIGEST_C,
    )
    async with sessions() as session, session.begin():
        await session.execute(
            insert(knowledge_document_revision).values(
                **scope_values(VALIDATION_SCOPE),
                revision_id="rev_create_002",
                source_id="src_" + "7" * 32,
                document_id="doc_create_001",
                source_content_hash=DIGEST_B,
                original_blob_locator="tapper-originals/refund-v2.md",
                normalized_blob_locator="tapper-artifacts/refund-v2.normalized",
                chunks_blob_locator="tapper-artifacts/refund-v2.chunks",
                embeddings_blob_locator="tapper-artifacts/refund-v2.embeddings",
                parser_version="tapper-parser-v1",
                chunker_version="tapper-chunker-v1",
                pipeline_version="tapper-ingestion-v1",
                parse_inventory_attempt=1,
                parser_config_digest=DIGEST_C,
                parse_inventory_digest=parse_inventory_digest((replacement,)),
                chunk_manifest_digest=DIGEST_B,
                projection_digest=DIGEST_C,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        await session.execute(
            insert(knowledge_parse_inventory).values(
                **scope_values(VALIDATION_SCOPE),
                inventory_row_id="inventory-create-replacement",
                source_revision_id=replacement.source_revision_id,
                attempt=1,
                item_id=replacement.item_id,
                ordinal=0,
                item_kind=replacement.kind.value,
                locator=replacement.locator,
                status=replacement.status.value,
                reason=replacement.reason,
                artifact_digest=replacement.artifact_digest,
                decision_actor_id=replacement.decision_actor_id,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        await session.execute(
            update(knowledge_document)
            .where(knowledge_document.c.document_id == "doc_create_001")
            .values(current_revision_id="rev_create_002")
        )
    return replacement


async def test_ready_latest_attempt_concurrent_create_or_open_is_one_durable_review(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        latest = await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, UnusedProjection())
        service = KnowledgeReviewHttpService(
            application,
            scope=VALIDATION_SCOPE,
            authorization_policy=AllowPolicy(),
            clock=lambda: NOW,
        )

        app = create_app(
            services=HttpServices(
                scope=VALIDATION_SCOPE,
                scope_provider=ValidationScopeProvider(),
                authorization_policy=AllowPolicy(),
                knowledge_reviews=service,
            ),
            allowed_origins=frozenset({"http://127.0.0.1:15175"}),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:15175"
        ) as http:
            first, second = await asyncio.gather(
                http.post(
                    "/api/v1/projects/tapper-demo/knowledge/documents/doc_create_001/review",
                    headers={
                        "Idempotency-Key": "open-create-001",
                        "Origin": "http://127.0.0.1:15175",
                    },
                    json={"sourceRevisionId": "rev_create_001"},
                ),
                http.post(
                    "/api/v1/projects/tapper-demo/knowledge/documents/doc_create_001/review",
                    headers={
                        "Idempotency-Key": "open-create-002",
                        "Origin": "http://127.0.0.1:15175",
                    },
                    json={"sourceRevisionId": "rev_create_001"},
                ),
            )
            replay = await http.post(
                "/api/v1/projects/tapper-demo/knowledge/documents/doc_create_001/review",
                headers={
                    "Idempotency-Key": "open-create-001",
                    "Origin": "http://127.0.0.1:15175",
                },
                json={"sourceRevisionId": "rev_create_001"},
            )

        assert first.status_code == second.status_code == 200
        assert replay.status_code == 200
        assert first.json() == second.json() == replay.json()
        payload = first.json()
        assert payload["status"] == ReviewStatus.CHECKING.value
        assert payload["version"] == 1
        assert payload["editorActorIds"] == [VALIDATION_SCOPE.actor_id]
        assert payload["approvedItemIds"] == [latest.item_id]
        assert payload["blockingItemIds"] == []
        created = await repository.get_review(payload["reviewId"])
        assert created is not None
        assert created.inventory_digest == parse_inventory_digest((latest,))
        assert created.annotation_digest == canonical_digest([])
        assert created.dependency_digest == review_dependency_digest(
            (("rev_create_001", DIGEST_A, DIGEST_C, DIGEST_C),)
        )

        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_revision))
                == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_history))
                == 1
            )

        restarted = KnowledgeReviewApplication(
            MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE),
            UnusedProjection(),
        )
        discovered = await restarted.list_reviews("rev_create_001")
        assert [item.revision for item in discovered] == [created]
    finally:
        await engine.dispose()


async def test_create_or_open_fails_closed_for_unready_missing_failed_and_unprojected_input(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        latest = await _seed_ready_document(sessions)
        application = KnowledgeReviewApplication(
            MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE),
            UnusedProjection(),
        )

        with pytest.raises(ReviewStateConflict, match="review-source-not-current"):
            await application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_stale_001",
                authorized_review_id=_authorized_review_id("rev_stale_001"),
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-stale-revision",
                now=NOW,
            )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == "doc_create_001")
                .values(status="failed")
            )
        with pytest.raises(ReviewStateConflict, match="review-source-not-ready"):
            await application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_create_001",
                authorized_review_id=_authorized_review_id("rev_create_001"),
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-unready",
                now=NOW,
            )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == "doc_create_001")
                .values(status="ready")
            )
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_create_001")
                .values(parse_inventory_attempt=3)
            )
        with pytest.raises(ReviewStateConflict, match="review-inventory-unavailable"):
            await application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_create_001",
                authorized_review_id=_authorized_review_id("rev_create_001"),
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-no-inventory",
                now=NOW,
            )

        failed = ParseInventoryItem.create(
            source_revision_id="rev_create_001",
            kind=ParseInventoryKind.IMAGE,
            locator="page:1/image:1",
            status=ParseInventoryStatus.FAILED,
            reason="ocr-required",
            artifact_digest=DIGEST_A,
        )
        async with sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_parse_inventory),
                [
                    {
                        **scope_values(VALIDATION_SCOPE),
                        "inventory_row_id": f"inventory-create-3-{ordinal}",
                        "source_revision_id": item.source_revision_id,
                        "attempt": 3,
                        "item_id": item.item_id,
                        "ordinal": ordinal,
                        "item_kind": item.kind.value,
                        "locator": item.locator,
                        "status": item.status.value,
                        "reason": item.reason,
                        "artifact_digest": item.artifact_digest,
                        "decision_actor_id": item.decision_actor_id,
                        "created_at": NOW.replace(tzinfo=None),
                    }
                    for ordinal, item in enumerate((latest, failed))
                ],
            )
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_create_001")
                .values(parse_inventory_digest=parse_inventory_digest((latest, failed)))
            )
        with pytest.raises(ReviewStateConflict, match="review-inventory-incomplete"):
            await application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_create_001",
                authorized_review_id=_authorized_review_id("rev_create_001"),
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-failed-inventory",
                now=NOW,
            )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == "rev_create_001")
                .values(
                    parse_inventory_attempt=2,
                    parse_inventory_digest=parse_inventory_digest((latest,)),
                )
            )
            await session.execute(delete(knowledge_projection_state))
        with pytest.raises(ReviewStateConflict, match="review-projection-not-ready"):
            await application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_create_001",
                authorized_review_id=_authorized_review_id("rev_create_001"),
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-unprojected",
                now=NOW,
            )

        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_revision))
                == 0
            )
    finally:
        await engine.dispose()


async def test_existing_approved_and_current_publication_are_opened_without_parallel_review(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, UnusedProjection())
        created = await application.open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=_authorized_review_id("rev_create_001"),
            actor_id=VALIDATION_SCOPE.actor_id,
            idempotency_key="open-existing-created",
            now=NOW,
        )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_review_revision)
                .where(knowledge_review_revision.c.review_id == created.review_id)
                .values(status="approved", version=2, reviewer_actor_id="independent-reviewer")
            )
        approved = await application.open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=_authorized_review_id("rev_create_001"),
            actor_id=VALIDATION_SCOPE.actor_id,
            idempotency_key="open-existing-approved",
            now=NOW,
        )
        assert approved.review_id == created.review_id
        assert approved.status is ReviewStatus.APPROVED

        publication_id = "kpb_create_001"
        async with sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_publication).values(
                    **scope_values(VALIDATION_SCOPE),
                    publication_id=publication_id,
                    review_id=created.review_id,
                    review_version=2,
                    version=1,
                    approval_digest=approved.approval_digest,
                    source_revision_ids=["rev_create_001"],
                    approved_item_ids=list(approved.approved_item_ids),
                    generation="generation-create-001",
                    published_by="independent-reviewer",
                    published_at=NOW.replace(tzinfo=None),
                    expires_at=approved.expires_at.replace(tzinfo=None),
                    status="published",
                    withdrawn_by=None,
                    withdrawn_at=None,
                )
            )
            await session.execute(
                insert(knowledge_current_publication).values(
                    **scope_values(VALIDATION_SCOPE),
                    pointer_id=scoped_outbox_id(
                        VALIDATION_SCOPE,
                        kind="knowledge-current-publication",
                        identity="current",
                    ),
                    publication_id=publication_id,
                    updated_at=NOW.replace(tzinfo=None),
                )
            )
            await session.execute(
                update(knowledge_review_revision)
                .where(knowledge_review_revision.c.review_id == created.review_id)
                .values(status="published", version=3)
            )
        published = await application.open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=_authorized_review_id("rev_create_001"),
            actor_id=VALIDATION_SCOPE.actor_id,
            idempotency_key="open-existing-published",
            now=NOW,
        )
        assert published.review_id == created.review_id
        assert published.status is ReviewStatus.PUBLISHED
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_revision))
                == 1
            )

        with pytest.raises(ReviewCommandConflict, match="idempotency-conflict"):
            await application.open_review(
                document_id="doc_other",
                source_revision_id="rev_other",
                authorized_review_id=_authorized_review_id("rev_other"),
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-existing-published",
                now=NOW,
            )
    finally:
        await engine.dispose()


async def test_legacy_single_source_actual_id_resolves_and_reopens_atomically(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, UnusedProjection())
        legacy = _legacy_review("krv_legacy_actual", ("rev_create_001",))
        await repository.create_review(legacy)

        actual_review_id = await application.resolve_open_review(
            document_id="doc_create_001", source_revision_id="rev_create_001"
        )
        reopened = await application.open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=actual_review_id,
            actor_id=VALIDATION_SCOPE.actor_id,
            idempotency_key="open-legacy-actual",
            now=NOW,
        )

        assert actual_review_id == reopened.review_id == legacy.review_id
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_revision))
                == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_command))
                == 1
            )
    finally:
        await engine.dispose()


async def test_open_revalidates_actual_id_before_command_write_when_target_changes(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, UnusedProjection())
        initially_resolved = await application.resolve_open_review(
            document_id="doc_create_001", source_revision_id="rev_create_001"
        )
        await repository.create_review(_legacy_review("krv_raced_legacy", ("rev_create_001",)))

        with pytest.raises(ReviewStateConflict, match="review-open-target-changed"):
            await application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_create_001",
                authorized_review_id=initially_resolved,
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-raced-target",
                now=NOW,
            )

        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_command))
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_history))
                == 1
            )
    finally:
        await engine.dispose()


async def test_document_open_rejects_multi_source_review_before_command_write(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, UnusedProjection())
        multi_source = _legacy_review("krv_legacy_multi", ("rev_create_001", "rev_unrelated_002"))
        await repository.create_review(multi_source)

        with pytest.raises(ReviewStateConflict, match="review-source-set-conflict"):
            await application.resolve_open_review(
                document_id="doc_create_001", source_revision_id="rev_create_001"
            )
        with pytest.raises(ReviewStateConflict, match="review-source-set-conflict"):
            await application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_create_001",
                authorized_review_id=multi_source.review_id,
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key="open-multi-source",
                now=NOW,
            )

        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_review_command))
                == 0
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("mutation", ["submit", "approve"])
async def test_reopen_serializes_with_review_mutation_without_deadlock(
    owned_project_mysql, mutation
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed_ready_document(sessions)
        seed_application = KnowledgeReviewApplication(
            MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE),
            UnusedProjection(),
        )
        created = await seed_application.open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=_authorized_review_id("rev_create_001"),
            actor_id=VALIDATION_SCOPE.actor_id,
            idempotency_key=f"open-before-{mutation}",
            now=NOW,
        )
        if mutation == "approve":
            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_review_revision)
                    .where(knowledge_review_revision.c.review_id == created.review_id)
                    .values(status="reviewing", version=2)
                )

        authority_locked = asyncio.Event()
        release = asyncio.Event()
        mutation_application = KnowledgeReviewApplication(
            PausingAuthorityRepository(
                sessions,
                scope=VALIDATION_SCOPE,
                authority_locked=authority_locked,
                release=release,
            ),
            UnusedProjection(),
        )
        if mutation == "submit":
            mutation_task = asyncio.create_task(
                mutation_application.transition_review(
                    created.review_id,
                    target=ReviewStatus.REVIEWING,
                    actor_id=VALIDATION_SCOPE.actor_id,
                    expected_version=1,
                    now=NOW,
                )
            )
        else:
            mutation_task = asyncio.create_task(
                mutation_application.approve_review(
                    created.review_id,
                    actor_id="independent-reviewer",
                    expected_version=2,
                    now=NOW,
                )
            )
        await asyncio.wait_for(authority_locked.wait(), timeout=5)

        reopen_task = asyncio.create_task(
            seed_application.open_review(
                document_id="doc_create_001",
                source_revision_id="rev_create_001",
                authorized_review_id=_authorized_review_id("rev_create_001"),
                actor_id=VALIDATION_SCOPE.actor_id,
                idempotency_key=f"concurrent-reopen-{mutation}",
                now=NOW,
            )
        )
        await asyncio.sleep(0.1)
        release.set()
        mutated, reopened = await asyncio.wait_for(
            asyncio.gather(mutation_task, reopen_task), timeout=10
        )

        assert mutated.review_id == reopened.review_id == created.review_id
        assert mutated.status is (
            ReviewStatus.REVIEWING if mutation == "submit" else ReviewStatus.APPROVED
        )
    finally:
        await engine.dispose()


async def test_review_authority_requires_current_ready_undeleted_document_and_source(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, UnusedProjection())
        created = await application.open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=_authorized_review_id("rev_create_001"),
            actor_id=VALIDATION_SCOPE.actor_id,
            idempotency_key="open-authority-guards",
            now=NOW,
        )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == "doc_create_001")
                .values(status="failed")
            )
        assert not await repository.review_authority(created)
        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == "doc_create_001")
                .values(status="ready", deleted_at=NOW.replace(tzinfo=None))
            )
        assert not await repository.review_authority(created)
        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == "doc_create_001")
                .values(deleted_at=None)
            )
            await session.execute(
                update(knowledge_source)
                .where(knowledge_source.c.source_id == "src_" + "7" * 32)
                .values(deleted_at=NOW.replace(tzinfo=None))
            )
        assert not await repository.review_authority(created)
    finally:
        await engine.dispose()


async def test_replaced_revision_cannot_be_submitted_approved_or_published_over_current_pointer(
    owned_project_mysql,
):
    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        application = KnowledgeReviewApplication(repository, UnusedProjection())
        created = await application.open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=_authorized_review_id("rev_create_001"),
            actor_id=VALIDATION_SCOPE.actor_id,
            idempotency_key="open-before-replacement",
            now=NOW,
        )
        await _activate_replacement_revision(sessions)

        with pytest.raises(ReviewStateConflict, match="review-authority-changed"):
            await application.transition_review(
                created.review_id,
                target=ReviewStatus.REVIEWING,
                actor_id=VALIDATION_SCOPE.actor_id,
                expected_version=1,
                now=NOW,
            )

        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_review_revision)
                .where(knowledge_review_revision.c.review_id == created.review_id)
                .values(status="reviewing", version=2)
            )
        with pytest.raises(ReviewStateConflict, match="review-authority-changed"):
            await application.approve_review(
                created.review_id,
                actor_id="independent-reviewer",
                expected_version=2,
                now=NOW,
            )

        guard_publication_id = "kpb_current_guard"
        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_review_revision)
                .where(knowledge_review_revision.c.review_id == created.review_id)
                .values(status="approved", version=3, reviewer_actor_id="independent-reviewer")
            )
            await session.execute(
                insert(knowledge_publication).values(
                    **scope_values(VALIDATION_SCOPE),
                    publication_id=guard_publication_id,
                    review_id=created.review_id,
                    review_version=2,
                    version=1,
                    approval_digest=created.approval_digest,
                    source_revision_ids=list(created.source_revision_ids),
                    approved_item_ids=list(created.approved_item_ids),
                    generation="generation-create-001",
                    published_by="independent-reviewer",
                    published_at=NOW.replace(tzinfo=None),
                    expires_at=created.expires_at.replace(tzinfo=None),
                    status="published",
                    withdrawn_by=None,
                    withdrawn_at=None,
                )
            )
            await session.execute(
                insert(knowledge_current_publication).values(
                    **scope_values(VALIDATION_SCOPE),
                    pointer_id=scoped_outbox_id(
                        VALIDATION_SCOPE,
                        kind="knowledge-current-publication",
                        identity="current",
                    ),
                    publication_id=guard_publication_id,
                    updated_at=NOW.replace(tzinfo=None),
                )
            )

        with pytest.raises(ReviewStateConflict, match="review-authority-changed"):
            await application.publish_review(
                created.review_id,
                generation="generation-create-001",
                idempotency_key="publish-stale-review",
                actor_id=VALIDATION_SCOPE.actor_id,
                expected_version=3,
                now=NOW,
            )
        async with sessions() as session:
            assert (
                await session.scalar(select(knowledge_current_publication.c.publication_id))
                == guard_publication_id
            )
            assert (
                await session.scalar(select(func.count()).select_from(knowledge_publication)) == 1
            )
    finally:
        await engine.dispose()
