"""Mutable, strongly reconciled Athena projection over provider-light Milvus ports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from tap.modules.knowledge.adapters.milvus.transport import MilvusCollectionDescriptor
from tap.modules.knowledge.domain.documents import (
    MAX_CHUNKS_PER_DOCUMENT,
    PARSER_VERSION,
    ChunkDraft,
    DocumentId,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    logical_chunk_projection_id,
    revision_id_for,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.documents import (
    DeletionTarget,
    EmbeddingArtifact,
    IndexReceipt,
    IngestionWork,
)
from tap.modules.knowledge.ports.errors import (
    IndexFenced,
    IndexReconciliationFailed,
    IndexUnavailable,
)
from tap.modules.knowledge.ports.projection import (
    ProjectionMutationCoordinator,
    ProjectionMutationLease,
    ProjectionOwnershipReceipt,
)
from tap.operations.milvus.async_call import await_task_terminal
from tap.operations.milvus.contracts import (
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusDocProvisioner,
    MilvusDocReader,
    MilvusWriter,
)
from tap.operations.milvus.doc_schema import (
    DocCollectionMetadata,
    build_doc_collection_schema,
    doc_schema_sha256,
)

ATHENA_PHYSICAL_COLLECTION = "kb_doc_v1_athena_demo"
ATHENA_ALIAS = "kb_doc_athena_demo_active"
ATHENA_SCHEMA_VERSION = "doc-schema-v1"
ATHENA_CORPUS_VERSION = "athena-demo-v1"
ATHENA_EMBEDDING_MODEL = "athena-embedding"
ATHENA_VECTOR_DIMENSION = 1536
ATHENA_TENANT_ID = "local"
ATHENA_PROJECT_ID = "athena-demo"
ATHENA_GROUP_ID = "athena-local"
ATHENA_ENVIRONMENT = "global"
ATHENA_CLASSIFICATION_RANK = 1

_UPSERT_BATCH = 64
_DELETE_BATCH = 256
_QUERY_LIMIT = MAX_CHUNKS_PER_DOCUMENT + 1
_HASH_ID = re.compile(r"h_[0-9a-f]{64}\Z")
_LOGICAL_ID = re.compile(r"(?:h_|lc_)[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DOC_READBACK_FIELDS = (
    "chunk_id",
    "source_revision",
    "source_content_hash",
    "chunk_content_hash",
    "embedding_model_version",
    "corpus_version",
    "source_id",
    "source_type",
    "physical_collection",
)


@dataclass(frozen=True, slots=True)
class AthenaMilvusConfig:
    physical_collection: str = ATHENA_PHYSICAL_COLLECTION
    alias: str = ATHENA_ALIAS
    schema_version: str = ATHENA_SCHEMA_VERSION
    corpus_version: str = ATHENA_CORPUS_VERSION
    embedding_model: str = ATHENA_EMBEDDING_MODEL
    vector_dimension: int = ATHENA_VECTOR_DIMENSION
    tenant_id: str = ATHENA_TENANT_ID
    project_id: str = ATHENA_PROJECT_ID
    group_id: str = ATHENA_GROUP_ID
    environment: str = ATHENA_ENVIRONMENT
    classification_rank: int = ATHENA_CLASSIFICATION_RANK

    def __post_init__(self) -> None:
        if (
            self.physical_collection,
            self.alias,
            self.schema_version,
            self.corpus_version,
            self.embedding_model,
            self.vector_dimension,
            self.tenant_id,
            self.project_id,
            self.group_id,
            self.environment,
            self.classification_rank,
        ) != (
            ATHENA_PHYSICAL_COLLECTION,
            ATHENA_ALIAS,
            ATHENA_SCHEMA_VERSION,
            ATHENA_CORPUS_VERSION,
            ATHENA_EMBEDDING_MODEL,
            ATHENA_VECTOR_DIMENSION,
            ATHENA_TENANT_ID,
            ATHENA_PROJECT_ID,
            ATHENA_GROUP_ID,
            ATHENA_ENVIRONMENT,
            ATHENA_CLASSIFICATION_RANK,
        ):
            raise ValueError("Athena Milvus v1 configuration must use the closed local target")


@dataclass(frozen=True, slots=True)
class IndexTargetReceipt:
    physical_collection: str
    alias: str


@dataclass(frozen=True, slots=True)
class ReadyRevisionArtifacts:
    work: IngestionWork
    chunks: tuple[ChunkDraft, ...]
    embeddings: EmbeddingArtifact
    index_version: str


@dataclass(frozen=True, slots=True)
class RebuildReceipt:
    physical_collection: str
    alias: str
    row_count: int
    cleanup_facts: tuple[str, ...] = ()


class RebuildRejected(IndexReconciliationFailed):
    def __init__(self, cleanup_facts: tuple[str, ...]) -> None:
        self.cleanup_facts = cleanup_facts
        super().__init__("Athena index rebuild failed before activation")


class MilvusDocumentIndex:
    """A durable mutable projection with exact post-write reconciliation."""

    def __init__(
        self,
        *,
        config: AthenaMilvusConfig,
        provisioner: MilvusDocProvisioner,
        writer: MilvusWriter,
        reader: MilvusDocReader,
        coordinator: ProjectionMutationCoordinator,
    ) -> None:
        if not isinstance(config, AthenaMilvusConfig):
            raise TypeError("Athena index requires closed configuration")
        self._config = config
        self._provisioner = provisioner
        self._writer = writer
        self._reader = reader
        self._coordinator = coordinator

    async def ensure_target(self) -> IndexTargetReceipt:
        try:
            async with self._coordinator.mutation(self._config.alias) as authority:
                return await self._ensure_target_locked(authority)
        except asyncio.CancelledError:
            raise
        except IndexUnavailable:
            raise
        except Exception as error:
            raise IndexUnavailable("Athena Milvus target provisioning failed") from error

    async def upsert_revision(
        self,
        work: IngestionWork,
        chunks: tuple[ChunkDraft, ...],
        embeddings: EmbeddingArtifact,
        *,
        index_version: str,
    ) -> IndexReceipt:
        try:
            async with self._coordinator.mutation(self._config.alias) as authority:
                physical = await self._current_target_locked(authority)
                rows = self._revision_rows(physical, work, chunks, embeddings, index_version)
                await self._require_not_fenced(authority, physical, work.revision_id)
                for batch in _batches(rows, _UPSERT_BATCH):
                    await self._writer.upsert(physical, batch)
                await self._writer.flush(physical)
                if await self._is_fenced(authority, physical, work.revision_id):
                    await self._delete_ids(
                        physical,
                        tuple(str(item.chunk_id) for item in chunks),
                    )
                    raise IndexFenced("Athena revision has a durable deletion fence")
                await self._require_revision_parity(physical, work, chunks)
        except asyncio.CancelledError:
            raise
        except (IndexFenced, IndexReconciliationFailed):
            raise
        except Exception as error:
            raise IndexUnavailable("Athena Milvus upsert failed") from error
        return IndexReceipt(work.revision_id, index_version, len(chunks))

    async def fence_revision(self, target: DeletionTarget) -> None:
        try:
            async with self._coordinator.mutation(self._config.alias) as authority:
                await authority.record_fence(target.revision_id, target.document_id)
                physical = await self._current_target_locked(authority)
                row = self._fence_row(physical, target)
                await self._writer.upsert(physical, (row,))
                await self._writer.flush(physical)
                persisted = await self._reader.query_persisted_rows(
                    physical,
                    _eq("chunk_id", str(row["chunk_id"])),
                    ("chunk_id", "source_type", "source_id"),
                    2,
                )
                if persisted != (
                    {
                        "chunk_id": row["chunk_id"],
                        "source_type": "athena_fence",
                        "source_id": target.document_id,
                    },
                ):
                    raise IndexReconciliationFailed("Athena deletion fence did not persist exactly")
                await self._delete_ids(physical, target.chunk_ids)
        except asyncio.CancelledError:
            raise
        except IndexReconciliationFailed:
            raise
        except Exception as error:
            raise IndexUnavailable("Athena Milvus fence failed") from error

    async def delete_revision(self, target: DeletionTarget) -> None:
        try:
            async with self._coordinator.mutation(self._config.alias) as authority:
                physical = await self._current_target_locked(authority)
                await self._delete_ids(physical, target.chunk_ids)
                if await self._count_revision_locked(physical, target):
                    raise IndexReconciliationFailed("Athena revision delete negative probe failed")
        except asyncio.CancelledError:
            raise
        except IndexReconciliationFailed:
            raise
        except Exception as error:
            raise IndexUnavailable("Athena Milvus delete failed") from error

    async def count_revision(self, target: DeletionTarget) -> int:
        try:
            async with self._coordinator.mutation(self._config.alias) as authority:
                physical = await self._current_target_locked(authority)
                return await self._count_revision_locked(physical, target)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise IndexUnavailable("Athena Milvus count failed") from error
        raise AssertionError("unreachable")

    async def rebuild(
        self,
        records: tuple[ReadyRevisionArtifacts, ...],
    ) -> RebuildReceipt:
        if not isinstance(records, tuple):
            raise ValueError("Athena rebuild requires a closed ready-revision snapshot")
        committed_receipt: RebuildReceipt | None = None
        try:
            async with self._coordinator.mutation(self._config.alias) as authority:
                committed_receipt = await self._rebuild_locked(authority, records)
        except asyncio.CancelledError:
            if committed_receipt is not None:
                return committed_receipt
            raise
        except RebuildRejected:
            raise
        except Exception as error:
            raise RebuildRejected(("cleanup_unknown:preflight",)) from error
        if committed_receipt is None:
            raise AssertionError("Athena rebuild exited without an authoritative receipt")
        return committed_receipt

    async def close(self) -> None:
        seen: set[int] = set()
        errors: list[BaseException] = []
        for client in (
            self._reader,
            self._writer,
            self._provisioner,
            self._coordinator,
        ):
            if id(client) not in seen:
                seen.add(id(client))
                try:
                    await client.close()
                except BaseException as close_error:
                    errors.append(close_error)
        if errors:
            for recorded_error in errors:
                if isinstance(recorded_error, asyncio.CancelledError):
                    raise recorded_error
            raise errors[0]

    async def _ensure_target_locked(
        self,
        authority: ProjectionMutationLease,
    ) -> IndexTargetReceipt:
        physical = self._config.physical_collection
        alias_target = await self._provisioner.describe_alias(self._config.alias)
        if alias_target is None:
            if not await self._provisioner.collection_exists(physical):
                await self._provisioner.create_collection(physical, self._schema())
            await self._require_base_descriptor(physical)
            try:
                await self._require_descriptor(physical)
            except Exception:
                await self._provisioner.create_indexes(physical, self._schema())
                await self._require_descriptor(physical)
            await self._require_loaded(physical)
            await self._ensure_exact_grants(physical)
            try:
                await self._provisioner.create_alias(self._config.alias, physical)
            except Exception:
                if await self._provisioner.describe_alias(self._config.alias) != physical:
                    raise
            alias_target = await self._provisioner.describe_alias(self._config.alias)
        self._require_target_name(alias_target)
        assert isinstance(alias_target, str)
        if not await self._provisioner.collection_exists(alias_target):
            raise IndexUnavailable("Athena alias target does not exist")
        await self._require_descriptor(alias_target)
        await self._require_loaded(alias_target)
        await self._ensure_exact_grants(alias_target)
        await self._synchronize_authority(authority, alias_target)
        await self._reconcile_cleanup_locked(authority, alias_target, limit=1)
        return IndexTargetReceipt(alias_target, self._config.alias)

    async def _synchronize_authority(
        self,
        authority: ProjectionMutationLease,
        actual_physical: str,
    ) -> None:
        _, recorded = await authority.state()
        if recorded is None:
            _, initialized = await authority.initialize(actual_physical)
            if initialized != actual_physical:
                raise IndexUnavailable("Athena projection generation initialization conflicted")
            return
        self._require_target_name(recorded)
        if recorded != actual_physical:
            ownership = await authority.ownership(actual_physical)
            if (
                ownership is None
                or ownership.status != "building"
                or ownership.predecessor_collection != recorded
            ):
                raise IndexUnavailable("Athena alias drift has no durable ownership lineage")
            await self._revoke_exact_grants(recorded)
            await self._activate_build_resolved(authority, ownership)

    async def _current_target_locked(self, authority: ProjectionMutationLease) -> str:
        target = await self._provisioner.describe_alias(self._config.alias)
        if target is None:
            target = (await self._ensure_target_locked(authority)).physical_collection
        self._require_target_name(target)
        assert isinstance(target, str)
        await self._require_descriptor(target)
        await self._synchronize_authority(authority, target)
        await self._reconcile_cleanup_locked(authority, target, limit=1)
        return target

    async def _count_revision_locked(
        self,
        physical: str,
        target: DeletionTarget,
    ) -> int:
        rows = await self._reader.query_persisted_rows(
            physical,
            _eq("source_revision", target.revision_id),
            ("chunk_id",),
            _QUERY_LIMIT,
        )
        if len(rows) > MAX_CHUNKS_PER_DOCUMENT:
            raise IndexReconciliationFailed("Athena revision exceeds the closed chunk bound")
        return len(rows)

    async def _reconcile_cleanup_locked(
        self,
        authority: ProjectionMutationLease,
        current: str,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        facts: list[str] = []
        for ownership in await authority.owned_cleanup(limit):
            physical = ownership.physical_collection
            self._require_target_name(physical)
            if physical == current:
                raise IndexUnavailable("Athena owned cleanup unexpectedly targets active alias")
            if not await authority.verify_cleanup(ownership):
                raise IndexUnavailable("Athena projection cleanup ownership changed")
            if await self._provisioner.collection_aliases(physical):
                facts.append(f"cleanup_still_aliased:{physical}")
                continue
            if await self._provisioner.collection_exists(physical):
                await self._provisioner.revoke_collection(physical, "tap_reader")
                await self._provisioner.revoke_collection(physical, "tap_writer")
                await self._provisioner.drop_collection(physical)
            await authority.complete_owned_cleanup(ownership)
            facts.append(f"dropped_owned_physical:{physical}")
        return tuple(facts)

    async def _rebuild_locked(
        self,
        authority: ProjectionMutationLease,
        records: tuple[ReadyRevisionArtifacts, ...],
    ) -> RebuildReceipt:
        old = await self._current_target_locked(authority)
        fresh = f"{self._config.physical_collection}_{secrets.token_hex(6)}"
        operation_id = secrets.token_hex(16)
        created = False
        ownership = await authority.reserve_build(fresh, old, operation_id)
        try:
            await self._provisioner.create_collection(fresh, self._schema())
            created = True
            await self._require_base_descriptor(fresh)
            await self._provisioner.create_indexes(fresh, self._schema())
            await self._require_descriptor(fresh)
            await self._require_loaded(fresh)

            old_fence_rows = await self._reader.query_persisted_rows(
                old,
                'source_type == "athena_fence"',
                ("source_revision", "source_id", "source_type"),
                _QUERY_LIMIT,
            )
            if len(old_fence_rows) >= _QUERY_LIMIT:
                raise IndexReconciliationFailed("Athena durable fence snapshot exceeds its bound")
            for row in old_fence_rows:
                revision = row.get("source_revision")
                document_id = row.get("source_id")
                if (
                    not isinstance(revision, str)
                    or not revision.startswith("fence:")
                    or not isinstance(document_id, str)
                ):
                    raise IndexReconciliationFailed("Athena deletion fence row is malformed")
                await authority.record_fence(revision.removeprefix("fence:"), document_id)

            durable_fences = await authority.fences(_QUERY_LIMIT)
            if len(durable_fences) >= _QUERY_LIMIT:
                raise IndexReconciliationFailed("Athena durable fence ledger exceeds its bound")
            fence_rows = tuple(
                self._fence_row(
                    fresh,
                    DeletionTarget(document_id, revision_id, (), ()),
                )
                for revision_id, document_id in durable_fences
            )
            for fence_batch in _batches(fence_rows, _UPSERT_BATCH):
                await self._writer.upsert(fresh, fence_batch)

            active_records: list[ReadyRevisionArtifacts] = []
            expected_ids: set[str] = set()
            for record in records:
                if await authority.is_fenced(record.work.revision_id):
                    continue
                rows = self._revision_rows(
                    fresh,
                    record.work,
                    record.chunks,
                    record.embeddings,
                    record.index_version,
                )
                row_ids = {str(row["chunk_id"]) for row in rows}
                if expected_ids & row_ids:
                    raise ValueError("Athena rebuild contains duplicate chunk identities")
                expected_ids.update(row_ids)
                active_records.append(record)
                for revision_batch in _batches(rows, _UPSERT_BATCH):
                    await self._writer.upsert(fresh, revision_batch)
            await self._writer.flush(fresh)
            await self._ensure_exact_grants(fresh)
            await self._require_fence_parity(fresh, fence_rows)
            for record in active_records:
                await self._require_revision_parity(fresh, record.work, record.chunks)

            try:
                await self._provisioner.alter_alias(self._config.alias, fresh)
            except Exception:
                if await self._provisioner.describe_alias(self._config.alias) != fresh:
                    raise
            if await self._provisioner.describe_alias(self._config.alias) != fresh:
                raise IndexReconciliationFailed("Athena alias switch did not persist")

            old_ownership = await authority.ownership(old)
            await self._revoke_exact_grants(old)
            await self._activate_build_resolved(authority, ownership)
        except asyncio.CancelledError as cancellation:
            cleanup = await self._settle_rebuild_rollback(authority, old, ownership, created)
            cancellation.add_note("Athena rebuild cleanup facts: " + ",".join(cleanup))
            raise
        except Exception as error:
            cleanup = await self._settle_rebuild_rollback(authority, old, ownership, created)
            raise RebuildRejected(cleanup) from error

        cleanup_fact = (
            f"cleanup_queued:{old}"
            if old_ownership is not None and old_ownership.status == "active"
            else f"retained_legacy_physical:{old}"
        )
        return RebuildReceipt(
            physical_collection=fresh,
            alias=self._config.alias,
            row_count=len(expected_ids),
            cleanup_facts=(cleanup_fact,),
        )

    async def _activate_build_resolved(
        self,
        authority: ProjectionMutationLease,
        ownership: ProjectionOwnershipReceipt,
    ) -> None:
        try:
            await authority.activate_build(ownership)
            return
        except BaseException:
            _, recorded = await authority.state()
            actual = await authority.ownership(ownership.physical_collection)
            alias_target = await self._provisioner.describe_alias(self._config.alias)
            if (
                recorded == ownership.physical_collection
                and actual is not None
                and actual.physical_collection == ownership.physical_collection
                and actual.operation_id == ownership.operation_id
                and actual.predecessor_collection == ownership.predecessor_collection
                and actual.status == "active"
                and alias_target == ownership.physical_collection
            ):
                return
            raise

    def _schema(self) -> dict[str, object]:
        return build_doc_collection_schema(
            DocCollectionMetadata(
                schema_version=self._config.schema_version,
                schema_sha256=doc_schema_sha256(),
                corpus_version=self._config.corpus_version,
                embedding_model_version=self._config.embedding_model,
                vector_dimension=self._config.vector_dimension,
            )
        )

    def _require_target_name(self, target: object) -> None:
        if not isinstance(target, str) or not (
            target == self._config.physical_collection
            or (
                target.startswith(self._config.physical_collection + "_")
                and len(target) == len(self._config.physical_collection) + 13
                and all(character in "0123456789abcdef" for character in target[-12:])
            )
        ):
            raise IndexUnavailable("Athena alias targets an untrusted physical collection")

    async def _require_descriptor(self, physical: str) -> None:
        descriptor = await self._provisioner.describe_collection(physical)
        self._require_descriptor_value(descriptor)

    async def _require_base_descriptor(self, physical: str) -> None:
        descriptor = await self._provisioner.describe_collection_schema(
            physical,
            self._schema(),
        )
        self._require_descriptor_value(descriptor)

    def _require_descriptor_value(self, descriptor: object) -> None:
        expected = (
            SourceFamily.DOC,
            self._config.schema_version,
            doc_schema_sha256(),
            self._config.corpus_version,
            self._config.embedding_model,
            self._config.vector_dimension,
            False,
            "Strong",
        )
        actual = (
            getattr(descriptor, "family", None),
            getattr(descriptor, "schema_version", None),
            getattr(descriptor, "schema_sha256", None),
            getattr(descriptor, "corpus_version", None),
            getattr(descriptor, "embedding_model_version", None),
            getattr(descriptor, "vector_dimension", None),
            getattr(descriptor, "dynamic_fields_enabled", None),
            getattr(descriptor, "consistency_level", None),
        )
        if not isinstance(descriptor, MilvusCollectionDescriptor) or actual != expected:
            raise IndexUnavailable("Athena Milvus collection metadata does not match")

    async def _ensure_exact_grants(self, physical: str) -> None:
        for role, expected in (
            ("tap_reader", READER_TARGET_PRIVILEGES),
            ("tap_writer", WRITER_PRIVILEGES),
        ):
            await self._provisioner.grant_collection(physical, role)
            grants = await self._provisioner.collection_grants(physical, role)
            privileges = frozenset(item.privilege for item in grants)
            if privileges != expected:
                raise IndexUnavailable("Athena Milvus scoped privileges are not exact")

    async def _revoke_exact_grants(self, physical: str) -> None:
        for role in ("tap_reader", "tap_writer"):
            await self._provisioner.revoke_collection(physical, role)
            if await self._provisioner.collection_grants(physical, role):
                raise IndexUnavailable("Athena retired Milvus scoped privileges remain")

    async def _require_loaded(self, physical: str) -> None:
        await self._provisioner.ensure_loaded(physical)
        if not await self._provisioner.is_loaded(physical):
            raise IndexUnavailable("Athena Milvus collection is not loaded")

    def _revision_rows(
        self,
        physical: str,
        work: IngestionWork,
        chunks: tuple[ChunkDraft, ...],
        embeddings: EmbeddingArtifact,
        index_version: str,
    ) -> tuple[Mapping[str, object], ...]:
        if (
            not isinstance(work, IngestionWork)
            or not isinstance(chunks, tuple)
            or not 1 <= len(chunks) <= MAX_CHUNKS_PER_DOCUMENT
            or not isinstance(embeddings, EmbeddingArtifact)
            or embeddings.model_alias != self._config.embedding_model
            or embeddings.dimension != self._config.vector_dimension
            or len(embeddings.vectors) != len(chunks)
            or embeddings.chunk_ids != tuple(str(chunk.chunk_id) for chunk in chunks)
            or not isinstance(index_version, str)
            or not 1 <= len(index_version) <= 256
            or not 1 <= len(work.document_id) <= 1_024
            or not 1 <= len(work.revision_id) <= 512
            or not 1 <= len(work.filename) <= 1_024
        ):
            raise ValueError("Athena revision artifacts do not match the closed index target")
        try:
            canonical_revision = revision_id_for(
                DocumentId(work.document_id),
                work.source_content_hash,
                work.parser_version,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Athena revision provenance is invalid") from error
        if work.parser_version != PARSER_VERSION or str(canonical_revision) != work.revision_id:
            raise ValueError("Athena revision provenance is inconsistent")
        if len({str(chunk.chunk_id) for chunk in chunks}) != len(chunks):
            raise ValueError("Athena revision chunk identities must be unique")
        rows: list[Mapping[str, object]] = []
        for chunk, vector in zip(chunks, embeddings.vectors, strict=True):
            if (
                chunk.source_content_hash != work.source_content_hash
                or str(chunk.root_id) != work.document_id
                or _HASH_ID.fullmatch(str(chunk.chunk_id)) is None
                or _LOGICAL_ID.fullmatch(str(chunk.logical_chunk_id)) is None
                or _DIGEST.fullmatch(chunk.source_content_hash) is None
                or _DIGEST.fullmatch(chunk.chunk_content_hash) is None
                or canonical_sha256(chunk.content.encode("utf-8")) != chunk.chunk_content_hash
                or str(logical_chunk_id_for(chunk.root_id, chunk.anchor_json))
                != str(chunk.logical_chunk_id)
                or str(
                    chunk_id_for(
                        RevisionId(work.revision_id),
                        chunk.anchor_json,
                        chunk.chunk_content_hash,
                    )
                )
                != str(chunk.chunk_id)
                or not 1 <= len(chunk.content) <= 32_768
                or any(
                    ord(character) < 0x20 and character not in "\t\n\r"
                    for character in chunk.content
                )
                or (
                    chunk.parent_id is not None
                    and (not isinstance(chunk.parent_id, str) or not chunk.parent_id)
                )
            ):
                raise ValueError("Athena chunk provenance does not match its revision")
            anchor = json.loads(chunk.anchor_json)
            if (
                not isinstance(anchor, dict)
                or json.dumps(anchor, sort_keys=True, separators=(",", ":")) != chunk.anchor_json
                or anchor.get("type") != "document"
            ):
                raise ValueError("Athena chunk anchor must be canonical JSON")
            rows.append(
                {
                    "chunk_id": str(chunk.chunk_id),
                    "logical_chunk_id": logical_chunk_projection_id(chunk.logical_chunk_id),
                    "root_id": _projection_id("root", str(chunk.root_id)),
                    "parent_id": (
                        None
                        if chunk.parent_id is None
                        else _projection_id("parent", chunk.parent_id)
                    ),
                    "title": work.filename,
                    "content": chunk.content,
                    "content_role": "source",
                    "tenant_id": self._config.tenant_id,
                    "project_id": self._config.project_id,
                    "allowed_group_ids": [self._config.group_id],
                    "classification_rank": self._config.classification_rank,
                    "environment": self._config.environment,
                    "deleted": False,
                    "index_family": "doc",
                    "physical_collection": physical,
                    "corpus_version": self._config.corpus_version,
                    "schema_version": self._config.schema_version,
                    "embedding_model_version": self._config.embedding_model,
                    "source_id": work.document_id,
                    "source_type": "doc",
                    "revision_kind": "blob_version",
                    "source_revision": work.revision_id,
                    "source_content_hash": work.source_content_hash,
                    "chunk_content_hash": chunk.chunk_content_hash,
                    "anchor_json": chunk.anchor_json,
                    "derived_from_chunk_ids": [],
                    "dense_vector": list(vector),
                }
            )
        return tuple(rows)

    def _fence_row(self, physical: str, target: DeletionTarget) -> Mapping[str, object]:
        digest = hashlib.sha256(target.revision_id.encode("utf-8")).hexdigest()
        row_id = "h_" + digest
        content_hash = "sha256:" + hashlib.sha256(b"athena deletion fence").hexdigest()
        return {
            "chunk_id": row_id,
            "logical_chunk_id": row_id,
            "root_id": target.document_id,
            "parent_id": None,
            "title": None,
            "content": "athena deletion fence",
            "content_role": "source",
            "tenant_id": self._config.tenant_id,
            "project_id": self._config.project_id,
            "allowed_group_ids": [self._config.group_id],
            "classification_rank": self._config.classification_rank,
            "environment": self._config.environment,
            "deleted": True,
            "index_family": "doc",
            "physical_collection": physical,
            "corpus_version": self._config.corpus_version,
            "schema_version": self._config.schema_version,
            "embedding_model_version": self._config.embedding_model,
            "source_id": target.document_id,
            "source_type": "athena_fence",
            "revision_kind": "deletion_fence",
            "source_revision": "fence:" + target.revision_id,
            "source_content_hash": content_hash,
            "chunk_content_hash": content_hash,
            "anchor_json": "{}",
            "derived_from_chunk_ids": [],
            "dense_vector": [0.0] * self._config.vector_dimension,
        }

    async def _require_not_fenced(
        self,
        authority: ProjectionMutationLease,
        physical: str,
        revision_id: str,
    ) -> None:
        if await self._is_fenced(authority, physical, revision_id):
            raise IndexFenced("Athena revision has a durable deletion fence")

    async def _is_fenced(
        self,
        authority: ProjectionMutationLease,
        physical: str,
        revision_id: str,
    ) -> bool:
        if await authority.is_fenced(revision_id):
            return True
        fence_id = "h_" + hashlib.sha256(revision_id.encode("utf-8")).hexdigest()
        rows = await self._reader.query_persisted_rows(
            physical,
            _eq("chunk_id", fence_id),
            ("chunk_id", "source_type"),
            2,
        )
        if not rows:
            return False
        if rows != ({"chunk_id": fence_id, "source_type": "athena_fence"},):
            raise IndexReconciliationFailed("Athena deletion fence row is malformed")
        return True

    async def _delete_ids(self, physical: str, chunk_ids: tuple[str, ...]) -> None:
        if not isinstance(chunk_ids, tuple) or len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Athena delete requires unique immutable chunk IDs")
        for batch in _batches(chunk_ids, _DELETE_BATCH):
            await self._writer.delete(physical, batch)
        await self._writer.flush(physical)

    async def _require_revision_parity(
        self,
        physical: str,
        work: IngestionWork,
        chunks: tuple[ChunkDraft, ...],
    ) -> None:
        rows = await self._reader.query_persisted_rows(
            physical,
            _eq("source_revision", work.revision_id),
            _DOC_READBACK_FIELDS,
            _QUERY_LIMIT,
        )
        expected = {
            str(chunk.chunk_id): (
                work.source_content_hash,
                chunk.chunk_content_hash,
            )
            for chunk in chunks
        }
        actual: dict[str, tuple[object, object]] = {}
        for row in rows:
            chunk_id = row.get("chunk_id")
            if not isinstance(chunk_id, str) or chunk_id in actual:
                raise IndexReconciliationFailed("Athena readback contains duplicate rows")
            if (
                row.get("source_revision") != work.revision_id
                or row.get("embedding_model_version") != self._config.embedding_model
                or row.get("corpus_version") != self._config.corpus_version
                or row.get("source_id") != work.document_id
                or row.get("source_type") != "doc"
                or row.get("physical_collection") != physical
            ):
                raise IndexReconciliationFailed("Athena readback metadata does not match")
            actual[chunk_id] = (
                row.get("source_content_hash"),
                row.get("chunk_content_hash"),
            )
        if actual != expected:
            raise IndexReconciliationFailed("Athena readback chunk/hash parity failed")

    async def _require_fence_parity(
        self,
        physical: str,
        old_rows: tuple[Mapping[str, object], ...],
    ) -> None:
        output_fields = (
            "chunk_id",
            "source_id",
            "source_type",
            "source_revision",
            "physical_collection",
        )
        rows = await self._reader.query_persisted_rows(
            physical,
            'source_type == "athena_fence"',
            output_fields,
            _QUERY_LIMIT,
        )
        expected = {
            (
                row.get("chunk_id"),
                row.get("source_id"),
                row.get("source_revision"),
                physical,
            )
            for row in old_rows
            if row.get("source_type") == "athena_fence"
        }
        actual = {
            (
                row.get("chunk_id"),
                row.get("source_id"),
                row.get("source_revision"),
                row.get("physical_collection"),
            )
            for row in rows
            if row.get("source_type") == "athena_fence"
        }
        if len(actual) != len(rows) or actual != expected:
            raise IndexReconciliationFailed("Athena rebuild fence parity failed")

    async def _settle_rebuild_rollback(
        self,
        authority: ProjectionMutationLease,
        old: str,
        ownership: ProjectionOwnershipReceipt,
        created: bool,
    ) -> tuple[str, ...]:
        task = asyncio.create_task(self._rebuild_rollback(authority, old, ownership, created))
        outcome = await await_task_terminal(task)
        if outcome.error is not None or outcome.value is None:
            return (f"cleanup_unknown:{ownership.physical_collection}",)
        return outcome.value

    async def _rebuild_rollback(
        self,
        authority: ProjectionMutationLease,
        old: str,
        ownership: ProjectionOwnershipReceipt,
        created: bool,
    ) -> tuple[str, ...]:
        fresh = ownership.physical_collection
        facts: list[str] = []
        alias_target: str | None = None
        try:
            alias_target = await self._provisioner.describe_alias(self._config.alias)
        except Exception:
            facts.append(f"alias_state_unknown:{self._config.alias}")
        if alias_target == fresh:
            try:
                await self._ensure_exact_grants(old)
                try:
                    await self._provisioner.alter_alias(self._config.alias, old)
                except Exception:
                    if await self._provisioner.describe_alias(self._config.alias) != old:
                        raise
                if await self._provisioner.describe_alias(self._config.alias) != old:
                    raise IndexReconciliationFailed("Athena old alias restoration did not persist")
                alias_target = old
            except Exception:
                facts.append(f"alias_restore_failed:{old}")
        elif alias_target == old:
            try:
                await self._ensure_exact_grants(old)
            except Exception:
                facts.append(f"old_grants_restore_failed:{old}")

        current_ownership = await authority.ownership(fresh)
        if current_ownership == ownership:
            try:
                await authority.abandon_build(ownership)
                current_ownership = await authority.ownership(fresh)
            except Exception:
                facts.append(f"ownership_abandon_failed:{fresh}")

        if created and alias_target != fresh and current_ownership is not None:
            try:
                if not await authority.verify_cleanup(current_ownership):
                    raise IndexReconciliationFailed("Athena owned partial cleanup receipt changed")
                await self._provisioner.revoke_collection(fresh, "tap_reader")
                await self._provisioner.revoke_collection(fresh, "tap_writer")
                if await self._provisioner.collection_aliases(fresh):
                    raise IndexReconciliationFailed(
                        "Athena owned partial collection is still aliased"
                    )
                if await self._provisioner.collection_exists(fresh):
                    await self._provisioner.drop_collection(fresh)
                await authority.complete_owned_cleanup(current_ownership)
                facts.append(f"dropped_owned_physical:{fresh}")
            except Exception:
                facts.append(f"cleanup_pending:{fresh}")
        elif created:
            facts.append(f"cleanup_pending:{fresh}")
        elif current_ownership is not None and await authority.verify_cleanup(current_ownership):
            await authority.complete_owned_cleanup(current_ownership)
        return tuple(facts)


def _eq(field: str, value: str) -> str:
    return f"{field} == {json.dumps(value, ensure_ascii=False)}"


def _projection_id(kind: str, value: str) -> str:
    if _HASH_ID.fullmatch(value) is not None:
        return value
    if kind == "logical" and value.startswith("lc_") and _LOGICAL_ID.fullmatch(value):
        return "h_" + value.removeprefix("lc_")
    return "h_" + hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()


def _batches[T](items: tuple[T, ...], size: int) -> tuple[tuple[T, ...], ...]:
    return tuple(items[offset : offset + size] for offset in range(0, len(items), size))


__all__ = [
    "ATHENA_ALIAS",
    "ATHENA_CORPUS_VERSION",
    "ATHENA_EMBEDDING_MODEL",
    "ATHENA_PHYSICAL_COLLECTION",
    "ATHENA_SCHEMA_VERSION",
    "AthenaMilvusConfig",
    "IndexFenced",
    "IndexTargetReceipt",
    "MilvusDocumentIndex",
    "ReadyRevisionArtifacts",
    "RebuildReceipt",
    "RebuildRejected",
]
