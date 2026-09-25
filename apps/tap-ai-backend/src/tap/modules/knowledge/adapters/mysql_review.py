"""MySQL authority for knowledge review, publication, and withdrawal."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import (
    Column,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_parse_inventory,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_projection import knowledge_projection_state
from tap.modules.knowledge.application.review import (
    PublishedSourceRecord,
    ReviewCommandConflict,
    ReviewComparisonTarget,
    ReviewInventoryRecord,
    ReviewNotFound,
    ReviewStateConflict,
)
from tap.modules.knowledge.domain.review import (
    KnowledgePublication,
    KnowledgeReviewHistoryEntry,
    KnowledgeReviewItemDecision,
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import augment_project_table, metadata
from tap.platform.messaging.mysql_outbox import scoped_outbox_id, write_project_event

knowledge_review_revision = Table(
    "knowledge_review_revision",
    metadata,
    Column("review_id", String(64), primary_key=True),
    Column("source_revision_ids", JSON, nullable=False),
    Column("inventory_digest", String(71), nullable=False),
    Column("chunk_manifest_digest", String(71), nullable=False),
    Column("annotation_digest", String(71), nullable=False),
    Column("dependency_digest", String(71), nullable=False),
    Column("editor_actor_ids", JSON, nullable=False),
    Column("reviewer_actor_id", String(128)),
    Column("expires_at", DATETIME(fsp=6), nullable=False),
    Column("status", String(24), nullable=False),
    Column("version", Integer, nullable=False),
    Column("blocking_item_ids", JSON, nullable=False),
    Column("approved_item_ids", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
)

knowledge_publication = Table(
    "knowledge_publication",
    metadata,
    Column("publication_id", String(64), primary_key=True),
    Column("review_id", String(64), nullable=False),
    Column("review_version", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("approval_digest", String(71), nullable=False),
    Column("source_revision_ids", JSON, nullable=False),
    Column("approved_item_ids", JSON, nullable=False),
    Column("generation", String(256), nullable=False),
    Column("published_by", String(128), nullable=False),
    Column("published_at", DATETIME(fsp=6), nullable=False),
    Column("expires_at", DATETIME(fsp=6), nullable=False),
    Column("status", String(16), nullable=False),
    Column("withdrawn_by", String(128)),
    Column("withdrawn_at", DATETIME(fsp=6)),
)

knowledge_review_item_decision = Table(
    "knowledge_review_item_decision",
    metadata,
    Column("decision_id", String(128), primary_key=True),
    Column("review_id", String(64), nullable=False),
    Column("item_id", String(128), nullable=False),
    Column("check_kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("note", Text, nullable=False),
    Column("decided_by", String(128), nullable=False),
    Column("review_version", Integer, nullable=False),
    Column("decided_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("review_id", "item_id", name="uq_review_item_decision_item"),
)

knowledge_review_history = Table(
    "knowledge_review_history",
    metadata,
    Column("history_id", String(128), primary_key=True),
    Column("review_id", String(64), nullable=False),
    Column("review_version", Integer, nullable=False),
    Column("action", String(64), nullable=False),
    Column("history_actor_id", String(128), nullable=False),
    Column("item_id", String(128)),
    Column("occurred_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("review_id", "review_version", name="uq_review_history_version"),
)

knowledge_current_publication = Table(
    "knowledge_current_publication",
    metadata,
    Column("pointer_id", String(128), primary_key=True),
    Column("publication_id", String(64), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
)

knowledge_review_command = Table(
    "knowledge_review_command",
    metadata,
    Column("command_id", String(128), primary_key=True),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("result", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("idempotency_key", name="uq_knowledge_review_command_project_command"),
)

knowledge_publication_cleanup = Table(
    "knowledge_publication_cleanup",
    metadata,
    Column("publication_id", String(64), primary_key=True),
    Column("generation", String(256), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)

for _table in (
    knowledge_review_revision,
    knowledge_review_item_decision,
    knowledge_review_history,
    knowledge_publication,
    knowledge_current_publication,
    knowledge_review_command,
    knowledge_publication_cleanup,
):
    augment_project_table(_table)

KNOWLEDGE_REVIEW_TABLES = (
    knowledge_review_revision,
    knowledge_review_item_decision,
    knowledge_review_history,
    knowledge_publication,
    knowledge_current_publication,
    knowledge_review_command,
    knowledge_publication_cleanup,
)


class MysqlApprovedProjectionVerifier:
    """Verify that every approved source revision belongs to one active projection."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def verify(self, revision: KnowledgeReviewRevision, generation: str) -> bool:
        if revision.project_id != self._scope.project_id:
            return False
        async with self._sessions() as session:
            active = (
                (
                    await session.execute(
                        select(knowledge_projection_state.c.physical_collection).where(
                            *scope_predicates(knowledge_projection_state, self._scope),
                            knowledge_projection_state.c.physical_collection == generation,
                        )
                    )
                )
                .scalars()
                .all()
            )
            projected = (
                (
                    await session.execute(
                        select(knowledge_document_revision.c.revision_id).where(
                            *scope_predicates(knowledge_document_revision, self._scope),
                            knowledge_document_revision.c.revision_id.in_(
                                revision.source_revision_ids
                            ),
                            knowledge_document_revision.c.projection_digest.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return len(active) == 1 and set(projected) == set(revision.source_revision_ids)

    async def generation_for(self, revision: KnowledgeReviewRevision) -> str | None:
        async with self._sessions() as session:
            values = tuple(
                (
                    await session.execute(
                        select(knowledge_projection_state.c.physical_collection).where(
                            *scope_predicates(knowledge_projection_state, self._scope)
                        )
                    )
                ).scalars()
            )
        if len(values) != 1:
            return None
        generation = values[0]
        return generation if await self.verify(revision, generation) else None


class MysqlKnowledgeReviewRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def create_review(self, revision: KnowledgeReviewRevision) -> KnowledgeReviewRevision:
        if revision.project_id != self._scope.project_id:
            raise ReviewStateConflict("scope-mismatch")
        now = _naive_utc(datetime.now(UTC))
        async with self._sessions() as session, session.begin():
            await session.execute(
                insert(knowledge_review_revision).values(
                    **scope_values(self._scope),
                    **_review_values(revision),
                    created_at=now,
                    updated_at=now,
                )
            )
            await self._insert_history(
                session,
                review_id=revision.review_id,
                review_version=revision.version,
                action="created",
                actor_id=self._scope.actor_id,
                occurred_at=datetime.now(UTC),
            )
        return revision

    async def get_review(self, review_id: str) -> KnowledgeReviewRevision | None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(knowledge_review_revision).where(
                            *scope_predicates(knowledge_review_revision, self._scope),
                            knowledge_review_revision.c.review_id == review_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _review(row)

    async def list_reviews(
        self, source_revision_id: str | None = None
    ) -> tuple[KnowledgeReviewRevision, ...]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_review_revision)
                        .where(*scope_predicates(knowledge_review_revision, self._scope))
                        .order_by(
                            knowledge_review_revision.c.updated_at.desc(),
                            knowledge_review_revision.c.review_id,
                        )
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            _review(row)
            for row in rows
            if source_revision_id is None or source_revision_id in row["source_revision_ids"]
        )

    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]:
        review = await self.get_review(review_id)
        if review is None:
            raise ReviewNotFound("review-not-found")
        async with self._sessions() as session:
            attempts = {
                row["revision_id"]: row["parse_inventory_attempt"]
                for row in (
                    (
                        await session.execute(
                            select(
                                knowledge_document_revision.c.revision_id,
                                knowledge_document_revision.c.parse_inventory_attempt,
                            ).where(
                                *scope_predicates(knowledge_document_revision, self._scope),
                                knowledge_document_revision.c.revision_id.in_(
                                    review.source_revision_ids
                                ),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            }
            rows = (
                (
                    await session.execute(
                        select(knowledge_parse_inventory)
                        .where(
                            *scope_predicates(knowledge_parse_inventory, self._scope),
                            knowledge_parse_inventory.c.source_revision_id.in_(
                                review.source_revision_ids
                            ),
                        )
                        .order_by(
                            knowledge_parse_inventory.c.source_revision_id,
                            knowledge_parse_inventory.c.ordinal,
                        )
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            ReviewInventoryRecord(
                source_revision_id=row["source_revision_id"],
                item_id=row["item_id"],
                attempt=row["attempt"],
                kind=row["item_kind"],
                locator=row["locator"],
                status=row["status"],
                artifact_digest=row["artifact_digest"],
                reason=row["reason"],
                decision_actor_id=row["decision_actor_id"],
            )
            for row in rows
            if attempts.get(row["source_revision_id"]) == row["attempt"]
        )

    async def save_review(
        self,
        revision: KnowledgeReviewRevision,
        *,
        expected_version: int,
        actor_id: str,
        action: str,
        occurred_at: datetime,
    ) -> KnowledgeReviewRevision:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(knowledge_review_revision)
                .where(
                    *scope_predicates(knowledge_review_revision, self._scope),
                    knowledge_review_revision.c.review_id == revision.review_id,
                    knowledge_review_revision.c.version == expected_version,
                )
                .values(**_review_values(revision), updated_at=_naive_utc(datetime.now(UTC)))
            )
            if result.rowcount != 1:
                raise ReviewStateConflict("revision-conflict")
            await self._insert_history(
                session,
                review_id=revision.review_id,
                review_version=revision.version,
                action=action,
                actor_id=actor_id,
                occurred_at=occurred_at,
            )
            if revision.status is ReviewStatus.APPROVED:
                await self._write_event(
                    session,
                    event_type="knowledge.review.approved",
                    aggregate_type="KnowledgeReview",
                    aggregate_id=revision.review_id,
                    aggregate_version=revision.version,
                    key=f"review-approve:{revision.review_id}:{revision.version}",
                    now=datetime.now(UTC),
                    payload={
                        "reviewId": revision.review_id,
                        "approvalDigest": revision.approval_digest,
                        "reviewerActorId": revision.reviewer_actor_id,
                    },
                )
        return revision

    async def has_review_item(self, review_id: str, item_id: str) -> bool:
        review = await self.get_review(review_id)
        if review is None:
            return False
        async with self._sessions() as session:
            count = await session.scalar(
                select(knowledge_parse_inventory.c.item_id)
                .select_from(
                    knowledge_parse_inventory.join(
                        knowledge_document_revision,
                        knowledge_document_revision.c.revision_id
                        == knowledge_parse_inventory.c.source_revision_id,
                    )
                )
                .where(
                    *scope_predicates(knowledge_parse_inventory, self._scope),
                    *scope_predicates(knowledge_document_revision, self._scope),
                    knowledge_parse_inventory.c.source_revision_id.in_(review.source_revision_ids),
                    knowledge_parse_inventory.c.item_id == item_id,
                    knowledge_parse_inventory.c.attempt
                    == knowledge_document_revision.c.parse_inventory_attempt,
                )
            )
        return isinstance(count, str)

    async def list_decisions(self, review_id: str) -> tuple[KnowledgeReviewItemDecision, ...]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_review_item_decision)
                        .where(
                            *scope_predicates(knowledge_review_item_decision, self._scope),
                            knowledge_review_item_decision.c.review_id == review_id,
                        )
                        .order_by(knowledge_review_item_decision.c.item_id)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_decision(row) for row in rows)

    async def list_history(self, review_id: str) -> tuple[KnowledgeReviewHistoryEntry, ...]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_review_history)
                        .where(
                            *scope_predicates(knowledge_review_history, self._scope),
                            knowledge_review_history.c.review_id == review_id,
                        )
                        .order_by(knowledge_review_history.c.review_version)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_history(row) for row in rows)

    async def save_item_decision(
        self,
        revision: KnowledgeReviewRevision,
        decision: KnowledgeReviewItemDecision,
        *,
        expected_version: int,
    ) -> KnowledgeReviewRevision:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(knowledge_review_revision)
                .where(
                    *scope_predicates(knowledge_review_revision, self._scope),
                    knowledge_review_revision.c.review_id == revision.review_id,
                    knowledge_review_revision.c.version == expected_version,
                )
                .values(**_review_values(revision), updated_at=_naive_utc(decision.decided_at))
            )
            if result.rowcount != 1:
                raise ReviewStateConflict("revision-conflict")
            item_exists = await session.scalar(
                select(knowledge_parse_inventory.c.item_id)
                .select_from(
                    knowledge_parse_inventory.join(
                        knowledge_document_revision,
                        knowledge_document_revision.c.revision_id
                        == knowledge_parse_inventory.c.source_revision_id,
                    )
                )
                .where(
                    *scope_predicates(knowledge_parse_inventory, self._scope),
                    *scope_predicates(knowledge_document_revision, self._scope),
                    knowledge_parse_inventory.c.source_revision_id.in_(
                        revision.source_revision_ids
                    ),
                    knowledge_parse_inventory.c.item_id == decision.item_id,
                    knowledge_parse_inventory.c.attempt
                    == knowledge_document_revision.c.parse_inventory_attempt,
                )
            )
            if item_exists is None:
                raise ReviewStateConflict("review-item-not-found")
            await session.execute(
                delete(knowledge_review_item_decision).where(
                    *scope_predicates(knowledge_review_item_decision, self._scope),
                    knowledge_review_item_decision.c.review_id == revision.review_id,
                    knowledge_review_item_decision.c.item_id == decision.item_id,
                )
            )
            await session.execute(
                insert(knowledge_review_item_decision).values(
                    **scope_values(self._scope),
                    decision_id=scoped_outbox_id(
                        self._scope,
                        kind="knowledge-review-decision",
                        identity=f"{revision.review_id}:{decision.item_id}",
                    ),
                    review_id=revision.review_id,
                    item_id=decision.item_id,
                    check_kind=decision.check_kind.value,
                    status=decision.status.value,
                    note=decision.note,
                    decided_by=decision.actor_id,
                    review_version=decision.review_version,
                    decided_at=_naive_utc(decision.decided_at),
                )
            )
            await self._insert_history(
                session,
                review_id=revision.review_id,
                review_version=revision.version,
                action="item_decided",
                actor_id=decision.actor_id,
                occurred_at=decision.decided_at,
                item_id=decision.item_id,
            )
        return revision

    async def command_result(self, key: str, digest: str) -> KnowledgePublication | None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(knowledge_review_command).where(
                            *scope_predicates(knowledge_review_command, self._scope),
                            knowledge_review_command.c.idempotency_key == key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["request_digest"] != digest:
            raise ReviewCommandConflict("idempotency-conflict")
        return _publication_json(cast(dict[str, object], row["result"]))

    async def publish(
        self,
        revision: KnowledgeReviewRevision,
        publication: KnowledgePublication,
        *,
        expected_version: int,
        command_key: str,
        command_digest: str,
    ) -> KnowledgePublication:
        pointer_id = _current_pointer_id(self._scope)
        async with self._sessions() as session, session.begin():
            locked_review = (
                (
                    await session.execute(
                        select(
                            knowledge_review_revision.c.version,
                            knowledge_review_revision.c.status,
                        )
                        .where(
                            *scope_predicates(knowledge_review_revision, self._scope),
                            knowledge_review_revision.c.review_id == revision.review_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            replay = await self._locked_command(session, command_key, command_digest)
            if replay is not None:
                return replay
            if (
                locked_review is None
                or locked_review["version"] != expected_version
                or locked_review["status"] != ReviewStatus.APPROVED.value
            ):
                raise ReviewStateConflict("revision-conflict")
            result = await session.execute(
                update(knowledge_review_revision)
                .where(
                    *scope_predicates(knowledge_review_revision, self._scope),
                    knowledge_review_revision.c.review_id == revision.review_id,
                    knowledge_review_revision.c.version == expected_version,
                    knowledge_review_revision.c.status == ReviewStatus.APPROVED.value,
                )
                .values(
                    status=ReviewStatus.PUBLISHED.value,
                    version=expected_version + 1,
                    updated_at=_naive_utc(publication.published_at),
                )
            )
            if result.rowcount != 1:
                raise ReviewStateConflict("revision-conflict")
            await session.execute(
                insert(knowledge_publication).values(
                    **scope_values(self._scope), **_publication_values(publication)
                )
            )
            await session.execute(
                delete(knowledge_current_publication).where(
                    *scope_predicates(knowledge_current_publication, self._scope),
                    knowledge_current_publication.c.pointer_id == pointer_id,
                )
            )
            await session.execute(
                insert(knowledge_current_publication).values(
                    **scope_values(self._scope),
                    pointer_id=pointer_id,
                    publication_id=publication.publication_id,
                    updated_at=_naive_utc(publication.published_at),
                )
            )
            await self._insert_command(
                session, command_key, command_digest, publication, publication.published_at
            )
            await self._insert_history(
                session,
                review_id=revision.review_id,
                review_version=expected_version + 1,
                action="published",
                actor_id=publication.published_by,
                occurred_at=publication.published_at,
            )
            await self._write_event(
                session,
                event_type="knowledge.publication.published",
                aggregate_type="KnowledgePublication",
                aggregate_id=publication.publication_id,
                aggregate_version=1,
                key=command_key,
                now=publication.published_at,
                payload={
                    "publicationId": publication.publication_id,
                    "reviewId": publication.review_id,
                    "approvalDigest": publication.approval_digest,
                    "generation": publication.generation,
                    "sourceRevisionIds": publication.source_revision_ids,
                },
            )
        return publication

    async def get_publication(self, publication_id: str) -> KnowledgePublication | None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(knowledge_publication).where(
                            *scope_predicates(knowledge_publication, self._scope),
                            knowledge_publication.c.publication_id == publication_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _publication(row)

    async def current_publication(
        self, project_id: str | None = None
    ) -> KnowledgePublication | None:
        if project_id is not None and project_id != self._scope.project_id:
            return None
        pointer_id = _current_pointer_id(self._scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(knowledge_publication)
                        .join(
                            knowledge_current_publication,
                            (
                                knowledge_current_publication.c.project_id
                                == knowledge_publication.c.project_id
                            )
                            & (
                                knowledge_current_publication.c.publication_id
                                == knowledge_publication.c.publication_id
                            ),
                        )
                        .where(
                            *scope_predicates(knowledge_publication, self._scope),
                            knowledge_current_publication.c.pointer_id == pointer_id,
                            knowledge_publication.c.status == "published",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _publication(row)

    async def list_publications(self, review_id: str) -> tuple[KnowledgePublication, ...]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_publication)
                        .where(
                            *scope_predicates(knowledge_publication, self._scope),
                            knowledge_publication.c.review_id == review_id,
                        )
                        .order_by(knowledge_publication.c.published_at)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_publication(row) for row in rows)

    async def list_published_sources(self, *, now: datetime) -> tuple[PublishedSourceRecord, ...]:
        publication = await self.current_publication()
        if publication is None or publication.expires_at <= now:
            return ()
        async with self._sessions() as session:
            revision_rows = (
                (
                    await session.execute(
                        select(
                            knowledge_document_revision.c.revision_id,
                            knowledge_document_revision.c.document_id,
                            knowledge_document_revision.c.source_id,
                            knowledge_document_revision.c.parse_inventory_attempt,
                            knowledge_document.c.filename,
                            knowledge_source.c.name.label("source_name"),
                        )
                        .select_from(
                            knowledge_document_revision.join(
                                knowledge_document,
                                knowledge_document.c.document_id
                                == knowledge_document_revision.c.document_id,
                            ).join(
                                knowledge_source,
                                knowledge_source.c.source_id
                                == knowledge_document_revision.c.source_id,
                            )
                        )
                        .where(
                            *scope_predicates(knowledge_document_revision, self._scope),
                            *scope_predicates(knowledge_document, self._scope),
                            *scope_predicates(knowledge_source, self._scope),
                            knowledge_document_revision.c.revision_id.in_(
                                publication.source_revision_ids
                            ),
                            knowledge_document.c.deleted_at.is_(None),
                            knowledge_source.c.deleted_at.is_(None),
                        )
                        .order_by(knowledge_document_revision.c.revision_id)
                    )
                )
                .mappings()
                .all()
            )
            inventory_rows = (
                (
                    await session.execute(
                        select(
                            knowledge_parse_inventory.c.source_revision_id,
                            knowledge_parse_inventory.c.attempt,
                            knowledge_parse_inventory.c.item_id,
                        ).where(
                            *scope_predicates(knowledge_parse_inventory, self._scope),
                            knowledge_parse_inventory.c.source_revision_id.in_(
                                publication.source_revision_ids
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
        approved = set(publication.approved_item_ids)
        return tuple(
            PublishedSourceRecord(
                source_id=row["source_id"],
                document_id=row["document_id"],
                revision_id=row["revision_id"],
                source_name=row["source_name"],
                filename=row["filename"],
                publication_id=publication.publication_id,
                expires_at=publication.expires_at,
                approved_item_count=sum(
                    item["item_id"] in approved
                    for item in inventory_rows
                    if item["source_revision_id"] == row["revision_id"]
                    and item["attempt"] == row["parse_inventory_attempt"]
                ),
                inventory_item_count=sum(
                    1
                    for item in inventory_rows
                    if item["source_revision_id"] == row["revision_id"]
                    and item["attempt"] == row["parse_inventory_attempt"]
                ),
            )
            for row in revision_rows
        )

    async def comparison_target(
        self, review_id: str, item_id: str
    ) -> ReviewComparisonTarget | None:
        review = await self.get_review(review_id)
        if review is None:
            return None
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            knowledge_document.c.media_type,
                            knowledge_document_revision.c.original_blob_locator,
                            knowledge_document_revision.c.normalized_blob_locator,
                            knowledge_document_revision.c.parse_inventory_attempt,
                            knowledge_parse_inventory.c.attempt,
                        )
                        .select_from(
                            knowledge_parse_inventory.join(
                                knowledge_document_revision,
                                knowledge_document_revision.c.revision_id
                                == knowledge_parse_inventory.c.source_revision_id,
                            ).join(
                                knowledge_document,
                                knowledge_document.c.document_id
                                == knowledge_document_revision.c.document_id,
                            )
                        )
                        .where(
                            *scope_predicates(knowledge_parse_inventory, self._scope),
                            *scope_predicates(knowledge_document_revision, self._scope),
                            *scope_predicates(knowledge_document, self._scope),
                            knowledge_parse_inventory.c.source_revision_id.in_(
                                review.source_revision_ids
                            ),
                            knowledge_parse_inventory.c.item_id == item_id,
                            knowledge_parse_inventory.c.attempt
                            == knowledge_document_revision.c.parse_inventory_attempt,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return ReviewComparisonTarget(
            media_type=row["media_type"],
            original_locator=ArtifactLocator(row["original_blob_locator"]),
            normalized_locator=(
                None
                if row["normalized_blob_locator"] is None
                else ArtifactLocator(row["normalized_blob_locator"])
            ),
            item_id=item_id,
        )

    async def withdraw(
        self,
        publication: KnowledgePublication,
        *,
        expected_version: int,
        command_key: str,
        command_digest: str,
    ) -> KnowledgePublication:
        assert publication.withdrawn_at is not None
        pointer_id = _current_pointer_id(self._scope)
        async with self._sessions() as session, session.begin():
            locked_publication = (
                (
                    await session.execute(
                        select(
                            knowledge_publication.c.version,
                            knowledge_publication.c.status,
                        )
                        .where(
                            *scope_predicates(knowledge_publication, self._scope),
                            knowledge_publication.c.publication_id == publication.publication_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            replay = await self._locked_command(session, command_key, command_digest)
            if replay is not None:
                return replay
            if (
                locked_publication is None
                or locked_publication["version"] != expected_version
                or locked_publication["status"] != "published"
            ):
                raise ReviewStateConflict("revision-conflict")
            result = await session.execute(
                update(knowledge_publication)
                .where(
                    *scope_predicates(knowledge_publication, self._scope),
                    knowledge_publication.c.publication_id == publication.publication_id,
                    knowledge_publication.c.status == "published",
                    knowledge_publication.c.version == expected_version,
                )
                .values(
                    status="withdrawn",
                    version=publication.version,
                    withdrawn_by=publication.withdrawn_by,
                    withdrawn_at=_naive_utc(publication.withdrawn_at),
                )
            )
            if result.rowcount != 1:
                raise ReviewStateConflict("revision-conflict")
            await session.execute(
                delete(knowledge_current_publication).where(
                    *scope_predicates(knowledge_current_publication, self._scope),
                    knowledge_current_publication.c.pointer_id == pointer_id,
                    knowledge_current_publication.c.publication_id == publication.publication_id,
                )
            )
            review_version = await session.scalar(
                select(knowledge_review_revision.c.version)
                .where(
                    *scope_predicates(knowledge_review_revision, self._scope),
                    knowledge_review_revision.c.review_id == publication.review_id,
                )
                .with_for_update()
            )
            await session.execute(
                update(knowledge_review_revision)
                .where(
                    *scope_predicates(knowledge_review_revision, self._scope),
                    knowledge_review_revision.c.review_id == publication.review_id,
                )
                .values(
                    status=ReviewStatus.WITHDRAWN.value,
                    version=knowledge_review_revision.c.version + 1,
                    updated_at=_naive_utc(publication.withdrawn_at),
                )
            )
            await session.execute(
                insert(knowledge_publication_cleanup).values(
                    **scope_values(self._scope),
                    publication_id=publication.publication_id,
                    generation=publication.generation,
                    created_at=_naive_utc(publication.withdrawn_at),
                )
            )
            await self._insert_command(
                session,
                command_key,
                command_digest,
                publication,
                publication.withdrawn_at,
            )
            if isinstance(review_version, int):
                await self._insert_history(
                    session,
                    review_id=publication.review_id,
                    review_version=review_version + 1,
                    action="withdrawn",
                    actor_id=publication.withdrawn_by or self._scope.actor_id,
                    occurred_at=publication.withdrawn_at,
                )
            await self._write_event(
                session,
                event_type="knowledge.publication.withdrawn",
                aggregate_type="KnowledgePublication",
                aggregate_id=publication.publication_id,
                aggregate_version=2,
                key=command_key,
                now=publication.withdrawn_at,
                payload={
                    "publicationId": publication.publication_id,
                    "generation": publication.generation,
                },
            )
        return publication

    async def pending_projection_cleanup(self) -> tuple[str, ...]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(knowledge_publication_cleanup.c.generation)
                    .where(*scope_predicates(knowledge_publication_cleanup, self._scope))
                    .order_by(knowledge_publication_cleanup.c.created_at)
                )
            ).scalars()
        return tuple(rows)

    async def _locked_command(
        self, session: AsyncSession, key: str, digest: str
    ) -> KnowledgePublication | None:
        row = (
            (
                await session.execute(
                    select(knowledge_review_command)
                    .where(
                        *scope_predicates(knowledge_review_command, self._scope),
                        knowledge_review_command.c.idempotency_key == key,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["request_digest"] != digest:
            raise ReviewCommandConflict("idempotency-conflict")
        return _publication_json(cast(dict[str, object], row["result"]))

    async def _insert_command(
        self,
        session: AsyncSession,
        key: str,
        digest: str,
        result: KnowledgePublication,
        now: datetime,
    ) -> None:
        await session.execute(
            insert(knowledge_review_command).values(
                **scope_values(self._scope),
                command_id=scoped_outbox_id(
                    self._scope, kind="knowledge-review-command", identity=key
                ),
                idempotency_key=key,
                request_digest=digest,
                result=_publication_payload(result),
                created_at=_naive_utc(now),
            )
        )

    async def _insert_history(
        self,
        session: AsyncSession,
        *,
        review_id: str,
        review_version: int,
        action: str,
        actor_id: str,
        occurred_at: datetime,
        item_id: str | None = None,
    ) -> None:
        await session.execute(
            insert(knowledge_review_history).values(
                **scope_values(self._scope),
                history_id=scoped_outbox_id(
                    self._scope,
                    kind="knowledge-review-history",
                    identity=f"{review_id}:{review_version}",
                ),
                review_id=review_id,
                review_version=review_version,
                action=action,
                history_actor_id=actor_id,
                item_id=item_id,
                occurred_at=_naive_utc(occurred_at),
            )
        )

    async def _write_event(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        key: str,
        now: datetime,
        payload: dict[str, object],
    ) -> None:
        await write_project_event(
            session,
            scope=self._scope,
            envelope=ProjectEventEnvelope(
                event_id=scoped_outbox_id(
                    self._scope,
                    kind=event_type,
                    identity=f"{aggregate_id}:{aggregate_version}",
                ),
                event_type=event_type,
                schema_version=1,
                occurred_at=now.astimezone(UTC),
                scope_kind="PROJECT",
                enterprise_id=self._scope.enterprise_id,
                project_id=self._scope.project_id,
                actor_id=self._scope.actor_id,
                identity_mode=self._scope.identity_mode.value,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                correlation_id=aggregate_id,
                causation_id=None,
                idempotency_key=key,
                payload=payload,
            ),
        )


def _review_values(value: KnowledgeReviewRevision) -> dict[str, object]:
    return {
        "review_id": value.review_id,
        "source_revision_ids": list(value.source_revision_ids),
        "inventory_digest": value.inventory_digest,
        "chunk_manifest_digest": value.chunk_manifest_digest,
        "annotation_digest": value.annotation_digest,
        "dependency_digest": value.dependency_digest,
        "editor_actor_ids": list(value.editor_actor_ids),
        "reviewer_actor_id": value.reviewer_actor_id,
        "expires_at": _naive_utc(value.expires_at),
        "status": value.status.value,
        "version": value.version,
        "blocking_item_ids": list(value.blocking_item_ids),
        "approved_item_ids": list(value.approved_item_ids),
    }


def _current_pointer_id(scope: ProjectScopeContext) -> str:
    return scoped_outbox_id(scope, kind="knowledge-current-publication", identity="current")


def _review(row) -> KnowledgeReviewRevision:  # type: ignore[no-untyped-def]
    return KnowledgeReviewRevision(
        review_id=row["review_id"],
        project_id=row["project_id"],
        source_revision_ids=tuple(row["source_revision_ids"]),
        inventory_digest=row["inventory_digest"],
        chunk_manifest_digest=row["chunk_manifest_digest"],
        annotation_digest=row["annotation_digest"],
        dependency_digest=row["dependency_digest"],
        editor_actor_ids=tuple(row["editor_actor_ids"]),
        reviewer_actor_id=row["reviewer_actor_id"],
        expires_at=_aware_utc(row["expires_at"]),
        status=ReviewStatus(row["status"]),
        version=row["version"],
        blocking_item_ids=tuple(row["blocking_item_ids"]),
        approved_item_ids=tuple(row["approved_item_ids"]),
    )


def _publication_values(value: KnowledgePublication) -> dict[str, object]:
    return {
        "publication_id": value.publication_id,
        "review_id": value.review_id,
        "review_version": value.review_version,
        "version": value.version,
        "approval_digest": value.approval_digest,
        "source_revision_ids": list(value.source_revision_ids),
        "approved_item_ids": list(value.approved_item_ids),
        "generation": value.generation,
        "published_by": value.published_by,
        "published_at": _naive_utc(value.published_at),
        "expires_at": _naive_utc(value.expires_at),
        "status": value.status,
        "withdrawn_by": value.withdrawn_by,
        "withdrawn_at": None if value.withdrawn_at is None else _naive_utc(value.withdrawn_at),
    }


def _publication_payload(value: KnowledgePublication) -> dict[str, object]:
    payload = asdict(value)
    payload["source_revision_ids"] = list(value.source_revision_ids)
    payload["approved_item_ids"] = list(value.approved_item_ids)
    payload["published_at"] = value.published_at.isoformat()
    payload["expires_at"] = value.expires_at.isoformat()
    payload["withdrawn_at"] = None if value.withdrawn_at is None else value.withdrawn_at.isoformat()
    return payload


def _publication(row) -> KnowledgePublication:  # type: ignore[no-untyped-def]
    return KnowledgePublication(
        publication_id=row["publication_id"],
        project_id=row["project_id"],
        review_id=row["review_id"],
        review_version=row["review_version"],
        version=row["version"],
        approval_digest=row["approval_digest"],
        source_revision_ids=tuple(row["source_revision_ids"]),
        approved_item_ids=tuple(row["approved_item_ids"]),
        generation=row["generation"],
        published_by=row["published_by"],
        published_at=_aware_utc(row["published_at"]),
        expires_at=_aware_utc(row["expires_at"]),
        status=row["status"],
        withdrawn_by=row["withdrawn_by"],
        withdrawn_at=(None if row["withdrawn_at"] is None else _aware_utc(row["withdrawn_at"])),
    )


def _publication_json(payload: dict[str, object]) -> KnowledgePublication:
    return KnowledgePublication(
        publication_id=cast(str, payload["publication_id"]),
        project_id=cast(str, payload["project_id"]),
        review_id=cast(str, payload["review_id"]),
        review_version=cast(int, payload["review_version"]),
        version=cast(int, payload.get("version", 1)),
        approval_digest=cast(str, payload["approval_digest"]),
        source_revision_ids=tuple(cast(list[str], payload["source_revision_ids"])),
        approved_item_ids=tuple(cast(list[str], payload["approved_item_ids"])),
        generation=cast(str, payload["generation"]),
        published_by=cast(str, payload["published_by"]),
        published_at=datetime.fromisoformat(cast(str, payload["published_at"])),
        expires_at=datetime.fromisoformat(cast(str, payload["expires_at"])),
        status=cast(Literal["published", "withdrawn"], payload["status"]),
        withdrawn_by=cast(str | None, payload["withdrawn_by"]),
        withdrawn_at=(
            None
            if payload["withdrawn_at"] is None
            else datetime.fromisoformat(cast(str, payload["withdrawn_at"]))
        ),
    )


def _decision(row) -> KnowledgeReviewItemDecision:  # type: ignore[no-untyped-def]
    return KnowledgeReviewItemDecision(
        review_id=row["review_id"],
        item_id=row["item_id"],
        check_kind=ReviewCheckKind(row["check_kind"]),
        status=ReviewDecisionStatus(row["status"]),
        note=row["note"],
        actor_id=row["decided_by"],
        review_version=row["review_version"],
        decided_at=_aware_utc(row["decided_at"]),
    )


def _history(row) -> KnowledgeReviewHistoryEntry:  # type: ignore[no-untyped-def]
    return KnowledgeReviewHistoryEntry(
        review_id=row["review_id"],
        review_version=row["review_version"],
        action=row["action"],
        actor_id=row["history_actor_id"],
        occurred_at=_aware_utc(row["occurred_at"]),
        item_id=row["item_id"],
    )


def _naive_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("review timestamps must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)
