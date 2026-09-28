import json
from dataclasses import replace

import pytest
from sqlalchemy import select, update

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_ingestion_job,
    knowledge_parse_inventory,
)
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.domain.documents import DocumentId, RevisionId, revision_id_for
from tap.modules.knowledge.domain.parse_inventory import ParseInventoryItem, parse_inventory_digest
from tap.modules.knowledge.domain.review import ReviewStatus, canonical_digest
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.platform.db.session import create_engine_and_session_factory
from tests.integration.test_knowledge_review_creation import NOW, _seed_ready_document
from tests.unit.knowledge.test_flowchart_correction import fixture


@pytest.mark.asyncio
async def test_correction_atomically_creates_indexing_revision_and_preserves_previous_artifact(
    owned_project_mysql,
):
    engine, sessions = create_engine_and_session_factory(
        owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    )
    try:
        await _seed_ready_document(sessions)
        repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        target = await repository.resolve_open_review_target(
            document_id="doc_create_001", source_revision_id="rev_create_001"
        )
        review = await repository.create_or_open_review(
            document_id="doc_create_001",
            source_revision_id="rev_create_001",
            authorized_review_id=target,
            actor_id=VALIDATION_SCOPE.actor_id,
            expires_at=NOW.replace(year=2027),
            command_key="correct-open",
            command_digest=canonical_digest("open"),
            now=NOW,
        )
        _, _, artifacts, _ = fixture()
        prior = artifacts.normalized
        correction_digest = canonical_digest(
            {"sourceRevisionId": "rev_create_001", "graph": json.loads(prior.flowchart_data)}
        )
        parser_version = "human-correction-" + correction_digest[7:]
        new_id = str(
            revision_id_for(DocumentId("doc_create_001"), "sha256:" + "a" * 64, parser_version)
        )
        items = tuple(
            ParseInventoryItem.create(
                source_revision_id=new_id,
                kind=item.kind,
                locator=item.locator,
                status=item.status,
                artifact_digest=item.artifact_digest,
                reason=item.reason,
                original_alignment_reason=item.original_alignment_reason,
            )
            for item in prior.parse_inventory
        )
        item_ids = {
            old.item_id: new.item_id for old, new in zip(prior.parse_inventory, items, strict=True)
        }
        artifact = replace(
            prior,
            document_id=DocumentId("doc_create_001"),
            revision_id=RevisionId(new_id),
            source_hash="sha256:" + "a" * 64,
            blocks=tuple(
                replace(block, inventory_item_id=item_ids[block.inventory_item_id])
                for block in prior.blocks
            ),
            parser_version=parser_version,
            correction_source_revision_id="rev_create_001",
            parse_inventory=items,
            parse_inventory_digest=parse_inventory_digest(items),
        )
        invalidated = replace(
            review,
            version=review.version + 1,
            status=ReviewStatus.NEEDS_REVIEW,
            editor_actor_ids=(VALIDATION_SCOPE.actor_id, "earlier-corrector"),
            approved_item_ids=(),
            reviewer_actor_id=None,
            annotation_digest=correction_digest,
        )
        await repository.save_flowchart_correction(
            invalidated,
            artifact,
            ArtifactLocator("corrected.normalized"),
            expected_version=review.version,
            actor_id=VALIDATION_SCOPE.actor_id,
            occurred_at=NOW,
        )
        async with sessions() as session:
            rows = (await session.execute(select(knowledge_document_revision))).mappings().all()
            assert len(rows) == 2
            old = next(row for row in rows if row["revision_id"] == "rev_create_001")
            new = next(row for row in rows if row["revision_id"] == new_id)
            assert old["normalized_blob_locator"] == "tapper-artifacts/refund.normalized"
            assert new["original_blob_locator"] == old["original_blob_locator"]
            assert new["normalized_blob_locator"] == "corrected.normalized"
            assert new["chunks_blob_locator"] is None
            assert await session.scalar(select(knowledge_document.c.current_revision_id)) == new_id
            job = (await session.execute(select(knowledge_ingestion_job))).mappings().one()
            assert job["stage"] == "chunking" and job["status"] == "pending"
            assert any(
                stage["stage"] == "parsing" and stage["state"] == "completed"
                for stage in job["stage_results_json"]
            )
            assert len(
                (
                    await session.execute(
                        select(knowledge_parse_inventory).where(
                            knowledge_parse_inventory.c.source_revision_id == new_id
                        )
                    )
                ).all()
            ) == len(items)
        saved = await repository.get_review(review.review_id)
        assert saved.approved_item_ids == () and saved.status is ReviewStatus.NEEDS_REVIEW
        history = await repository.list_history(review.review_id)
        assert history[-1].action == "flowchart_corrected"
        assert history[-1].item_id == new_id
        assert history[-1].actor_id == VALIDATION_SCOPE.actor_id
        async with sessions() as session, session.begin():
            await session.execute(update(knowledge_document).values(status="ready", stage="ready"))
            await session.execute(
                update(knowledge_document_revision)
                .where(knowledge_document_revision.c.revision_id == new_id)
                .values(
                    chunk_manifest_digest=canonical_digest("chunks"),
                    projection_digest=canonical_digest("projection"),
                )
            )
        next_target = await repository.resolve_open_review_target(
            document_id="doc_create_001", source_revision_id=new_id
        )
        next_review = await repository.create_or_open_review(
            document_id="doc_create_001",
            source_revision_id=new_id,
            authorized_review_id=next_target,
            actor_id=VALIDATION_SCOPE.actor_id,
            expires_at=NOW.replace(year=2027),
            command_key="correct-open-next",
            command_digest=canonical_digest("open-next"),
            now=NOW,
        )
        assert "earlier-corrector" in next_review.editor_actor_ids
        assert next_review.approved_item_ids == ()
        assert len(next_review.blocking_item_ids) == len(items)
        with pytest.raises(Exception, match="revision-conflict"):
            await repository.save_flowchart_correction(
                invalidated,
                artifact,
                ArtifactLocator("corrected.normalized"),
                expected_version=review.version,
                actor_id=VALIDATION_SCOPE.actor_id,
                occurred_at=NOW,
            )
    finally:
        await engine.dispose()
