"""Prepare a governed current publication in the owned E2E project only."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from tap.entrypoints.tapper_runtime import TapperSettings
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_parse_inventory,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_projection import knowledge_projection_state
from tap.modules.knowledge.adapters.mysql_review import (
    MysqlApprovedProjectionVerifier,
    MysqlKnowledgeReviewRepository,
)
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
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
)
from tap.platform.db.project_scope import scope_predicates
from tap.platform.db.session import create_engine_and_session_factory

EDITOR = "tapper-e2e-fixture-editor"
REVIEWER = "tapper-e2e-fixture-reviewer"
PUBLISHER = "tapper-e2e-fixture-publisher"


async def prepare(revision_ids: tuple[str, ...]) -> dict[str, object]:
    if (
        os.environ.get("TAP_DEMO_MODE") != "e2e"
        or os.environ.get("TAP_TAPPER_COMPOSE_PROJECT") != "tap-tapper-e2e"
    ):
        raise ValueError("publication fixture requires the owned E2E project")
    settings = TapperSettings.from_mapping(dict(os.environ))
    if settings.project_id != VALIDATION_SCOPE.project_id or not revision_ids:
        raise ValueError("publication fixture requires selected validation revisions")
    engine, sessions = create_engine_and_session_factory(settings.database_url)
    repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
    projection = MysqlApprovedProjectionVerifier(sessions, scope=VALIDATION_SCOPE)
    application = KnowledgeReviewApplication(repository, projection)
    try:
        current = await repository.current_publication()
        selected = set(revision_ids)
        if current is not None:
            selected.update(current.source_revision_ids)
        async with sessions() as session:
            active = (
                (
                    await session.execute(
                        select(
                            knowledge_document_revision.c.revision_id,
                            knowledge_document_revision.c.parse_inventory_attempt,
                            knowledge_document_revision.c.parse_inventory_digest,
                            knowledge_document_revision.c.chunk_manifest_digest,
                            knowledge_document_revision.c.projection_digest,
                        )
                        .join(
                            knowledge_document,
                            knowledge_document.c.document_id
                            == knowledge_document_revision.c.document_id,
                        )
                        .join(
                            knowledge_source,
                            knowledge_source.c.source_id
                            == knowledge_document.c.source_id,
                        )
                        .where(
                            *scope_predicates(
                                knowledge_document_revision, VALIDATION_SCOPE
                            ),
                            *scope_predicates(knowledge_document, VALIDATION_SCOPE),
                            *scope_predicates(knowledge_source, VALIDATION_SCOPE),
                            knowledge_document_revision.c.revision_id.in_(selected),
                            knowledge_document.c.current_revision_id
                            == knowledge_document_revision.c.revision_id,
                            knowledge_document.c.status == "ready",
                            knowledge_document.c.deleted_at.is_(None),
                            knowledge_source.c.deleted_at.is_(None),
                        )
                    )
                )
                .mappings()
                .all()
            )
            by_id = {row["revision_id"]: row for row in active}
            if not set(revision_ids) <= by_id.keys():
                raise ValueError("selected revision is not active and ready")
            generation = await session.scalar(
                select(knowledge_projection_state.c.physical_collection).where(
                    *scope_predicates(knowledge_projection_state, VALIDATION_SCOPE),
                    knowledge_projection_state.c.alias_name
                    == f"tap-tapper-e2e:{settings.alias}",
                )
            )
            if not generation:
                raise ValueError("owned projection is not active")
            inventory_facts: list[dict[str, str]] = []
            manifest_facts: list[dict[str, str]] = []
            projection_facts: list[dict[str, str]] = []
            approved_items: list[str] = []
            for revision_id in sorted(by_id):
                row = by_id[revision_id]
                if not all(
                    row[key]
                    for key in (
                        "parse_inventory_attempt",
                        "parse_inventory_digest",
                        "chunk_manifest_digest",
                        "projection_digest",
                    )
                ):
                    raise ValueError(
                        "ready revision lacks durable inventory or projection"
                    )
                inventory_rows = (
                    (
                        await session.execute(
                            select(knowledge_parse_inventory)
                            .where(
                                *scope_predicates(
                                    knowledge_parse_inventory, VALIDATION_SCOPE
                                ),
                                knowledge_parse_inventory.c.source_revision_id
                                == revision_id,
                                knowledge_parse_inventory.c.attempt
                                == row["parse_inventory_attempt"],
                            )
                            .order_by(knowledge_parse_inventory.c.ordinal)
                        )
                    )
                    .mappings()
                    .all()
                )
                inventory = tuple(
                    ParseInventoryItem(
                        source_revision_id=revision_id,
                        item_id=item["item_id"],
                        kind=ParseInventoryKind(item["item_kind"]),
                        locator=item["locator"],
                        status=ParseInventoryStatus(item["status"]),
                        artifact_digest=item["artifact_digest"],
                        reason=item["reason"],
                        decision_actor_id=item["decision_actor_id"],
                    )
                    for item in inventory_rows
                )
                if (
                    not inventory
                    or parse_inventory_digest(inventory)
                    != row["parse_inventory_digest"]
                    or any(
                        item.status
                        not in {
                            ParseInventoryStatus.PARSED,
                            ParseInventoryStatus.EXCLUDED,
                        }
                        for item in inventory
                    )
                ):
                    raise ValueError("selected inventory is incomplete or unreviewed")
                approved_items.extend(
                    item.item_id
                    for item in inventory
                    if item.status is ParseInventoryStatus.PARSED
                )
                inventory_facts.append(
                    {"revisionId": revision_id, "digest": row["parse_inventory_digest"]}
                )
                manifest_facts.append(
                    {"revisionId": revision_id, "digest": row["chunk_manifest_digest"]}
                )
                projection_facts.append(
                    {"revisionId": revision_id, "digest": row["projection_digest"]}
                )
        if not approved_items:
            raise ValueError("publication requires parsed inventory items")
        now = datetime.now(UTC)
        review = KnowledgeReviewRevision(
            review_id=f"krv_{uuid4().hex}",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=tuple(sorted(by_id)),
            inventory_digest=canonical_digest(inventory_facts),
            chunk_manifest_digest=canonical_digest(manifest_facts),
            annotation_digest=canonical_digest({"annotations": []}),
            dependency_digest=canonical_digest(
                {"generation": generation, "projectionDigests": projection_facts}
            ),
            editor_actor_ids=(EDITOR,),
            reviewer_actor_id=None,
            expires_at=now + timedelta(days=1),
            status=ReviewStatus.DRAFT,
            version=1,
            blocking_item_ids=(),
            approved_item_ids=tuple(sorted(approved_items)),
        )
        await repository.create_review(review)
        for target in (ReviewStatus.CHECKING, ReviewStatus.REVIEWING):
            review = await application.transition_review(
                review.review_id,
                target=target,
                actor_id=EDITOR,
                expected_version=review.version,
            )
        review = await application.approve_review(
            review.review_id,
            actor_id=REVIEWER,
            expected_version=review.version,
            now=now,
        )
        publication = await application.publish_review(
            review.review_id,
            generation=generation,
            idempotency_key=f"e2e-publish-{uuid4().hex}",
            actor_id=PUBLISHER,
            now=now,
        )
        persisted_review = await repository.get_review(review.review_id)
        if (
            persisted_review is None
            or persisted_review.status is not ReviewStatus.PUBLISHED
        ):
            raise RuntimeError("governed review was not persisted")
        if await repository.current_publication() != publication:
            raise RuntimeError("current publication did not advance")
        await PublishedKnowledgeAuthority(repository).authorize_selection(
            VALIDATION_SCOPE.project_id, tuple(revision_ids)
        )
        for revision_id in revision_ids:
            if revision_id not in publication.source_revision_ids:
                raise RuntimeError("selected revision is outside publication")
        return {
            "publicationId": publication.publication_id,
            "sourceRevisionIds": list(publication.source_revision_ids),
            "approvedItemIds": list(publication.approved_item_ids),
            "generation": publication.generation,
            "editorActorIds": list(persisted_review.editor_actor_ids),
            "reviewerActorId": persisted_review.reviewer_actor_id,
            "publishedBy": publication.published_by,
            "status": publication.status,
        }
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("selected revision IDs are required")
    print(json.dumps(asyncio.run(prepare(tuple(sys.argv[1:]))), sort_keys=True))
