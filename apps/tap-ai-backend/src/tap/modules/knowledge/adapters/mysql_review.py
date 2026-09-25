"""MySQL authority for knowledge review, publication, and withdrawal."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import (
    Column,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.adapters.mysql import project
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_parse_inventory,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_projection import knowledge_projection_state
from tap.modules.knowledge.application.review import (
    KnowledgeReviewListItemRead,
    KnowledgeReviewRead,
    ProjectionNotReady,
    PublishedSourceRecord,
    ReviewCommandConflict,
    ReviewComparisonTarget,
    ReviewDecisionPageRead,
    ReviewHistoryPageRead,
    ReviewInventoryPageRead,
    ReviewInventoryRecord,
    ReviewNotFound,
    ReviewPublicationPageRead,
    ReviewStateConflict,
)
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.review import (
    KnowledgePublication,
    KnowledgeReviewHistoryEntry,
    KnowledgeReviewItemDecision,
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
    canonical_digest,
    review_dependency_digest,
    review_id_for,
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
    UniqueConstraint(
        "review_id", "item_id", "review_version", name="uq_review_item_decision_version"
    ),
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
    Column("decision_id", String(128)),
    Column("decision_digest", String(71)),
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

Index(
    "ix_review_decision_project_review_version",
    knowledge_review_item_decision.c.project_id,
    knowledge_review_item_decision.c.review_id,
    knowledge_review_item_decision.c.review_version,
)
Index(
    "ix_publication_project_review_cursor",
    knowledge_publication.c.project_id,
    knowledge_publication.c.review_id,
    knowledge_publication.c.publication_id,
)

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

    async def generations_for(
        self, revisions: tuple[KnowledgeReviewRevision, ...]
    ) -> dict[str, str | None]:
        if not revisions:
            return {}
        source_revision_ids = tuple(
            sorted({item for revision in revisions for item in revision.source_revision_ids})
        )
        async with self._sessions() as session:
            generations = tuple(
                (
                    await session.execute(
                        select(knowledge_projection_state.c.physical_collection).where(
                            *scope_predicates(knowledge_projection_state, self._scope)
                        )
                    )
                ).scalars()
            )
            projected = frozenset(
                (
                    await session.execute(
                        select(knowledge_document_revision.c.revision_id).where(
                            *scope_predicates(knowledge_document_revision, self._scope),
                            knowledge_document_revision.c.revision_id.in_(source_revision_ids),
                            knowledge_document_revision.c.projection_digest.is_not(None),
                        )
                    )
                ).scalars()
            )
        generation = generations[0] if len(generations) == 1 else None
        return {
            revision.review_id: (
                generation
                if generation is not None and set(revision.source_revision_ids) <= projected
                else None
            )
            for revision in revisions
        }


class MysqlKnowledgeReviewRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def resolve_open_review_target(self, *, document_id: str, source_revision_id: str) -> str:
        async with self._sessions() as session:
            current_document = await session.scalar(
                select(knowledge_document.c.document_id)
                .select_from(
                    knowledge_document.join(
                        knowledge_source,
                        (knowledge_source.c.source_id == knowledge_document.c.source_id)
                        & (knowledge_source.c.enterprise_id == knowledge_document.c.enterprise_id)
                        & (knowledge_source.c.project_id == knowledge_document.c.project_id),
                    ).join(
                        knowledge_document_revision,
                        (
                            knowledge_document_revision.c.document_id
                            == knowledge_document.c.document_id
                        )
                        & (
                            knowledge_document_revision.c.enterprise_id
                            == knowledge_document.c.enterprise_id
                        )
                        & (
                            knowledge_document_revision.c.project_id
                            == knowledge_document.c.project_id
                        ),
                    )
                )
                .where(
                    *scope_predicates(knowledge_document, self._scope),
                    *scope_predicates(knowledge_source, self._scope),
                    *scope_predicates(knowledge_document_revision, self._scope),
                    knowledge_document.c.document_id == document_id,
                    knowledge_document.c.current_revision_id == source_revision_id,
                    knowledge_document_revision.c.revision_id == source_revision_id,
                    knowledge_document.c.activated_at.is_not(None),
                    knowledge_document.c.deleted_at.is_(None),
                    knowledge_source.c.deleted_at.is_(None),
                )
            )
            if current_document != document_id:
                raise ReviewStateConflict("review-source-not-current")
            existing_rows = await self._open_review_candidates(
                session, source_revision_id=source_revision_id, lock=False
            )
        if len(existing_rows) > 1:
            raise ReviewStateConflict("parallel-review-conflict")
        if not existing_rows:
            return review_id_for(self._scope.project_id, (source_revision_id,))
        existing = _review(existing_rows[0])
        if existing.source_revision_ids != (source_revision_id,):
            raise ReviewStateConflict("review-source-set-conflict")
        return existing.review_id

    async def create_or_open_review(
        self,
        *,
        document_id: str,
        source_revision_id: str,
        authorized_review_id: str,
        actor_id: str,
        expires_at: datetime,
        command_key: str,
        command_digest: str,
        now: datetime,
    ) -> KnowledgeReviewRevision:
        if actor_id != self._scope.actor_id:
            raise ReviewStateConflict("scope-mismatch")
        async with self._sessions() as session, session.begin():
            await self._lock_project(session)
            existing_rows = await self._open_review_candidates(
                session, source_revision_id=source_revision_id, lock=True
            )
            if len(existing_rows) > 1:
                raise ReviewStateConflict("parallel-review-conflict")
            if existing_rows:
                existing = _review(existing_rows[0])
                if existing.source_revision_ids != (source_revision_id,):
                    raise ReviewStateConflict("review-source-set-conflict")
                actual_review_id = existing.review_id
            else:
                actual_review_id = review_id_for(self._scope.project_id, (source_revision_id,))
            if actual_review_id != authorized_review_id:
                raise ReviewStateConflict("review-open-target-changed")
            replay_id = await self._locked_open_command(session, command_key, command_digest)
            if replay_id is not None:
                replay = next(
                    (_review(row) for row in existing_rows if row["review_id"] == replay_id),
                    None,
                )
                if replay is None:
                    raise ReviewStateConflict("review-command-result-missing")
                return replay

            authority = (
                (
                    await session.execute(
                        select(
                            knowledge_document.c.status,
                            knowledge_document.c.stage,
                            knowledge_document_revision.c.revision_id,
                            knowledge_document_revision.c.source_content_hash,
                            knowledge_document_revision.c.parse_inventory_attempt,
                            knowledge_document_revision.c.parser_config_digest,
                            knowledge_document_revision.c.parse_inventory_digest,
                            knowledge_document_revision.c.chunk_manifest_digest,
                            knowledge_document_revision.c.projection_digest,
                        )
                        .select_from(
                            knowledge_document.join(
                                knowledge_source,
                                (knowledge_source.c.source_id == knowledge_document.c.source_id)
                                & (
                                    knowledge_source.c.enterprise_id
                                    == knowledge_document.c.enterprise_id
                                )
                                & (
                                    knowledge_source.c.project_id == knowledge_document.c.project_id
                                ),
                            ).join(
                                knowledge_document_revision,
                                (
                                    knowledge_document_revision.c.document_id
                                    == knowledge_document.c.document_id
                                )
                                & (
                                    knowledge_document_revision.c.enterprise_id
                                    == knowledge_document.c.enterprise_id
                                )
                                & (
                                    knowledge_document_revision.c.project_id
                                    == knowledge_document.c.project_id
                                ),
                            )
                        )
                        .where(
                            *scope_predicates(knowledge_document, self._scope),
                            *scope_predicates(knowledge_source, self._scope),
                            *scope_predicates(knowledge_document_revision, self._scope),
                            knowledge_document.c.document_id == document_id,
                            knowledge_document.c.current_revision_id == source_revision_id,
                            knowledge_document_revision.c.revision_id == source_revision_id,
                            knowledge_document.c.activated_at.is_not(None),
                            knowledge_document.c.deleted_at.is_(None),
                            knowledge_source.c.deleted_at.is_(None),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if authority is None:
                raise ReviewStateConflict("review-source-not-current")

            if existing_rows:
                existing = _review(existing_rows[0])
                await self._insert_open_command(
                    session,
                    command_key,
                    command_digest,
                    existing.review_id,
                    now,
                )
                return existing

            if authority["status"] != "ready" or authority["stage"] != "ready":
                raise ReviewStateConflict("review-source-not-ready")
            required = (
                authority["parse_inventory_attempt"],
                authority["parser_config_digest"],
                authority["parse_inventory_digest"],
                authority["chunk_manifest_digest"],
                authority["projection_digest"],
            )
            if any(value is None for value in required):
                raise ReviewStateConflict("review-source-incomplete")
            generations = tuple(
                (
                    await session.execute(
                        select(knowledge_projection_state.c.physical_collection).where(
                            *scope_predicates(knowledge_projection_state, self._scope)
                        )
                    )
                ).scalars()
            )
            if len(generations) != 1:
                raise ReviewStateConflict("review-projection-not-ready")

            inventory_rows = (
                (
                    await session.execute(
                        select(knowledge_parse_inventory)
                        .where(
                            *scope_predicates(knowledge_parse_inventory, self._scope),
                            knowledge_parse_inventory.c.source_revision_id == source_revision_id,
                            knowledge_parse_inventory.c.attempt
                            == authority["parse_inventory_attempt"],
                        )
                        .order_by(knowledge_parse_inventory.c.ordinal)
                        .limit(501)
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            if not inventory_rows or len(inventory_rows) > 500:
                raise ReviewStateConflict("review-inventory-unavailable")
            try:
                inventory = tuple(
                    ParseInventoryItem(
                        source_revision_id=row["source_revision_id"],
                        item_id=row["item_id"],
                        kind=ParseInventoryKind(row["item_kind"]),
                        locator=row["locator"],
                        status=ParseInventoryStatus(row["status"]),
                        artifact_digest=row["artifact_digest"],
                        reason=row["reason"],
                        decision_actor_id=row["decision_actor_id"],
                    )
                    for row in inventory_rows
                )
                inventory_digest = parse_inventory_digest(inventory)
            except (TypeError, ValueError) as error:
                raise ReviewStateConflict("review-inventory-invalid") from error
            if inventory_digest != authority["parse_inventory_digest"]:
                raise ReviewStateConflict("review-inventory-changed")
            if any(item.status is not ParseInventoryStatus.PARSED for item in inventory):
                raise ReviewStateConflict("review-inventory-incomplete")

            revision = KnowledgeReviewRevision(
                review_id=review_id_for(self._scope.project_id, (source_revision_id,)),
                project_id=self._scope.project_id,
                source_revision_ids=(source_revision_id,),
                inventory_digest=inventory_digest,
                chunk_manifest_digest=cast(str, authority["chunk_manifest_digest"]),
                annotation_digest=canonical_digest([]),
                dependency_digest=review_dependency_digest(
                    (
                        (
                            source_revision_id,
                            authority["source_content_hash"],
                            cast(str, authority["parser_config_digest"]),
                            cast(str, authority["projection_digest"]),
                        ),
                    )
                ),
                editor_actor_ids=(actor_id,),
                reviewer_actor_id=None,
                expires_at=expires_at,
                status=ReviewStatus.CHECKING,
                version=1,
                blocking_item_ids=(),
                approved_item_ids=tuple(sorted(item.item_id for item in inventory)),
            )
            await session.execute(
                insert(knowledge_review_revision).values(
                    **scope_values(self._scope),
                    **_review_values(revision),
                    created_at=_naive_utc(now),
                    updated_at=_naive_utc(now),
                )
            )
            await self._insert_history(
                session,
                review_id=revision.review_id,
                review_version=revision.version,
                action="created",
                actor_id=actor_id,
                occurred_at=now,
            )
            await self._insert_open_command(
                session,
                command_key,
                command_digest,
                revision.review_id,
                now,
            )
            return revision

    async def _open_review_candidates(
        self, session: AsyncSession, *, source_revision_id: str, lock: bool
    ):  # type: ignore[no-untyped-def]
        statement = (
            select(knowledge_review_revision)
            .where(
                *scope_predicates(knowledge_review_revision, self._scope),
                func.json_contains(
                    knowledge_review_revision.c.source_revision_ids,
                    func.json_quote(source_revision_id),
                )
                == 1,
            )
            .order_by(knowledge_review_revision.c.review_id)
            .limit(2)
        )
        if lock:
            statement = statement.with_for_update()
        return (await session.execute(statement)).mappings().all()

    async def _locked_review(
        self, session: AsyncSession, review_id: str
    ) -> KnowledgeReviewRevision | None:
        row = (
            (
                await session.execute(
                    select(knowledge_review_revision)
                    .where(
                        *scope_predicates(knowledge_review_revision, self._scope),
                        knowledge_review_revision.c.review_id == review_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _review(row)

    async def create_review(self, revision: KnowledgeReviewRevision) -> KnowledgeReviewRevision:
        if revision.project_id != self._scope.project_id:
            raise ReviewStateConflict("scope-mismatch")
        now = _naive_utc(datetime.now(UTC))
        async with self._sessions() as session, session.begin():
            await self._lock_project(session)
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
        self,
        source_revision_id: str | None = None,
        *,
        limit: int = 50,
        after_review_id: str | None = None,
    ) -> tuple[KnowledgeReviewRevision, ...]:
        predicates = list(scope_predicates(knowledge_review_revision, self._scope))
        if source_revision_id is not None:
            predicates.append(
                func.json_contains(
                    knowledge_review_revision.c.source_revision_ids,
                    func.json_quote(source_revision_id),
                )
                == 1
            )
        if after_review_id is not None:
            predicates.append(knowledge_review_revision.c.review_id > after_review_id)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_review_revision)
                        .where(*predicates)
                        .order_by(knowledge_review_revision.c.review_id)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_review(row) for row in rows)

    async def review_list_batch(
        self,
        revisions: tuple[KnowledgeReviewRevision, ...],
        *,
        child_limit: int,
    ) -> tuple[KnowledgeReviewListItemRead, ...]:
        if not revisions:
            return ()
        review_ids = tuple(revision.review_id for revision in revisions)
        source_revision_ids = tuple(
            sorted({item for revision in revisions for item in revision.source_revision_ids})
        )
        current_decisions = (
            select(
                *knowledge_review_item_decision.c,
                func.row_number()
                .over(
                    partition_by=(
                        knowledge_review_item_decision.c.review_id,
                        knowledge_review_item_decision.c.item_id,
                    ),
                    order_by=(
                        knowledge_review_item_decision.c.review_version.desc(),
                        knowledge_review_item_decision.c.decision_id.desc(),
                    ),
                )
                .label("decision_rank"),
            )
            .where(
                *scope_predicates(knowledge_review_item_decision, self._scope),
                knowledge_review_item_decision.c.review_id.in_(review_ids),
            )
            .subquery()
        )
        current_decision_pages = (
            select(
                *current_decisions.c,
                func.row_number()
                .over(
                    partition_by=current_decisions.c.review_id,
                    order_by=current_decisions.c.item_id,
                )
                .label("review_rank"),
            )
            .where(current_decisions.c.decision_rank == 1)
            .subquery()
        )
        authority_inventory_pages = (
            select(
                *knowledge_parse_inventory.c,
                func.row_number()
                .over(
                    partition_by=knowledge_parse_inventory.c.source_revision_id,
                    order_by=knowledge_parse_inventory.c.ordinal,
                )
                .label("source_rank"),
            )
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
                knowledge_parse_inventory.c.source_revision_id.in_(source_revision_ids),
                knowledge_parse_inventory.c.attempt
                == knowledge_document_revision.c.parse_inventory_attempt,
            )
            .subquery()
        )
        review_inventory_pages = (
            select(
                knowledge_review_revision.c.review_id.label("batch_review_id"),
                *knowledge_parse_inventory.c,
                func.row_number()
                .over(
                    partition_by=knowledge_review_revision.c.review_id,
                    order_by=knowledge_parse_inventory.c.item_id,
                )
                .label("page_rank"),
            )
            .select_from(
                knowledge_review_revision.join(
                    knowledge_parse_inventory,
                    func.json_contains(
                        knowledge_review_revision.c.source_revision_ids,
                        func.json_quote(knowledge_parse_inventory.c.source_revision_id),
                    )
                    == 1,
                ).join(
                    knowledge_document_revision,
                    knowledge_document_revision.c.revision_id
                    == knowledge_parse_inventory.c.source_revision_id,
                )
            )
            .where(
                *scope_predicates(knowledge_review_revision, self._scope),
                *scope_predicates(knowledge_parse_inventory, self._scope),
                *scope_predicates(knowledge_document_revision, self._scope),
                knowledge_review_revision.c.review_id.in_(review_ids),
                knowledge_parse_inventory.c.source_revision_id.in_(source_revision_ids),
                knowledge_document_revision.c.revision_id.in_(source_revision_ids),
                knowledge_parse_inventory.c.attempt
                == knowledge_document_revision.c.parse_inventory_attempt,
            )
            .subquery()
        )
        decision_pages = (
            select(
                *knowledge_review_item_decision.c,
                func.count()
                .over(partition_by=knowledge_review_item_decision.c.review_id)
                .label("total_count"),
                func.row_number()
                .over(
                    partition_by=knowledge_review_item_decision.c.review_id,
                    order_by=(
                        knowledge_review_item_decision.c.review_version,
                        knowledge_review_item_decision.c.item_id,
                    ),
                )
                .label("page_rank"),
            )
            .where(
                *scope_predicates(knowledge_review_item_decision, self._scope),
                knowledge_review_item_decision.c.review_id.in_(review_ids),
            )
            .subquery()
        )
        history_pages = (
            select(
                *knowledge_review_history.c,
                func.count()
                .over(partition_by=knowledge_review_history.c.review_id)
                .label("total_count"),
                func.row_number()
                .over(
                    partition_by=knowledge_review_history.c.review_id,
                    order_by=knowledge_review_history.c.review_version,
                )
                .label("page_rank"),
            )
            .where(
                *scope_predicates(knowledge_review_history, self._scope),
                knowledge_review_history.c.review_id.in_(review_ids),
            )
            .subquery()
        )
        publication_pages = (
            select(
                *knowledge_publication.c,
                func.count()
                .over(partition_by=knowledge_publication.c.review_id)
                .label("total_count"),
                func.row_number()
                .over(
                    partition_by=knowledge_publication.c.review_id,
                    order_by=knowledge_publication.c.publication_id,
                )
                .label("page_rank"),
            )
            .where(
                *scope_predicates(knowledge_publication, self._scope),
                knowledge_publication.c.review_id.in_(review_ids),
            )
            .subquery()
        )
        async with self._sessions() as session:
            decision_rows = (
                (
                    await session.execute(
                        select(current_decision_pages)
                        .where(current_decision_pages.c.review_rank <= 501)
                        .order_by(
                            current_decision_pages.c.review_id,
                            current_decision_pages.c.item_id,
                        )
                        .limit(len(review_ids) * 501)
                    )
                )
                .mappings()
                .all()
            )
            inventory_rows = (
                (
                    await session.execute(
                        select(authority_inventory_pages)
                        .where(authority_inventory_pages.c.source_rank <= 501)
                        .order_by(
                            authority_inventory_pages.c.source_revision_id,
                            authority_inventory_pages.c.ordinal,
                        )
                        .limit(len(source_revision_ids) * 501)
                    )
                )
                .mappings()
                .all()
            )
            inventory_page_rows = (
                (
                    await session.execute(
                        select(review_inventory_pages)
                        .where(review_inventory_pages.c.page_rank <= child_limit + 1)
                        .order_by(
                            review_inventory_pages.c.batch_review_id,
                            review_inventory_pages.c.item_id,
                        )
                        .limit(len(review_ids) * (child_limit + 1))
                    )
                )
                .mappings()
                .all()
            )
            inventory_count_rows = (
                (
                    await session.execute(
                        select(
                            knowledge_parse_inventory.c.source_revision_id,
                            knowledge_parse_inventory.c.status,
                            func.count().label("item_count"),
                        )
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
                            knowledge_parse_inventory.c.source_revision_id.in_(source_revision_ids),
                            knowledge_parse_inventory.c.attempt
                            == knowledge_document_revision.c.parse_inventory_attempt,
                        )
                        .group_by(
                            knowledge_parse_inventory.c.source_revision_id,
                            knowledge_parse_inventory.c.status,
                        )
                    )
                )
                .mappings()
                .all()
            )
            revision_rows = (
                (
                    await session.execute(
                        select(
                            knowledge_document_revision.c.revision_id,
                            knowledge_document_revision.c.source_content_hash,
                            knowledge_document_revision.c.parser_config_digest,
                            knowledge_document_revision.c.parse_inventory_attempt,
                            knowledge_document_revision.c.parse_inventory_digest,
                            knowledge_document_revision.c.chunk_manifest_digest,
                            knowledge_document_revision.c.projection_digest,
                        )
                        .where(
                            *scope_predicates(knowledge_document_revision, self._scope),
                            knowledge_document_revision.c.revision_id.in_(source_revision_ids),
                        )
                        .order_by(knowledge_document_revision.c.revision_id)
                    )
                )
                .mappings()
                .all()
            )
            decision_page_rows = (
                (
                    await session.execute(
                        select(decision_pages)
                        .where(decision_pages.c.page_rank <= child_limit + 1)
                        .order_by(decision_pages.c.review_id, decision_pages.c.review_version)
                    )
                )
                .mappings()
                .all()
            )
            history_page_rows = (
                (
                    await session.execute(
                        select(history_pages)
                        .where(history_pages.c.page_rank <= child_limit + 1)
                        .order_by(history_pages.c.review_id, history_pages.c.review_version)
                    )
                )
                .mappings()
                .all()
            )
            publication_page_rows = (
                (
                    await session.execute(
                        select(publication_pages)
                        .where(publication_pages.c.page_rank <= child_limit + 1)
                        .order_by(
                            publication_pages.c.review_id,
                            publication_pages.c.publication_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            current_row = (
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
                            knowledge_current_publication.c.pointer_id
                            == _current_pointer_id(self._scope),
                            knowledge_publication.c.status == "published",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )

        current = None if current_row is None else _publication(current_row)
        values: list[KnowledgeReviewListItemRead] = []
        for revision in revisions:
            review_decisions = tuple(
                _decision(row) for row in decision_rows if row["review_id"] == revision.review_id
            )
            inventory = tuple(
                row
                for row in inventory_rows
                if row["source_revision_id"] in revision.source_revision_ids
            )
            inventory_by_item = tuple(
                row for row in inventory_page_rows if row["batch_review_id"] == revision.review_id
            )
            inventory_items = tuple(
                _inventory_record(row) for row in inventory_by_item[:child_limit]
            )
            inventory_counts = {
                status: sum(
                    int(row["item_count"])
                    for row in inventory_count_rows
                    if row["source_revision_id"] in revision.source_revision_ids
                    and row["status"] == status
                )
                for status in ("parsed", "failed", "needs_review", "excluded")
            }
            review_decision_page_rows = tuple(
                row for row in decision_page_rows if row["review_id"] == revision.review_id
            )
            decision_items = tuple(
                _decision(row) for row in review_decision_page_rows[:child_limit]
            )
            review_history_page_rows = tuple(
                row for row in history_page_rows if row["review_id"] == revision.review_id
            )
            history_items = tuple(_history(row) for row in review_history_page_rows[:child_limit])
            review_publication_page_rows = tuple(
                row for row in publication_page_rows if row["review_id"] == revision.review_id
            )
            publication_items = tuple(
                _publication(row) for row in review_publication_page_rows[:child_limit]
            )
            review_revision_rows = tuple(
                row for row in revision_rows if row["revision_id"] in revision.source_revision_ids
            )
            review_current = (
                current if current is not None and current.review_id == revision.review_id else None
            )
            values.append(
                KnowledgeReviewListItemRead(
                    review=KnowledgeReviewRead(
                        revision=revision,
                        decisions=review_decisions,
                        decision_history=decision_items,
                        history=history_items,
                    ),
                    inventory=ReviewInventoryPageRead(
                        items=inventory_items,
                        total_count=sum(inventory_counts.values()),
                        parsed_count=inventory_counts["parsed"],
                        failed_count=inventory_counts["failed"],
                        needs_review_count=inventory_counts["needs_review"],
                        excluded_count=inventory_counts["excluded"],
                        next_cursor=(
                            inventory_items[-1].item_id
                            if len(inventory_by_item) > child_limit and inventory_items
                            else None
                        ),
                    ),
                    decision_history=ReviewDecisionPageRead(
                        items=decision_items,
                        total_count=(
                            int(review_decision_page_rows[0]["total_count"])
                            if review_decision_page_rows
                            else 0
                        ),
                        next_cursor=(
                            decision_items[-1].review_version
                            if len(review_decision_page_rows) > child_limit and decision_items
                            else None
                        ),
                    ),
                    history=ReviewHistoryPageRead(
                        items=history_items,
                        total_count=(
                            int(review_history_page_rows[0]["total_count"])
                            if review_history_page_rows
                            else 0
                        ),
                        next_cursor=(
                            history_items[-1].review_version
                            if len(review_history_page_rows) > child_limit and history_items
                            else None
                        ),
                    ),
                    publications=ReviewPublicationPageRead(
                        items=publication_items,
                        total_count=(
                            int(review_publication_page_rows[0]["total_count"])
                            if review_publication_page_rows
                            else 0
                        ),
                        next_cursor=(
                            publication_items[-1].publication_id
                            if len(review_publication_page_rows) > child_limit and publication_items
                            else None
                        ),
                    ),
                    current_publication=review_current,
                    authoritative=_review_authority_matches(
                        revision, review_revision_rows, inventory
                    ),
                )
            )
        return tuple(values)

    async def list_inventory(self, review_id: str) -> tuple[ReviewInventoryRecord, ...]:
        review = await self.get_review(review_id)
        if review is None:
            raise ReviewNotFound("review-not-found")
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_parse_inventory)
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
                                review.source_revision_ids
                            ),
                            knowledge_parse_inventory.c.attempt
                            == knowledge_document_revision.c.parse_inventory_attempt,
                        )
                        .order_by(
                            knowledge_parse_inventory.c.source_revision_id,
                            knowledge_parse_inventory.c.ordinal,
                        )
                        .limit(501)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_inventory_record(row) for row in rows)

    async def inventory_page(
        self, review_id: str, *, limit: int, after_item_id: str | None
    ) -> ReviewInventoryPageRead:
        review = await self.get_review(review_id)
        if review is None:
            raise ReviewNotFound("review-not-found")
        predicates = [
            *scope_predicates(knowledge_parse_inventory, self._scope),
            *scope_predicates(knowledge_document_revision, self._scope),
            knowledge_parse_inventory.c.source_revision_id.in_(review.source_revision_ids),
            knowledge_parse_inventory.c.attempt
            == knowledge_document_revision.c.parse_inventory_attempt,
        ]
        page_predicates = list(predicates)
        if after_item_id is not None:
            page_predicates.append(knowledge_parse_inventory.c.item_id > after_item_id)
        joined = knowledge_parse_inventory.join(
            knowledge_document_revision,
            knowledge_document_revision.c.revision_id
            == knowledge_parse_inventory.c.source_revision_id,
        )
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_parse_inventory)
                        .select_from(joined)
                        .where(*page_predicates)
                        .order_by(knowledge_parse_inventory.c.item_id)
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
            count_rows = (
                (
                    await session.execute(
                        select(
                            knowledge_parse_inventory.c.status,
                            func.count().label("item_count"),
                        )
                        .select_from(joined)
                        .where(*predicates)
                        .group_by(knowledge_parse_inventory.c.status)
                    )
                )
                .mappings()
                .all()
            )
        counts = {row["status"]: row["item_count"] for row in count_rows}
        items = tuple(_inventory_record(row) for row in rows[:limit])
        return ReviewInventoryPageRead(
            items=items,
            total_count=sum(counts.values()),
            parsed_count=counts.get("parsed", 0),
            failed_count=counts.get("failed", 0),
            needs_review_count=counts.get("needs_review", 0),
            excluded_count=counts.get("excluded", 0),
            next_cursor=items[-1].item_id if len(rows) > limit else None,
        )

    async def review_authority(self, revision: KnowledgeReviewRevision) -> bool:
        async with self._sessions() as session:
            return await self._review_authority(session, revision, lock=False)

    async def _review_authority(
        self,
        session: AsyncSession,
        revision: KnowledgeReviewRevision,
        *,
        lock: bool,
    ) -> bool:
        statement = (
            select(
                knowledge_document_revision.c.revision_id,
                knowledge_document_revision.c.source_content_hash,
                knowledge_document_revision.c.parser_config_digest,
                knowledge_document_revision.c.parse_inventory_attempt,
                knowledge_document_revision.c.parse_inventory_digest,
                knowledge_document_revision.c.chunk_manifest_digest,
                knowledge_document_revision.c.projection_digest,
            )
            .select_from(
                knowledge_document_revision.join(
                    knowledge_document,
                    (knowledge_document.c.document_id == knowledge_document_revision.c.document_id)
                    & (
                        knowledge_document.c.enterprise_id
                        == knowledge_document_revision.c.enterprise_id
                    )
                    & (knowledge_document.c.project_id == knowledge_document_revision.c.project_id),
                ).join(
                    knowledge_source,
                    (knowledge_source.c.source_id == knowledge_document.c.source_id)
                    & (knowledge_source.c.enterprise_id == knowledge_document.c.enterprise_id)
                    & (knowledge_source.c.project_id == knowledge_document.c.project_id),
                )
            )
            .where(
                *scope_predicates(knowledge_document_revision, self._scope),
                *scope_predicates(knowledge_document, self._scope),
                *scope_predicates(knowledge_source, self._scope),
                knowledge_document_revision.c.revision_id.in_(revision.source_revision_ids),
                knowledge_document.c.current_revision_id
                == knowledge_document_revision.c.revision_id,
                knowledge_document.c.status == "ready",
                knowledge_document.c.stage == "ready",
                knowledge_document.c.activated_at.is_not(None),
                knowledge_document.c.deleted_at.is_(None),
                knowledge_source.c.deleted_at.is_(None),
            )
            .order_by(knowledge_document_revision.c.revision_id)
        )
        if lock:
            statement = statement.with_for_update()
        rows = (await session.execute(statement)).mappings().all()
        if len(rows) != len(revision.source_revision_ids):
            return False
        inventory_statement = (
            select(knowledge_parse_inventory)
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
                knowledge_parse_inventory.c.source_revision_id.in_(revision.source_revision_ids),
                knowledge_parse_inventory.c.attempt
                == knowledge_document_revision.c.parse_inventory_attempt,
            )
            .order_by(
                knowledge_parse_inventory.c.source_revision_id,
                knowledge_parse_inventory.c.ordinal,
            )
            .limit(501)
        )
        if lock:
            inventory_statement = inventory_statement.with_for_update()
        inventory_rows = (await session.execute(inventory_statement)).mappings().all()
        return _review_authority_matches(revision, rows, inventory_rows)

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
            await self._lock_project(session)
            locked = await self._locked_review(session, revision.review_id)
            if locked is None or locked.version != expected_version:
                raise ReviewStateConflict("revision-conflict")
            if action in {"submitted", "approved"}:
                if not await self._review_authority(session, locked, lock=True):
                    raise ReviewStateConflict("review-authority-changed")
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
        ranked = (
            select(
                *knowledge_review_item_decision.c,
                func.row_number()
                .over(
                    partition_by=knowledge_review_item_decision.c.item_id,
                    order_by=(
                        knowledge_review_item_decision.c.review_version.desc(),
                        knowledge_review_item_decision.c.decision_id.desc(),
                    ),
                )
                .label("decision_rank"),
            )
            .where(
                *scope_predicates(knowledge_review_item_decision, self._scope),
                knowledge_review_item_decision.c.review_id == review_id,
            )
            .subquery()
        )
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(ranked).where(ranked.c.decision_rank == 1).order_by(ranked.c.item_id)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_decision(row) for row in rows)

    async def list_decision_history(
        self, review_id: str
    ) -> tuple[KnowledgeReviewItemDecision, ...]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(knowledge_review_item_decision)
                        .where(
                            *scope_predicates(knowledge_review_item_decision, self._scope),
                            knowledge_review_item_decision.c.review_id == review_id,
                        )
                        .order_by(
                            knowledge_review_item_decision.c.review_version.desc(),
                            knowledge_review_item_decision.c.item_id,
                        )
                        .limit(501)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(reversed([_decision(row) for row in rows]))

    async def decision_history_page(
        self, review_id: str, *, limit: int, after_version: int | None
    ) -> ReviewDecisionPageRead:
        predicates = [
            *scope_predicates(knowledge_review_item_decision, self._scope),
            knowledge_review_item_decision.c.review_id == review_id,
        ]
        page_predicates = list(predicates)
        if after_version is not None:
            page_predicates.append(knowledge_review_item_decision.c.review_version > after_version)
        async with self._sessions() as session:
            total = await session.scalar(
                select(func.count()).select_from(knowledge_review_item_decision).where(*predicates)
            )
            rows = (
                (
                    await session.execute(
                        select(knowledge_review_item_decision)
                        .where(*page_predicates)
                        .order_by(knowledge_review_item_decision.c.review_version)
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
        items = tuple(_decision(row) for row in rows[:limit])
        return ReviewDecisionPageRead(
            items=items,
            total_count=int(total or 0),
            next_cursor=items[-1].review_version if len(rows) > limit else None,
        )

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
                        .order_by(knowledge_review_history.c.review_version.desc())
                        .limit(501)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(reversed([_history(row) for row in rows]))

    async def history_page(
        self, review_id: str, *, limit: int, after_version: int | None
    ) -> ReviewHistoryPageRead:
        predicates = [
            *scope_predicates(knowledge_review_history, self._scope),
            knowledge_review_history.c.review_id == review_id,
        ]
        page_predicates = list(predicates)
        if after_version is not None:
            page_predicates.append(knowledge_review_history.c.review_version > after_version)
        async with self._sessions() as session:
            total = await session.scalar(
                select(func.count()).select_from(knowledge_review_history).where(*predicates)
            )
            rows = (
                (
                    await session.execute(
                        select(knowledge_review_history)
                        .where(*page_predicates)
                        .order_by(knowledge_review_history.c.review_version)
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
        items = tuple(_history(row) for row in rows[:limit])
        return ReviewHistoryPageRead(
            items=items,
            total_count=int(total or 0),
            next_cursor=items[-1].review_version if len(rows) > limit else None,
        )

    async def save_item_decision(
        self,
        revision: KnowledgeReviewRevision,
        decision: KnowledgeReviewItemDecision,
        *,
        expected_version: int,
    ) -> KnowledgeReviewRevision:
        async with self._sessions() as session, session.begin():
            await self._lock_project(session)
            locked = await self._locked_review(session, revision.review_id)
            if locked is None or locked.version != expected_version:
                raise ReviewStateConflict("revision-conflict")
            if not await self._review_authority(session, locked, lock=True):
                raise ReviewStateConflict("review-authority-changed")
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
                insert(knowledge_review_item_decision).values(
                    **scope_values(self._scope),
                    decision_id=decision.decision_id,
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
                decision_id=decision.decision_id,
                decision_digest=decision.decision_digest,
            )
        return revision

    async def command_result(
        self, key: str, digest: str, *, legacy_digest: str | None = None
    ) -> KnowledgePublication | None:
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
        if row["request_digest"] not in {digest, legacy_digest}:
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
            await self._lock_project(session)
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
            if not await self._review_authority(session, revision, lock=True):
                raise ReviewStateConflict("review-authority-changed")
            generation = await session.scalar(
                select(knowledge_projection_state.c.physical_collection)
                .where(
                    *scope_predicates(knowledge_projection_state, self._scope),
                    knowledge_projection_state.c.physical_collection == publication.generation,
                )
                .with_for_update()
            )
            if generation != publication.generation:
                raise ProjectionNotReady("projection-not-ready")
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
                        .order_by(knowledge_publication.c.published_at.desc())
                        .limit(500)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(reversed([_publication(row) for row in rows]))

    async def publication_page(
        self, review_id: str, *, limit: int, after_publication_id: str | None
    ) -> ReviewPublicationPageRead:
        predicates = [
            *scope_predicates(knowledge_publication, self._scope),
            knowledge_publication.c.review_id == review_id,
        ]
        page_predicates = list(predicates)
        if after_publication_id is not None:
            page_predicates.append(knowledge_publication.c.publication_id > after_publication_id)
        async with self._sessions() as session:
            total = await session.scalar(
                select(func.count()).select_from(knowledge_publication).where(*predicates)
            )
            rows = (
                (
                    await session.execute(
                        select(knowledge_publication)
                        .where(*page_predicates)
                        .order_by(knowledge_publication.c.publication_id)
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
        items = tuple(_publication(row) for row in rows[:limit])
        return ReviewPublicationPageRead(
            items=items,
            total_count=int(total or 0),
            next_cursor=items[-1].publication_id if len(rows) > limit else None,
        )

    async def list_published_sources(self, *, now: datetime) -> tuple[PublishedSourceRecord, ...]:
        pointer_id = _current_pointer_id(self._scope)
        async with self._sessions() as session, session.begin():
            await self._lock_project(session)
            publication_row = (
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
            if publication_row is None:
                return ()
            publication = _publication(publication_row)
            if publication.expires_at <= now:
                return ()
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
                            knowledge_document.c.current_revision_id
                            == knowledge_document_revision.c.revision_id,
                            knowledge_document.c.status == "ready",
                            knowledge_document.c.stage == "ready",
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
                            knowledge_parse_inventory.c.status,
                        )
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
                                publication.source_revision_ids
                            ),
                            knowledge_parse_inventory.c.attempt
                            == knowledge_document_revision.c.parse_inventory_attempt,
                        )
                        .order_by(
                            knowledge_parse_inventory.c.source_revision_id,
                            knowledge_parse_inventory.c.ordinal,
                        )
                        .limit(501)
                    )
                )
                .mappings()
                .all()
            )
        approved = set(publication.approved_item_ids)
        if {row["revision_id"] for row in revision_rows} != set(publication.source_revision_ids):
            return ()
        if len(inventory_rows) > 500:
            return ()
        latest_inventory = tuple(inventory_rows)
        publishable_ids = {
            item["item_id"] for item in latest_inventory if item["status"] == "parsed"
        }
        if not approved or not approved <= publishable_ids:
            return ()
        records = tuple(
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
                    for item in latest_inventory
                    if item["source_revision_id"] == row["revision_id"]
                ),
                inventory_item_count=sum(
                    1
                    for item in latest_inventory
                    if item["source_revision_id"] == row["revision_id"]
                ),
            )
            for row in revision_rows
        )
        return tuple(record for record in records if record.approved_item_count > 0)

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
            await self._lock_project(session)
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
            current_publication_id = await session.scalar(
                select(knowledge_current_publication.c.publication_id)
                .where(
                    *scope_predicates(knowledge_current_publication, self._scope),
                    knowledge_current_publication.c.pointer_id == pointer_id,
                )
                .with_for_update()
            )
            if current_publication_id != publication.publication_id:
                raise ReviewStateConflict("publication-not-current")
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

    async def _locked_open_command(
        self, session: AsyncSession, key: str, digest: str
    ) -> str | None:
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
        result = cast(dict[str, object], row["result"])
        review_id = result.get("review_id")
        if result.get("operation") != "open-review" or not isinstance(review_id, str):
            raise ReviewCommandConflict("idempotency-conflict")
        return review_id

    async def _lock_project(self, session: AsyncSession) -> None:
        locked = await session.scalar(
            select(project.c.project_id)
            .where(
                project.c.enterprise_id == self._scope.enterprise_id,
                project.c.project_id == self._scope.project_id,
            )
            .with_for_update()
        )
        if locked != self._scope.project_id:
            raise ReviewStateConflict("scope-mismatch")

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

    async def _insert_open_command(
        self,
        session: AsyncSession,
        key: str,
        digest: str,
        review_id: str,
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
                result={"operation": "open-review", "review_id": review_id},
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
        decision_id: str | None = None,
        decision_digest: str | None = None,
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
                decision_id=decision_id,
                decision_digest=decision_digest,
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


def _review_authority_matches(
    revision: KnowledgeReviewRevision,
    revision_rows,  # type: ignore[no-untyped-def]
    inventory_rows,  # type: ignore[no-untyped-def]
) -> bool:
    if len(revision_rows) != len(revision.source_revision_ids) or len(inventory_rows) > 500:
        return False
    if any(
        not any(item["source_revision_id"] == source_id for item in inventory_rows)
        for source_id in revision.source_revision_ids
    ):
        return False
    inventory_digests = tuple(
        (row["revision_id"], row["parse_inventory_digest"]) for row in revision_rows
    )
    try:
        actual_inventory_digests = tuple(
            (
                source_id,
                parse_inventory_digest(
                    tuple(
                        ParseInventoryItem(
                            source_revision_id=item["source_revision_id"],
                            item_id=item["item_id"],
                            kind=ParseInventoryKind(item["item_kind"]),
                            locator=item["locator"],
                            status=ParseInventoryStatus(item["status"]),
                            artifact_digest=item["artifact_digest"],
                            reason=item["reason"],
                            decision_actor_id=item["decision_actor_id"],
                        )
                        for item in inventory_rows
                        if item["source_revision_id"] == source_id
                    )
                ),
            )
            for source_id in (row["revision_id"] for row in revision_rows)
        )
    except (TypeError, ValueError):
        return False
    if inventory_digests != actual_inventory_digests:
        return False
    chunk_digests = tuple(
        (row["revision_id"], row["chunk_manifest_digest"]) for row in revision_rows
    )
    expected_inventory = (
        inventory_digests[0][1]
        if len(inventory_digests) == 1
        else canonical_digest({"sourceRevisionInventoryDigests": inventory_digests})
    )
    expected_chunks = (
        chunk_digests[0][1]
        if len(chunk_digests) == 1
        else canonical_digest({"sourceRevisionChunkDigests": chunk_digests})
    )
    expected_dependency = review_dependency_digest(
        tuple(
            (
                row["revision_id"],
                row["source_content_hash"],
                row["parser_config_digest"],
                row["projection_digest"],
            )
            for row in revision_rows
        )
    )
    latest_ids = {row["item_id"] for row in inventory_rows}
    return (
        expected_inventory == revision.inventory_digest
        and expected_chunks == revision.chunk_manifest_digest
        and expected_dependency == revision.dependency_digest
        and set(revision.approved_item_ids) <= latest_ids
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


def _inventory_record(row) -> ReviewInventoryRecord:  # type: ignore[no-untyped-def]
    return ReviewInventoryRecord(
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


def _history(row) -> KnowledgeReviewHistoryEntry:  # type: ignore[no-untyped-def]
    return KnowledgeReviewHistoryEntry(
        review_id=row["review_id"],
        review_version=row["review_version"],
        action=row["action"],
        actor_id=row["history_actor_id"],
        occurred_at=_aware_utc(row["occurred_at"]),
        item_id=row["item_id"],
        decision_id=row["decision_id"],
        decision_digest=row["decision_digest"],
    )


def _naive_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("review timestamps must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)
