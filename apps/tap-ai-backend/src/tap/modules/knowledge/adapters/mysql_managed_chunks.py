"""Project-scoped editable chunks with immutable generations and real index updates."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import tiktoken
from sqlalchemy import JSON, Column, Integer, String, Table, exists, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.managed_chunk_search import ParentGroups
from tap.modules.knowledge.adapters.mysql_documents import (
    MysqlDocumentRepository,
)
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_chunk_manifest as manifests,
)
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document as documents,
)
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document_revision as revisions,
)
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_ingestion_job as jobs,
)
from tap.modules.knowledge.adapters.mysql_documents import knowledge_parse_inventory as inventories
from tap.modules.knowledge.adapters.mysql_documents import knowledge_source as sources
from tap.modules.knowledge.domain.documents import (
    BlockKind,
    ChunkDraft,
    DocumentId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    revision_id_for,
)
from tap.modules.knowledge.domain.managed_chunks import (
    ChunkSettings,
    generate_chunks,
    new_chunk,
    split_text,
)
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    original_alignment_binding_digest,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.sources import chunk_manifest_digest, projection_digest
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    ArtifactStore,
    DocumentEmbeddingPort,
    DocumentIndexPort,
    IngestionWork,
    JobKind,
    JobStage,
    ManifestChunk,
)
from tap.modules.knowledge.ports.models import SearchHit
from tap.platform.db.project_scope import scope_predicates, scope_values
from tap.platform.db.schema import augment_project_table, metadata

managed_documents = Table(
    "knowledge_managed_document",
    metadata,
    Column("document_id", String(64), primary_key=True),
    Column("original_revision_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("settings_json", JSON, nullable=False),
    Column("chunks_json", JSON, nullable=False),
    Column("index_status", String(24), nullable=False),
    Column("index_error", String(240)),
)
managed_history = Table(
    "knowledge_managed_generation",
    metadata,
    Column("document_id", String(64), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("snapshot_json", JSON, nullable=False),
)
for table in (managed_documents, managed_history):
    augment_project_table(table)


class ChunkConflict(ValueError):
    pass


class ChunkNotFound(ValueError):
    pass


class MysqlManagedChunks:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        repository: MysqlDocumentRepository,
        scope: ProjectScopeContext,
        artifacts: ArtifactStore,
        embeddings: DocumentEmbeddingPort,
        index: DocumentIndexPort,
        embedding_model_alias: str,
        embedding_dimension: int,
        index_version: str,
        parser: Any = None,
    ) -> None:
        self.parser = parser
        self.sessions = sessions
        self.repository = repository
        self.scope = scope
        self.artifacts = artifacts
        self.embeddings = embeddings
        self.index = index
        self.embedding_model_alias = embedding_model_alias
        self.embedding_dimension = embedding_dimension
        self.index_version = index_version
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def _where(self, table: Table, document_id: str) -> tuple[Any, ...]:
        return (*scope_predicates(table, self.scope), table.c.document_id == document_id)

    def _active_source(self):  # type: ignore[no-untyped-def]
        return exists(
            select(sources.c.source_id).where(
                *scope_predicates(sources, self.scope),
                sources.c.source_id == documents.c.source_id,
                sources.c.deleted_at.is_(None),
            )
        )

    async def _document(
        self, session: AsyncSession, document_id: str, *, lock: bool = False
    ) -> dict[str, Any]:
        query = select(documents).where(
            *self._where(documents, document_id),
            documents.c.deleted_at.is_(None),
            self._active_source(),
        )
        row = (await session.execute(query.with_for_update() if lock else query)).mappings().first()
        if row is None:
            raise ChunkNotFound("document not found")
        return dict(row)

    async def _ensure(self, document_id: str) -> dict[str, Any]:
        # Reads must remain available while the worker holds its generation lock.
        async with self.sessions() as session:
            document = await self._document(session, document_id)
            if document["media_type"] in {"image/png", "image/jpeg"}:
                # Model-read flowchart arrows are corrected and published through review.
                raise ChunkConflict("flowchart images are managed through review")
            existing = (
                (
                    await session.execute(
                        select(managed_documents).where(
                            *self._where(managed_documents, document_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return dict(existing)
        async with self.sessions() as session, session.begin():
            document = await self._document(session, document_id, lock=True)
            existing = (
                (
                    await session.execute(
                        select(managed_documents).where(
                            *self._where(managed_documents, document_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return dict(existing)
            revision = (
                (
                    await session.execute(
                        select(revisions).where(
                            *scope_predicates(revisions, self.scope),
                            revisions.c.revision_id == document["current_revision_id"],
                        )
                    )
                )
                .mappings()
                .first()
            )
            if (
                revision is None
                or revision["chunks_blob_locator"] is None
                or document["status"] != "ready"
            ):
                raise ChunkConflict("document must finish processing before chunk management")
            drafts = await self.artifacts.read_chunks(
                ArtifactLocator(revision["chunks_blob_locator"])
            )
            chunks = []
            for i, draft in enumerate(drafts):
                chunk = new_chunk(draft.content, i + 1)
                chunk["chunkId"] = str(draft.chunk_id)
                chunk["indexStatus"] = "ready"
                chunks.append(chunk)
            row = dict(
                document_id=document_id,
                original_revision_id=document["current_revision_id"],
                version=1,
                settings_json=asdict(ChunkSettings()),
                chunks_json=chunks,
                index_status="ready",
                index_error=None,
                **scope_values(self.scope),
            )
            await session.execute(insert(managed_documents).values(**row))
            await session.execute(
                insert(managed_history).values(
                    document_id=document_id,
                    version=1,
                    snapshot_json=row,
                    **scope_values(self.scope),
                )
            )
            return row

    async def settings(self, document_id: str) -> dict[str, Any]:
        state = await self._ensure(document_id)
        return {
            "settings": state["settings_json"],
            "version": state["version"],
            "originalRevisionId": state["original_revision_id"],
        }

    async def parent_groups(self, hits: tuple[SearchHit, ...]) -> ParentGroups:
        if not hits:
            return {}
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            manifests.c.chunk_id,
                            manifests.c.revision_id,
                            manifests.c.parent_id,
                            documents.c.document_id,
                            documents.c.source_id,
                            documents.c.current_revision_id,
                            documents.c.status,
                            documents.c.deleted_at,
                            documents.c.chunk_count,
                            managed_documents.c.index_status,
                            managed_documents.c.settings_json,
                            managed_documents.c.chunks_json,
                        )
                        .select_from(
                            manifests.join(
                                documents,
                                (manifests.c.root_id == documents.c.document_id)
                                & (manifests.c.project_id == documents.c.project_id),
                            ).outerjoin(
                                managed_documents,
                                (documents.c.document_id == managed_documents.c.document_id)
                                & (documents.c.project_id == managed_documents.c.project_id),
                            )
                        )
                        .where(
                            *scope_predicates(manifests, self.scope),
                            manifests.c.chunk_id.in_(tuple(hit.chunk_id for hit in hits)),
                            self._active_source(),
                        )
                    )
                )
                .mappings()
                .all()
            )
        facts = {row["chunk_id"]: row for row in rows}
        groups: ParentGroups = {}
        for hit in hits:
            row = facts.get(hit.chunk_id)
            if row is None:
                if hit.family.value == "doc":
                    groups[hit.chunk_id] = None
                continue
            if (
                row["deleted_at"] is not None
                or row["status"] != "ready"
                or row["chunk_count"] == 0
                or row["current_revision_id"] != hit.source.revision
                or row["revision_id"] != hit.source.revision
                # Search adapters normalize legacy physical source IDs to the
                # canonical, SQL-authorized source before returning SearchHit.
                or row["source_id"] != hit.source.source_id
                or row["index_status"] not in {None, "ready"}
            ):
                groups[hit.chunk_id] = None
                continue
            if row["settings_json"] is None or row["settings_json"]["mode"] != "parent_child":
                continue
            # This parent_id comes from our immutable SQL manifest, never provider
            # headings or user-authored text. The current hierarchy validates visibility.
            child_id = str(row["parent_id"]).removeprefix("managed_")
            parent = next(
                (
                    parent
                    for parent in row["chunks_json"]
                    if parent["enabled"]
                    and any(
                        child["chunkId"] == child_id and child["enabled"]
                        for child in parent["children"]
                    )
                ),
                None,
            )
            groups[hit.chunk_id] = (
                None
                if parent is None
                else (row["document_id"], row["revision_id"], parent["chunkId"])
            )
        return groups

    async def original(self, document_id: str) -> tuple[str, str, bytes]:
        async with self.sessions() as session:
            document = await self._document(session, document_id)
            state = (
                (
                    await session.execute(
                        select(managed_documents).where(
                            *self._where(managed_documents, document_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
            original_revision_id = (
                document["current_revision_id"] if state is None else state["original_revision_id"]
            )
            revision = (
                (
                    await session.execute(
                        select(revisions).where(
                            *scope_predicates(revisions, self.scope),
                            revisions.c.revision_id == original_revision_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
        content = await self.artifacts.read_original(
            ArtifactLocator(revision["original_blob_locator"])
        )
        if canonical_sha256(content) != revision["source_content_hash"]:
            raise ValueError("original document integrity check failed")
        return document["filename"], document["media_type"], content

    async def _original_text(self, document_id: str, state: dict[str, Any]) -> str:
        async with self.sessions() as session:
            await self._document(session, document_id)
            revision = (
                (
                    await session.execute(
                        select(revisions).where(
                            *scope_predicates(revisions, self.scope),
                            revisions.c.revision_id == state["original_revision_id"],
                        )
                    )
                )
                .mappings()
                .one()
            )
        normalized = await self.artifacts.read_normalized(
            ArtifactLocator(revision["normalized_blob_locator"])
        )
        return "\n\n".join(block.text for block in normalized.blocks)

    def _view(self, chunk: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(chunk)
        value["tokens"] = len(self.encoding.encode(value["content"], disallowed_special=()))
        value["indexStatus"] = (
            "pending"
            if state["index_status"] == "waiting_ingestion"
            else "error"
            if state["index_status"] == "ingestion_error"
            else state["index_status"]
        )
        value["indexError"] = state["index_error"]
        value["children"] = [self._view(c, state) for c in value["children"]]
        return value

    async def list_chunks(
        self,
        document_id: str,
        *,
        q: str = "",
        status: str = "all",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        state = await self._ensure(document_id)
        values = [
            c
            for c in state["chunks_json"]
            if (status == "all" or c["enabled"] == (status == "enabled"))
            and (
                q.casefold() in c["content"].casefold()
                or any(q.casefold() in child["content"].casefold() for child in c["children"])
            )
        ]
        return dict(
            items=[self._view(c, state) for c in values[(page - 1) * page_size : page * page_size]],
            total=len(values),
            page=page,
            pageSize=page_size,
        )

    async def preview(self, document_id: str, settings: ChunkSettings) -> dict[str, Any]:
        state = await self._ensure(document_id)
        chunks = generate_chunks(await self._original_text(document_id, state), settings)
        return dict(
            items=[
                self._view(c, {"index_status": "pending", "index_error": None}) for c in chunks[:20]
            ],
            total=len(chunks),
        )

    async def configure(
        self, document_id: str, settings: ChunkSettings, version: int, confirm_replace: bool
    ) -> dict[str, Any]:
        state = await self._ensure(document_id)
        if not confirm_replace:
            raise ChunkConflict("confirm replacement of all chunk edits")
        chunks = generate_chunks(await self._original_text(document_id, state), settings)
        await self._save(document_id, version, chunks, asdict(settings))
        return await self.settings(document_id)

    @staticmethod
    def _find(
        chunks: list[dict[str, Any]], chunk_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
        for chunk in chunks:
            if chunk["chunkId"] == chunk_id:
                return chunk, chunks, None
            for child in chunk["children"]:
                if child["chunkId"] == chunk_id:
                    return child, chunk["children"], chunk
        raise ChunkNotFound("chunk not found")

    async def _replay(
        self, document_id: str, key: str | None, digest: str
    ) -> dict[str, Any] | None:
        if key is None:
            return None
        async with self.sessions() as session:
            await self._document(session, document_id)
            receipt = (
                await session.execute(
                    select(managed_history.c.snapshot_json).where(
                        *self._where(managed_history, document_id),
                        managed_history.c.snapshot_json["commandKey"].as_string() == key,
                    )
                )
            ).scalar_one_or_none()
        if receipt is None:
            return None
        if receipt["commandDigest"] != digest:
            raise ChunkConflict("idempotency key was used for different content")
        return dict(receipt["commandResult"])

    async def _lock_mode(
        self, session: AsyncSession, document: dict[str, Any], settings: dict[str, Any]
    ) -> None:
        await session.execute(
            select(sources.c.source_id)
            .where(
                *scope_predicates(sources, self.scope), sources.c.source_id == document["source_id"]
            )
            .with_for_update()
        )
        peers = (
            (
                await session.execute(
                    select(managed_documents.c.settings_json)
                    .join(
                        documents,
                        (managed_documents.c.document_id == documents.c.document_id)
                        & (managed_documents.c.project_id == documents.c.project_id),
                    )
                    .where(
                        *scope_predicates(managed_documents, self.scope),
                        documents.c.source_id == document["source_id"],
                        or_(
                            managed_documents.c.version > 1,
                            managed_documents.c.index_status != "ready",
                        ),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if any(peer["mode"] != settings["mode"] for peer in peers):
            raise ChunkConflict("chunk mode is fixed for this source")

    async def _save(
        self,
        document_id: str,
        expected: int,
        chunks: list[dict[str, Any]],
        settings: dict[str, Any],
        receipt: dict[str, Any] | None = None,
        initializing: bool = False,
    ) -> None:
        if sum(1 + len(item["children"]) for item in chunks) > 10000:
            raise ValueError("document has too many chunks")
        async with self.sessions() as session, session.begin():
            document = await self._document(session, document_id)
            await self._lock_mode(session, document, settings)
            await self._document(session, document_id, lock=True)
            state = (
                (
                    await session.execute(
                        select(managed_documents)
                        .where(*self._where(managed_documents, document_id))
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if (
                state["index_status"] in {"waiting_ingestion", "ingestion_error"}
                and not initializing
            ):
                raise ChunkConflict("initial chunk processing is still pending")
            if state["version"] != expected:
                raise ChunkConflict("chunks changed; refresh before saving")
            values = dict(
                version=expected + 1,
                chunks_json=chunks,
                settings_json=settings,
                index_status="pending",
                index_error=None,
            )
            await session.execute(
                update(managed_documents)
                .where(*self._where(managed_documents, document_id))
                .values(**values)
            )
            await session.execute(
                insert(managed_history).values(
                    document_id=document_id,
                    version=expected + 1,
                    snapshot_json={**values, **(receipt or {})},
                    **scope_values(self.scope),
                )
            )
            await session.execute(
                update(documents)
                .where(*self._where(documents, document_id))
                .values(
                    status="processing",
                    stage="embedding",
                    updated_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )

    async def create(
        self, document_id: str, content: str, parent_id: str | None = None, key: str | None = None
    ) -> dict[str, Any]:
        digest = canonical_sha256(json.dumps(["create", parent_id, content]).encode())
        replay = await self._replay(document_id, key, digest)
        if replay is not None:
            return self._view(replay, await self._ensure(document_id))
        state = await self._ensure(document_id)
        chunks = copy.deepcopy(state["chunks_json"])
        settings = ChunkSettings(**state["settings_json"])
        target = chunks
        if parent_id is not None:
            parent, _, grandparent = self._find(chunks, parent_id)
            if grandparent is not None or settings.mode != "parent_child" or not parent["enabled"]:
                raise ChunkConflict("only enabled parent chunks accept children")
            target = parent["children"]
        elif settings.mode == "parent_child" and settings.parentMode == "full_doc":
            raise ChunkConflict("full-document parents cannot be added manually")
        chunk = new_chunk(content, len(target) + 1, edited=True)
        if len(content) > 32768 and parent_id is not None:
            raise ValueError("child content exceeds 32768 characters")
        if parent_id is None and settings.mode == "parent_child":
            chunk["children"] = [
                new_chunk(t, i + 1) for i, t in enumerate(split_text(content, settings, child=True))
            ]
        target.append(chunk)
        await self._save(
            document_id,
            state["version"],
            chunks,
            state["settings_json"],
            receipt=dict(commandKey=key, commandDigest=digest, commandResult=chunk)
            if key
            else None,
        )
        return self._view(chunk, await self._ensure(document_id))

    async def change(
        self,
        document_id: str,
        chunk_id: str,
        version: int,
        *,
        content: str | None = None,
        enabled: bool | None = None,
        regenerate_children: bool = False,
        delete: bool = False,
    ) -> dict[str, Any]:
        state = await self._ensure(document_id)
        chunks = copy.deepcopy(state["chunks_json"])
        chunk, siblings, parent = self._find(chunks, chunk_id)
        if chunk["version"] != version:
            raise ChunkConflict("chunk changed; refresh before saving")
        settings = ChunkSettings(**state["settings_json"])
        if content is not None:
            if not chunk["enabled"] or (parent is not None and not parent["enabled"]):
                raise ChunkConflict("disabled chunks cannot be edited")
            if (
                parent is None
                and settings.mode == "parent_child"
                and settings.parentMode == "full_doc"
            ):
                raise ChunkConflict("replace the original file to modify a full-document parent")
            new_chunk(content, 1)  # Validate safe text before changing persistent state.
            if len(content) > 32768:
                raise ValueError("editable content exceeds 32768 characters")
            chunk.update(content=content, charCount=len(content), edited=True)
            if parent is None and settings.mode == "parent_child" and regenerate_children:
                chunk["children"] = [
                    new_chunk(t, i + 1)
                    for i, t in enumerate(split_text(content, settings, child=True))
                ]
        if enabled is not None:
            chunk["enabled"] = enabled
        chunk["version"] += 1
        if delete:
            siblings.remove(chunk)
        for i, item in enumerate(siblings):
            item["position"] = i + 1
        await self._save(document_id, state["version"], chunks, state["settings_json"])
        return self._view(chunk, await self._ensure(document_id))

    async def batch(
        self, document_id: str, action: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        result: dict[str, Any] = dict(succeeded=[], failed=[])
        for item in items:
            try:
                if action == "retry":
                    state = await self._ensure(document_id)
                    chunk, _, _ = self._find(state["chunks_json"], item["chunkId"])
                    if chunk["version"] != item["version"]:
                        raise ChunkConflict("chunk changed; refresh before retry")
                    await self._save(
                        document_id, state["version"], state["chunks_json"], state["settings_json"]
                    )
                else:
                    await self.change(
                        document_id,
                        item["chunkId"],
                        item["version"],
                        enabled=(action == "enable") if action in {"enable", "disable"} else None,
                        delete=action == "delete",
                    )
                result["succeeded"].append(item["chunkId"])
            except (ValueError, ChunkConflict, ChunkNotFound) as error:
                result["failed"].append(dict(chunkId=item["chunkId"], error=str(error)))
        return result

    async def import_contents(
        self, document_id: str, contents: list[str], key: str | None = None
    ) -> dict[str, Any]:
        digest = canonical_sha256(json.dumps(["import", contents]).encode())
        replay = await self._replay(document_id, key, digest)
        if replay is not None:
            return replay
        state = await self._ensure(document_id)
        settings = ChunkSettings(**state["settings_json"])
        if settings.mode == "parent_child" and settings.parentMode == "full_doc":
            raise ChunkConflict("full-document parents cannot be imported")
        chunks = copy.deepcopy(state["chunks_json"])
        ids = []
        for content in contents:
            if len(content) > 32768:
                raise ValueError("imported content exceeds 32768 characters")
            chunk = new_chunk(content, len(chunks) + 1, edited=True)
            if settings.mode == "parent_child":
                chunk["children"] = [
                    new_chunk(t, i + 1)
                    for i, t in enumerate(split_text(content, settings, child=True))
                ]
            chunks.append(chunk)
            ids.append(chunk["chunkId"])
        await self._save(
            document_id,
            state["version"],
            chunks,
            state["settings_json"],
            receipt=dict(
                commandKey=key, commandDigest=digest, commandResult=dict(succeeded=ids, failed=[])
            )
            if key
            else None,
        )
        return dict(succeeded=ids, failed=[])

    async def capture_upload_settings(self, document_id: str, settings: ChunkSettings) -> None:
        async with self.sessions() as session, session.begin():
            document = await self._document(session, document_id)
            await self._lock_mode(session, document, asdict(settings))
            document = await self._document(session, document_id, lock=True)
            existing = (
                (
                    await session.execute(
                        select(managed_documents).where(
                            *self._where(managed_documents, document_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return
            await session.execute(
                insert(managed_documents).values(
                    document_id=document_id,
                    original_revision_id=document["current_revision_id"],
                    version=1,
                    settings_json=asdict(settings),
                    chunks_json=[],
                    index_status="waiting_ingestion",
                    index_error=None,
                    **scope_values(self.scope),
                )
            )

    async def preview_upload(
        self, filename: str, media_type: str, content: bytes, settings: ChunkSettings
    ) -> dict[str, Any]:
        from tap.modules.knowledge.domain.documents import DocumentSource

        if self.parser is None:
            raise ValueError("upload parser is unavailable")
        normalized = await self.parser.parse(
            DocumentSource(
                filename,
                MediaType(media_type),
                content,
                DocumentId("doc_" + "0" * 32),
                revision_id_for(
                    DocumentId("doc_" + "0" * 32), canonical_sha256(content), "tapper-parser-v1"
                ),
            )
        )
        chunks = generate_chunks("\n\n".join(b.text for b in normalized.blocks), settings)
        return dict(
            items=[
                self._view(c, {"index_status": "pending", "index_error": None}) for c in chunks[:20]
            ],
            total=len(chunks),
        )

    async def run_pending(self, limit: int = 10) -> int:
        async with self.sessions() as session, session.begin():
            # A subquery inside UPDATE uses locking reads in InnoDB and can
            # deadlock with an original-ingestion retry updating document indexes.
            # Snapshot the candidates, then update only the managed rows. A retry
            # racing this snapshot still resumes through ingestion_error when ready.
            failed_ids = (
                (
                    await session.execute(
                        select(managed_documents.c.document_id)
                        .join(
                            documents,
                            (managed_documents.c.document_id == documents.c.document_id)
                            & (managed_documents.c.project_id == documents.c.project_id),
                        )
                        .where(
                            *scope_predicates(managed_documents, self.scope),
                            managed_documents.c.index_status == "waiting_ingestion",
                            documents.c.status == "failed",
                            documents.c.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if failed_ids:
                await session.execute(
                    update(managed_documents)
                    .where(
                        *scope_predicates(managed_documents, self.scope),
                        managed_documents.c.index_status == "waiting_ingestion",
                        managed_documents.c.document_id.in_(failed_ids),
                    )
                    .values(
                        index_status="ingestion_error",
                        index_error="Original document processing failed. Retry the document.",
                    )
                )
            ids = (
                (
                    await session.execute(
                        select(managed_documents.c.document_id)
                        .join(
                            documents,
                            (managed_documents.c.document_id == documents.c.document_id)
                            & (managed_documents.c.project_id == documents.c.project_id),
                        )
                        .where(
                            documents.c.deleted_at.is_(None),
                            self._active_source(),
                            *scope_predicates(managed_documents, self.scope),
                            or_(
                                managed_documents.c.index_status == "pending",
                                (
                                    managed_documents.c.index_status.in_(
                                        ["waiting_ingestion", "ingestion_error"]
                                    )
                                )
                                & (documents.c.status == "ready"),
                            ),
                        )
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        for document_id in ids:
            try:
                state = await self._ensure(document_id)
                if state["index_status"] in {"waiting_ingestion", "ingestion_error"}:
                    async with self.sessions() as session:
                        document = await self._document(session, document_id)
                    if document["status"] != "ready":
                        continue
                    chunks = generate_chunks(
                        await self._original_text(document_id, state),
                        ChunkSettings(**state["settings_json"]),
                    )
                    await self._save(
                        document_id,
                        state["version"],
                        chunks,
                        state["settings_json"],
                        initializing=True,
                    )
                await self.process_document(document_id)
            except (ChunkNotFound, ChunkConflict):
                continue
            except Exception:
                async with self.sessions() as session, session.begin():
                    await session.execute(
                        update(managed_documents)
                        .where(*self._where(managed_documents, document_id))
                        .values(
                            index_status="error",
                            index_error="Chunk processing failed. Check settings and retry.",
                        )
                    )
                    await session.execute(
                        update(documents)
                        .where(
                            *self._where(documents, document_id), documents.c.deleted_at.is_(None)
                        )
                        .values(
                            status="failed",
                            error_code="index-unavailable",
                            error_summary="Chunk processing failed. Check settings and retry.",
                        )
                    )
        return len(ids)

    async def process_document(self, document_id: str) -> None:
        # The durable pending record is the queue. Locking serializes processes and the
        # generation check prevents an older projection from becoming current.
        pending_purge: tuple[str, str] | None = None
        async with self.sessions() as session, session.begin():
            document = await self._document(session, document_id, lock=True)
            state = dict(
                (
                    await session.execute(
                        select(managed_documents)
                        .where(*self._where(managed_documents, document_id))
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if state["index_status"] == "ready":
                return
            try:
                async with session.begin_nested():
                    pending_purge = await self._index_generation(session, document, state)
            except Exception as error:
                # Safe public failure; pending immutable artifacts can be retried.
                cause: BaseException = error
                while cause.__cause__ is not None:
                    cause = cause.__cause__
                logging.getLogger(__name__).error(
                    "knowledge.managed_index_failed exception=%s cause=%s",
                    type(error).__name__,
                    type(cause).__name__,
                    extra={
                        "document_id": document_id,
                        "exception_type": type(error).__name__,
                        "cause_type": type(cause).__name__,
                    },
                )
                await session.execute(
                    update(managed_documents)
                    .where(*self._where(managed_documents, document_id))
                    .values(
                        index_status="error", index_error="Index update failed. Retry processing."
                    )
                )
                await session.execute(
                    update(documents)
                    .where(*self._where(documents, document_id))
                    .values(
                        status="failed",
                        error_code="index-unavailable",
                        error_summary="Index update failed. Retry processing.",
                    )
                )
                return
        if pending_purge is None:
            return
        # The publish already committed and released its row locks; the purge's
        # Milvus round trips run here, outside any lock, so they cannot block
        # concurrent chunk edits/deletes on this document. A failure here is
        # surfaced as an index error (consistent with other post-publish index
        # failures) rather than silently leaving superseded rows live forever;
        # the existing manual-retry path re-runs generation and, since the
        # content identity is unchanged, republishes idempotently and retries
        # the purge.
        superseded_revision_id, current_revision_id = pending_purge
        try:
            await self.index.purge_document(
                document_id,
                keep_revision_id=current_revision_id,
                fence_revision_ids=(superseded_revision_id,),
            )
        except Exception as error:
            logging.getLogger(__name__).error(
                "knowledge.managed_index_purge_failed exception=%s",
                type(error).__name__,
                extra={"document_id": document_id, "exception_type": type(error).__name__},
            )
            async with self.sessions() as session, session.begin():
                await session.execute(
                    update(managed_documents)
                    .where(*self._where(managed_documents, document_id))
                    .values(
                        index_status="error",
                        index_error="Index cleanup failed. Retry processing.",
                    )
                )
                await session.execute(
                    update(documents)
                    .where(*self._where(documents, document_id))
                    .values(
                        status="failed",
                        error_code="index-unavailable",
                        error_summary="Index cleanup failed. Retry processing.",
                    )
                )

    async def _index_generation(
        self, session: AsyncSession, document: dict[str, Any], state: dict[str, Any]
    ) -> tuple[str, str] | None:
        """Publish the managed revision; return (superseded, current) if a purge is owed."""
        from tap.modules.knowledge.ports.documents import (
            StageResult,
            StageState,
            serialize_stage_results,
        )

        document_id = document["document_id"]
        original = (
            (
                await session.execute(
                    select(revisions).where(
                        *scope_predicates(revisions, self.scope),
                        revisions.c.revision_id == state["original_revision_id"],
                    )
                )
            )
            .mappings()
            .one()
        )
        settings = ChunkSettings(**state["settings_json"])
        active: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for parent in state["chunks_json"]:
            if parent["enabled"]:
                if settings.mode == "parent_child":
                    active.extend(
                        (parent, child) for child in parent["children"] if child["enabled"]
                    )
                else:
                    active.append((parent, parent))
        if not active:
            await session.execute(
                update(managed_documents)
                .where(*self._where(managed_documents, document_id))
                .values(index_status="ready", index_error=None)
            )
            # Keep an empty selection unavailable; old index rows are unreachable.
            await session.execute(
                update(documents)
                .where(*self._where(documents, document_id))
                .values(
                    status="ready",
                    stage="ready",
                    chunk_count=0,
                    error_code=None,
                    error_summary=None,
                )
            )
            return None
        identity = json.dumps(
            {
                "anchorProfile": "managed-content-v2",
                "version": state["version"],
                "chunks": state["chunks_json"],
                "settings": state["settings_json"],
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
        source_hash = canonical_sha256(identity)
        parser_version = "managed-chunks-v1"
        revision_id = revision_id_for(DocumentId(document_id), source_hash, parser_version)
        blocks = []
        inventory = []
        drafts = []
        offset = 0
        for position, (parent, matching) in enumerate(active):
            content = parent["content"]
            if len(content) > 32768:
                raise ValueError("parent content exceeds the supported index context size")
            # Chunk ordinals/provenance are structural metadata, not semantic
            # headings shared by unrelated documents. Their identities remain in
            # block/manifest/inventory records and edited state.
            heading: tuple[str, ...] = ()
            block_id = "managed_" + matching["chunkId"]
            inventory_item = ParseInventoryItem.create(
                source_revision_id=str(revision_id),
                kind=ParseInventoryKind.PARAGRAPH,
                locator=f"managed-chunk:{matching['chunkId']}",
                status=ParseInventoryStatus.PARSED,
                artifact_digest=canonical_sha256(content.encode()),
                original_alignment_reason="managed-content-not-original-excerpt",
            )
            inventory.append(inventory_item)
            block = NormalizedBlock(
                block_id,
                BlockKind.PARAGRAPH,
                content,
                heading,
                None,
                position,
                offset,
                offset + len(content),
                inventory_item.item_id,
            )
            blocks.append(block)
            anchor = json.dumps(
                dict(
                    type="document",
                    headingPath=list(heading),
                    startOffset=offset,
                    endOffset=offset + len(content),
                    inventoryItemId=inventory_item.item_id,
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_hash = canonical_sha256(content.encode())
            drafts.append(
                ChunkDraft(
                    chunk_id_for(revision_id, anchor, content_hash),
                    logical_chunk_id_for(DocumentId(document_id), anchor),
                    DocumentId(document_id),
                    block_id,
                    content,
                    anchor,
                    source_hash,
                    content_hash,
                )
            )
            offset += len(content) + 2
        normalized = NormalizedArtifact(
            document["filename"],
            MediaType(document["media_type"]),
            source_hash,
            tuple(blocks),
            DocumentId(document_id),
            revision_id,
            parse_inventory=tuple(inventory),
            parser_config_digest=canonical_sha256(
                json.dumps(state["settings_json"], sort_keys=True).encode()
            ),
            parse_inventory_digest=parse_inventory_digest(tuple(inventory)),
        )
        normalized_locator = await self.artifacts.write_normalized(str(revision_id), normalized)
        chunks_locator = await self.artifacts.write_chunks(str(revision_id), tuple(drafts))
        embedding = await self.embeddings.embed_documents(
            tuple(matching["content"] for _, matching in active),
            model_alias=self.embedding_model_alias,
            chunk_ids=tuple(str(c.chunk_id) for c in drafts),
        )
        if (
            embedding.dimension != self.embedding_dimension
            or embedding.model_alias != self.embedding_model_alias
        ):
            raise ValueError("embedding contract mismatch")
        embeddings_locator = await self.artifacts.write_embeddings(
            str(revision_id), embedding, source_content_hash=source_hash
        )
        manifest = tuple(
            ManifestChunk(
                str(c.chunk_id),
                str(c.logical_chunk_id),
                i,
                document_id,
                c.parent_id,
                c.anchor_json,
                c.chunk_content_hash,
                self.embedding_model_alias,
                self.index_version,
            )
            for i, c in enumerate(drafts)
        )
        work = IngestionWork(
            "managed_" + uuid4().hex,
            "managed",
            JobKind.INGESTION,
            JobStage.PUBLISHING,
            document_id,
            str(revision_id),
            document["filename"],
            document["media_type"],
            source_hash,
            ArtifactLocator(original["original_blob_locator"]),
            normalized_locator,
            chunks_locator,
            embeddings_locator,
            parser_version,
            "managed-chunks-v1",
            "managed-chunks-v1",
            manifest,
            source_id=document["source_id"],
            enterprise_id=self.scope.enterprise_id,
            project_id=self.scope.project_id,
        )
        receipt = await self.index.upsert_revision(
            work, tuple(drafts), embedding, index_version=self.index_version
        )
        if (
            receipt.indexed_count != len(drafts)
            or receipt.revision_id != str(revision_id)
            or receipt.index_version != self.index_version
            or not receipt.schema_version
            or receipt.projection_digest
            != projection_digest(
                str(revision_id), receipt.schema_version, self.index_version, manifest
            )
        ):
            raise ValueError("index reconciliation failed")
        now = datetime.now(UTC).replace(tzinfo=None)
        values = dict(
            revision_id=str(revision_id),
            source_id=document["source_id"],
            document_id=document_id,
            source_content_hash=source_hash,
            original_blob_locator=str(work.original_locator),
            normalized_blob_locator=str(normalized_locator),
            chunks_blob_locator=str(chunks_locator),
            embeddings_blob_locator=str(embeddings_locator),
            parser_version=parser_version,
            chunker_version="managed-chunks-v1",
            pipeline_version="managed-chunks-v1",
            created_at=now,
            chunk_manifest_digest=chunk_manifest_digest(manifest),
            projection_digest=receipt.projection_digest,
            parse_inventory_attempt=1,
            parser_config_digest=normalized.parser_config_digest,
            parse_inventory_digest=normalized.parse_inventory_digest,
            **scope_values(self.scope),
        )
        await session.execute(insert(revisions).values(**values))
        for ordinal, inventory_row in enumerate(inventory):
            await session.execute(
                insert(inventories).values(
                    inventory_row_id="inv_" + uuid4().hex,
                    source_revision_id=str(revision_id),
                    attempt=1,
                    item_id=inventory_row.item_id,
                    ordinal=ordinal,
                    item_kind=inventory_row.kind.value,
                    locator=inventory_row.locator,
                    status=inventory_row.status.value,
                    artifact_digest=inventory_row.artifact_digest,
                    original_alignment_reason=inventory_row.original_alignment_reason,
                    original_alignment_binding_digest=original_alignment_binding_digest(
                        inventory_row,
                        attempt=1,
                        parser_digest=normalized.parser_config_digest or "",
                        inventory_digest=normalized.parse_inventory_digest or "",
                    ),
                    created_at=now,
                    **scope_values(self.scope),
                )
            )
        for item in manifest:
            await session.execute(
                insert(manifests).values(
                    chunk_id=item.chunk_id,
                    logical_chunk_id=item.logical_chunk_id,
                    revision_id=str(revision_id),
                    ordinal=item.ordinal,
                    root_id=item.root_id,
                    parent_id=item.parent_id,
                    anchor_json=json.loads(item.anchor_json),
                    chunk_content_hash=item.chunk_content_hash,
                    embedding_model_version=item.embedding_model_version,
                    index_version=item.index_version,
                    created_at=now,
                    **scope_values(self.scope),
                )
            )
        stages = tuple(StageResult(stage, StageState.COMPLETED, now) for stage in JobStage)
        await session.execute(
            insert(jobs).values(
                job_id=work.job_id,
                revision_id=str(revision_id),
                kind="ingestion",
                attempt=1,
                status="completed",
                stage="ready",
                stage_results_json=serialize_stage_results(stages),
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
                completed_at=now,
                **scope_values(self.scope),
            )
        )
        await session.execute(
            update(documents)
            .where(*self._where(documents, document_id))
            .values(
                current_revision_id=str(revision_id),
                source_content_hash=source_hash,
                status="ready",
                stage="ready",
                chunk_count=len(drafts),
                error_code=None,
                error_summary=None,
                updated_at=now,
            )
        )
        await session.execute(
            update(managed_documents)
            .where(*self._where(managed_documents, document_id))
            .values(index_status="ready", index_error=None)
        )
        await self.repository.record_revision_ready(
            session, str(revision_id), now=now, job_id=work.job_id
        )
        # The index is a rebuildable projection: once the new revision's rows are
        # live, the superseded revision must not keep the document's deleted
        # content searchable. The purge itself runs after this transaction commits
        # (see process_document) so the Milvus round trips it needs do not hold the
        # document/managed-chunk row locks other requests (chunk edits, deletes)
        # need to proceed.
        previous_revision_id = document["current_revision_id"]
        if previous_revision_id and previous_revision_id != str(revision_id):
            return previous_revision_id, str(revision_id)
        return None
