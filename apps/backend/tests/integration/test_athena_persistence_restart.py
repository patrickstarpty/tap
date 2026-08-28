from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import stat
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from azure.core.exceptions import ResourceNotFoundError
from pymilvus.decorators import _log_rpc_error  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tap.contracts.http import (
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
    RetrievalSearchRequest,
)
from tap.entrypoints.athena_runtime import (
    AthenaSettings,
    OwnedResources,
    _create_blob,
    _create_database,
    _create_search,
    create_api_runtime,
)
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.knowledge.adapters.blob_artifacts import (
    ARTIFACTS_CONTAINER,
    ORIGINALS_CONTAINER,
    AzureBlobArtifactStore,
)
from tap.modules.knowledge.adapters.milvus.targets import bind_target
from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusQueryRequest,
    MilvusReader,
)
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_chunk_manifest,
    knowledge_document,
    knowledge_document_revision,
    knowledge_ingestion_job,
)
from tap.modules.knowledge.domain.documents import (
    CHUNKER_VERSION,
    PARSER_VERSION,
    LogicalChunkId,
    canonical_sha256,
    logical_chunk_projection_id,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.operations.milvus.client import suppress_pymilvus_rpc_logging

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RUN_ID = re.compile(r"athena-[a-f0-9]{16}\Z")
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_MAX_STATE_BYTES = 64 * 1024
_OUTPUT_FIELDS = (
    "chunk_id",
    "logical_chunk_id",
    "root_id",
    "parent_id",
    "content_role",
    "index_family",
    "schema_version",
    "corpus_version",
    "embedding_model_version",
    "source_id",
    "source_type",
    "revision_kind",
    "source_revision",
    "source_content_hash",
    "chunk_content_hash",
    "anchor_json",
    "derived_from_chunk_ids",
)


def _emit_provider_rpc_error(details: str) -> None:
    try:
        raise RuntimeError(details)
    except RuntimeError:
        _log_rpc_error("synthetic_call", "RPC error", details, time.monotonic())


class VerificationFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class DocumentState:
    document_id: str
    job_id: str
    revision_id: str
    source_content_hash: str


@dataclass(frozen=True, slots=True, repr=False)
class CitationState:
    anchor_hash: str
    chunk_content_hash: str
    chunk_id: str
    citation_id: str
    document_id: str
    revision_id: str
    quote_hash: str
    source_content_hash: str


@dataclass(frozen=True, slots=True, repr=False)
class JourneyState:
    citation: CitationState
    deleted: DocumentState
    injection: DocumentState
    other: DocumentState
    policy: DocumentState
    recovered: tuple[DocumentState, DocumentState, DocumentState]
    reference: DocumentState
    run_id: str

    @property
    def survivors(self) -> tuple[DocumentState, ...]:
        return (
            self.policy,
            self.reference,
            self.other,
            self.injection,
            *self.recovered,
        )


@dataclass(frozen=True, slots=True, repr=False)
class ManifestEvidence:
    anchor_json: str
    chunk_content_hash: str
    chunk_id: str
    logical_chunk_id: str
    ordinal: int
    parent_id: str | None
    root_id: str


@dataclass(frozen=True, slots=True, repr=False)
class RevisionEvidence:
    locators: tuple[ArtifactLocator, ArtifactLocator, ArtifactLocator, ArtifactLocator]
    manifest: Mapping[str, ManifestEvidence]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise VerificationFailure(code)


def _exact_mapping(value: object, keys: set[str], code: str) -> Mapping[str, object]:
    _require(isinstance(value, Mapping) and set(value) == keys, code)
    return cast(Mapping[str, object], value)


def _identity(value: object, code: str) -> str:
    _require(isinstance(value, str) and _IDENTITY.fullmatch(value) is not None, code)
    return cast(str, value)


def _digest(value: object, code: str) -> str:
    _require(isinstance(value, str) and _DIGEST.fullmatch(value) is not None, code)
    return cast(str, value)


def _document_state(value: object) -> DocumentState:
    item = _exact_mapping(
        value,
        {"documentId", "jobId", "revisionId", "sourceContentHash"},
        "state-document-shape",
    )
    return DocumentState(
        document_id=_identity(item["documentId"], "state-document-id"),
        job_id=_identity(item["jobId"], "state-job-id"),
        revision_id=_identity(item["revisionId"], "state-revision-id"),
        source_content_hash=_digest(item["sourceContentHash"], "state-source-hash"),
    )


def _citation_state(value: object) -> CitationState:
    item = _exact_mapping(
        value,
        {
            "anchorHash",
            "chunkContentHash",
            "chunkId",
            "citationId",
            "documentId",
            "revisionId",
            "quoteHash",
            "sourceContentHash",
        },
        "state-citation-shape",
    )
    return CitationState(
        anchor_hash=_digest(item["anchorHash"], "state-anchor-hash"),
        chunk_content_hash=_digest(item["chunkContentHash"], "state-chunk-hash"),
        chunk_id=_identity(item["chunkId"], "state-chunk-id"),
        citation_id=_identity(item["citationId"], "state-citation-id"),
        document_id=_identity(item["documentId"], "state-citation-document"),
        revision_id=_identity(item["revisionId"], "state-citation-revision"),
        quote_hash=_digest(item["quoteHash"], "state-quote-hash"),
        source_content_hash=_digest(item["sourceContentHash"], "state-citation-source-hash"),
    )


def _load_state(path_value: str | None) -> JourneyState:
    _require(isinstance(path_value, str) and bool(path_value), "state-path")
    path = Path(cast(str, path_value))
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "state-file-kind")
    _require(0 < info.st_size <= _MAX_STATE_BYTES, "state-file-size")
    _require(info.st_mode & 0o077 == 0, "state-file-mode")
    raw = path.read_bytes()
    _require(len(raw) == info.st_size, "state-file-read")
    value = json.loads(raw)
    item = _exact_mapping(
        value,
        {
            "citation",
            "deleted",
            "injection",
            "other",
            "policy",
            "recovered",
            "reference",
            "runId",
            "schemaVersion",
        },
        "state-shape",
    )
    _require(item["schemaVersion"] == 1, "state-version")
    run_id = item["runId"]
    _require(isinstance(run_id, str) and _RUN_ID.fullmatch(run_id) is not None, "state-run")
    recovered_value = item["recovered"]
    if not isinstance(recovered_value, list) or len(recovered_value) != 3:
        raise VerificationFailure("state-recovered")
    recovered = tuple(_document_state(value) for value in recovered_value)
    _require(len(recovered) == 3, "state-recovered-count")
    state = JourneyState(
        citation=_citation_state(item["citation"]),
        deleted=_document_state(item["deleted"]),
        injection=_document_state(item["injection"]),
        other=_document_state(item["other"]),
        policy=_document_state(item["policy"]),
        recovered=cast(tuple[DocumentState, DocumentState, DocumentState], recovered),
        reference=_document_state(item["reference"]),
        run_id=cast(str, run_id),
    )
    survivor_ids = tuple(document.document_id for document in state.survivors)
    _require(len(set(survivor_ids)) == len(survivor_ids), "state-survivor-identity")
    _require(state.deleted.document_id not in survivor_ids, "state-deleted-identity")
    _require(
        state.citation.document_id == state.policy.document_id
        and state.citation.revision_id == state.policy.revision_id
        and state.citation.source_content_hash == state.policy.source_content_hash,
        "state-citation-binding",
    )
    return state


async def _one(connection: AsyncConnection, statement: Any, code: str) -> RowMapping:
    rows = (await connection.execute(statement)).mappings().all()
    _require(len(rows) == 1, code)
    return rows[0]


def _expected_locators(
    document: DocumentState, settings: AthenaSettings
) -> tuple[ArtifactLocator, ArtifactLocator, ArtifactLocator, ArtifactLocator]:
    revision = document.revision_id
    return (
        ArtifactLocator(
            f"{ORIGINALS_CONTAINER}/revisions/{revision}/"
            f"{document.source_content_hash.removeprefix('sha256:')}"
        ),
        ArtifactLocator(f"{ARTIFACTS_CONTAINER}/revisions/{revision}/normalized-v1.json"),
        ArtifactLocator(f"{ARTIFACTS_CONTAINER}/revisions/{revision}/chunks-v1.jsonl.gz"),
        ArtifactLocator(
            f"{ARTIFACTS_CONTAINER}/revisions/{revision}/embeddings/"
            f"{settings.embedding_alias}/{settings.embedding_dimension}-v1.jsonl.gz"
        ),
    )


def _require_revision_binding(
    revision: Mapping[Any, Any],
    document: DocumentState,
    locators: tuple[ArtifactLocator, ArtifactLocator, ArtifactLocator, ArtifactLocator],
    pipeline_version: str,
) -> None:
    _require(
        revision["document_id"] == document.document_id
        and revision["source_content_hash"] == document.source_content_hash
        and revision["parser_version"] == PARSER_VERSION
        and revision["chunker_version"] == CHUNKER_VERSION
        and revision["pipeline_version"] == pipeline_version
        and tuple(
            revision[name]
            for name in (
                "original_blob_locator",
                "normalized_blob_locator",
                "chunks_blob_locator",
                "embeddings_blob_locator",
            )
        )
        == tuple(locators),
        "mysql-revision-binding",
    )


async def _verify_database(
    engine: AsyncEngine,
    settings: AthenaSettings,
    state: JourneyState,
) -> Mapping[str, RevisionEvidence]:
    evidence: dict[str, RevisionEvidence] = {}
    async with engine.connect() as connection:
        for document in (*state.survivors, state.deleted):
            row = await _one(
                connection,
                select(knowledge_document).where(
                    knowledge_document.c.document_id == document.document_id
                ),
                "mysql-document",
            )
            _require(
                row["document_id"] == document.document_id
                and row["current_revision_id"] == document.revision_id
                and row["source_content_hash"] == document.source_content_hash,
                "mysql-document-binding",
            )
            is_deleted = document.document_id == state.deleted.document_id
            if is_deleted:
                _require(
                    row["status"] == "deleting"
                    and row["deleted_at"] is not None
                    and row["dedupe_key"] is None,
                    "mysql-deleted-state",
                )
            else:
                _require(
                    row["status"] == "ready"
                    and row["deleted_at"] is None
                    and isinstance(row["dedupe_key"], str)
                    and type(row["chunk_count"]) is int
                    and 1 <= row["chunk_count"] < 50,
                    "mysql-survivor-state",
                )

            revision = await _one(
                connection,
                select(knowledge_document_revision).where(
                    knowledge_document_revision.c.revision_id == document.revision_id
                ),
                "mysql-revision",
            )
            locators = _expected_locators(document, settings)
            _require_revision_binding(
                revision,
                document,
                locators,
                settings.pipeline_version,
            )

            ingestion_job = await _one(
                connection,
                select(knowledge_ingestion_job).where(
                    knowledge_ingestion_job.c.job_id == document.job_id
                ),
                "mysql-ingestion-job",
            )
            _require(
                ingestion_job["revision_id"] == document.revision_id
                and ingestion_job["kind"] == "ingestion"
                and ingestion_job["status"] == "completed",
                "mysql-ingestion-job-binding",
            )
            revision_ids = (
                (
                    await connection.execute(
                        select(knowledge_document_revision.c.revision_id).where(
                            knowledge_document_revision.c.document_id == document.document_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            _require(
                revision_ids == [document.revision_id],
                "mysql-document-revision-inventory",
            )
            revision_jobs = (
                (
                    await connection.execute(
                        select(
                            knowledge_ingestion_job.c.job_id,
                            knowledge_ingestion_job.c.kind,
                            knowledge_ingestion_job.c.status,
                        ).where(knowledge_ingestion_job.c.revision_id == document.revision_id)
                    )
                )
                .mappings()
                .all()
            )
            ingestion_jobs = [job for job in revision_jobs if job["kind"] == "ingestion"]
            deletion_inventory = [job for job in revision_jobs if job["kind"] == "deletion"]
            _require(
                len(ingestion_jobs) == 1
                and ingestion_jobs[0]["job_id"] == document.job_id
                and ingestion_jobs[0]["status"] == "completed",
                "mysql-ingestion-job-inventory",
            )
            if is_deleted:
                _require(
                    len(revision_jobs) == 2
                    and len(deletion_inventory) == 1
                    and deletion_inventory[0]["status"] == "completed",
                    "mysql-deletion-job-inventory",
                )
            else:
                _require(
                    len(revision_jobs) == 1 and not deletion_inventory,
                    "mysql-survivor-job-inventory",
                )

            manifests = (
                (
                    await connection.execute(
                        select(knowledge_chunk_manifest)
                        .where(knowledge_chunk_manifest.c.revision_id == document.revision_id)
                        .order_by(knowledge_chunk_manifest.c.ordinal)
                    )
                )
                .mappings()
                .all()
            )
            if is_deleted:
                _require(not manifests, "mysql-deleted-manifest")
            else:
                _require(len(manifests) == row["chunk_count"], "mysql-manifest-count")
            manifest_by_id: dict[str, ManifestEvidence] = {}
            for ordinal, manifest in enumerate(manifests):
                anchor = manifest["anchor_json"]
                logical_chunk_id = manifest["logical_chunk_id"]
                parent_id = manifest["parent_id"]
                _require(
                    manifest["ordinal"] == ordinal
                    and manifest["root_id"] == document.document_id
                    and manifest["embedding_model_version"] == settings.embedding_alias
                    and manifest["index_version"] == settings.index_version
                    and isinstance(manifest["chunk_id"], str)
                    and isinstance(manifest["chunk_content_hash"], str)
                    and _DIGEST.fullmatch(manifest["chunk_content_hash"]) is not None
                    and isinstance(logical_chunk_id, str)
                    and re.fullmatch(r"lc_[a-f0-9]{64}", logical_chunk_id) is not None
                    and (parent_id is None or isinstance(parent_id, str) and bool(parent_id))
                    and isinstance(anchor, Mapping),
                    "mysql-manifest-binding",
                )
                anchor_json = json.dumps(
                    anchor,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                chunk_id = cast(str, manifest["chunk_id"])
                manifest_by_id[chunk_id] = ManifestEvidence(
                    anchor_json=anchor_json,
                    chunk_content_hash=cast(str, manifest["chunk_content_hash"]),
                    chunk_id=chunk_id,
                    logical_chunk_id=cast(str, logical_chunk_id),
                    ordinal=ordinal,
                    parent_id=cast(str | None, parent_id),
                    root_id=cast(str, manifest["root_id"]),
                )
            _require(len(manifest_by_id) == len(manifests), "mysql-manifest-identity")
            evidence[document.document_id] = RevisionEvidence(
                locators=locators,
                manifest=manifest_by_id,
            )

        deletion_jobs = (
            (
                await connection.execute(
                    select(knowledge_ingestion_job).where(
                        knowledge_ingestion_job.c.revision_id == state.deleted.revision_id,
                        knowledge_ingestion_job.c.kind == "deletion",
                    )
                )
            )
            .mappings()
            .all()
        )
        _require(
            len(deletion_jobs) == 1 and deletion_jobs[0]["status"] == "completed",
            "mysql-deletion-job",
        )

    policy_manifest = evidence[state.policy.document_id].manifest
    _require(
        state.citation.chunk_id in policy_manifest
        and policy_manifest[state.citation.chunk_id].chunk_content_hash
        == state.citation.chunk_content_hash,
        "mysql-citation-manifest",
    )
    return evidence


async def _blob_missing(artifacts: AzureBlobArtifactStore, locator: ArtifactLocator) -> bool:
    container, blob_name = str(locator).split("/", 1)
    client = artifacts._service.get_blob_client(container, blob_name)  # noqa: SLF001
    try:
        await asyncio.wait_for(
            client.get_blob_properties(),
            timeout=artifacts._config.operation_timeout_seconds,  # noqa: SLF001
        )
    except ResourceNotFoundError:
        return True
    return False


async def _verify_blobs(
    artifacts: AzureBlobArtifactStore,
    settings: AthenaSettings,
    state: JourneyState,
    evidence: Mapping[str, RevisionEvidence],
) -> None:
    for document in state.survivors:
        revision = evidence[document.document_id]
        original = await artifacts.read_original(revision.locators[0])
        normalized = await artifacts.read_normalized(revision.locators[1])
        chunks = await artifacts.read_chunks(revision.locators[2])
        embeddings = await artifacts.read_embeddings(revision.locators[3])
        chunk_ids = tuple(str(chunk.chunk_id) for chunk in chunks)
        _require(
            canonical_sha256(original) == document.source_content_hash
            and normalized.source_hash == document.source_content_hash
            and str(normalized.document_id) == document.document_id
            and str(normalized.revision_id) == document.revision_id
            and set(chunk_ids) == set(revision.manifest)
            and all(
                item.ordinal == ordinal
                and item.root_id == document.document_id
                and str(chunk.root_id) == item.root_id
                and str(chunk.logical_chunk_id) == item.logical_chunk_id
                and chunk.parent_id == item.parent_id
                and chunk.source_content_hash == document.source_content_hash
                and item.chunk_content_hash == chunk.chunk_content_hash
                and item.anchor_json == chunk.anchor_json
                for ordinal, chunk in enumerate(chunks)
                for item in (revision.manifest[str(chunk.chunk_id)],)
            )
            and embeddings.model_alias == settings.embedding_alias
            and embeddings.dimension == settings.embedding_dimension
            and embeddings.chunk_ids == chunk_ids,
            "blob-survivor-binding",
        )
    for locator in evidence[state.deleted.document_id].locators:
        _require(await _blob_missing(artifacts, locator), "blob-deleted-artifact")


def _milvus_filter(document: DocumentState, *, live_only: bool) -> str:
    expression = (
        f"source_id == {json.dumps(document.document_id)} and "
        f"source_revision == {json.dumps(document.revision_id)}"
    )
    return f"{expression} and deleted == false" if live_only else expression


async def _milvus_rows(
    reader: MilvusReader,
    collection: str,
    document: DocumentState,
    *,
    live_only: bool,
) -> tuple[Mapping[str, object], ...]:
    return await reader.query(
        MilvusQueryRequest(
            collection_name=collection,
            filter_expression=_milvus_filter(document, live_only=live_only),
            output_fields=_OUTPUT_FIELDS,
            limit=50,
        )
    )


def _projected_id(kind: str, value: str) -> str:
    if re.fullmatch(r"h_[a-f0-9]{64}", value) is not None:
        return value
    return "h_" + sha256(f"{kind}\0{value}".encode()).hexdigest()


def _require_milvus_survivor_row(
    row: Mapping[str, object],
    document: DocumentState,
    manifest: Mapping[str, ManifestEvidence],
    settings: AthenaSettings,
) -> None:
    chunk_id = row.get("chunk_id")
    _require(isinstance(chunk_id, str) and chunk_id in manifest, "milvus-survivor-binding")
    item = manifest[cast(str, chunk_id)]
    expected_parent = None if item.parent_id is None else _projected_id("parent", item.parent_id)
    _require(
        row.get("logical_chunk_id")
        == logical_chunk_projection_id(LogicalChunkId(item.logical_chunk_id))
        and row.get("root_id") == _projected_id("root", document.document_id)
        and row.get("parent_id") == expected_parent
        and row.get("content_role") == "source"
        and row.get("derived_from_chunk_ids") == []
        and row.get("source_id") == document.document_id
        and row.get("source_revision") == document.revision_id
        and row.get("source_content_hash") == document.source_content_hash
        and row.get("source_type") == "doc"
        and row.get("revision_kind") == "blob_version"
        and row.get("index_family") == "doc"
        and row.get("schema_version") == settings.schema_version
        and row.get("corpus_version") == settings.corpus_version
        and row.get("embedding_model_version") == settings.embedding_alias
        and row.get("chunk_content_hash") == item.chunk_content_hash
        and row.get("anchor_json") == item.anchor_json,
        "milvus-survivor-binding",
    )


async def _verify_milvus(
    reader: MilvusReader,
    collection: str,
    settings: AthenaSettings,
    state: JourneyState,
    evidence: Mapping[str, RevisionEvidence],
) -> None:
    for document in state.survivors:
        expected = evidence[document.document_id].manifest
        rows = await _milvus_rows(reader, collection, document, live_only=False)
        live_rows = await _milvus_rows(reader, collection, document, live_only=True)
        row_ids = {row.get("chunk_id") for row in rows}
        live_ids = {row.get("chunk_id") for row in live_rows}
        _require(
            len(rows) == len(expected)
            and len(live_rows) == len(expected)
            and row_ids == set(expected)
            and live_ids == set(expected),
            "milvus-survivor-count",
        )
        for row in rows:
            _require_milvus_survivor_row(row, document, expected, settings)
        _require(len(expected) < 50, "milvus-source-query-bound")
        source_rows = await reader.query(
            MilvusQueryRequest(
                collection_name=collection,
                filter_expression=f"source_id == {json.dumps(document.document_id)}",
                output_fields=_OUTPUT_FIELDS,
                limit=50,
            )
        )
        _require(
            len(source_rows) == len(expected)
            and {row.get("chunk_id") for row in source_rows} == set(expected)
            and all(row.get("source_revision") == document.revision_id for row in source_rows),
            "milvus-source-inventory",
        )
        for row in source_rows:
            _require_milvus_survivor_row(row, document, expected, settings)
    deleted_rows = await _milvus_rows(
        reader,
        collection,
        state.deleted,
        live_only=False,
    )
    _require(not deleted_rows, "milvus-deleted-projection")
    deleted_source_rows = await reader.query(
        MilvusQueryRequest(
            collection_name=collection,
            filter_expression=f"source_id == {json.dumps(state.deleted.document_id)}",
            output_fields=_OUTPUT_FIELDS,
            limit=50,
        )
    )
    deleted_true_rows = await reader.query(
        MilvusQueryRequest(
            collection_name=collection,
            filter_expression=(
                f"source_id == {json.dumps(state.deleted.document_id)} and deleted == true"
            ),
            output_fields=_OUTPUT_FIELDS,
            limit=2,
        )
    )
    deleted_false_rows = await reader.query(
        MilvusQueryRequest(
            collection_name=collection,
            filter_expression=(
                f"source_id == {json.dumps(state.deleted.document_id)} and deleted == false"
            ),
            output_fields=_OUTPUT_FIELDS,
            limit=2,
        )
    )
    fence_id = "h_" + sha256(state.deleted.revision_id.encode()).hexdigest()
    fence_hash = canonical_sha256(b"athena deletion fence")
    _require(
        len(deleted_source_rows) == 1
        and len(deleted_true_rows) == 1
        and deleted_true_rows[0].get("chunk_id") == fence_id
        and not deleted_false_rows,
        "milvus-deleted-source-inventory",
    )
    fence = deleted_source_rows[0]
    _require(
        fence.get("chunk_id") == fence_id
        and fence.get("logical_chunk_id") == fence_id
        and fence.get("root_id") == state.deleted.document_id
        and fence.get("parent_id") is None
        and fence.get("content_role") == "source"
        and fence.get("index_family") == "doc"
        and fence.get("derived_from_chunk_ids") == []
        and fence.get("source_id") == state.deleted.document_id
        and fence.get("source_revision") == f"fence:{state.deleted.revision_id}"
        and fence.get("source_type") == "athena_fence"
        and fence.get("revision_kind") == "deletion_fence"
        and fence.get("source_content_hash") == fence_hash
        and fence.get("chunk_content_hash") == fence_hash
        and fence.get("anchor_json") == "{}"
        and fence.get("schema_version") == settings.schema_version
        and fence.get("corpus_version") == settings.corpus_version
        and fence.get("embedding_model_version") == settings.embedding_alias,
        "milvus-deletion-fence-binding",
    )


def _scope_request(state: JourneyState, document_ids: tuple[str, ...]) -> RetrievalSearchRequest:
    return RetrievalSearchRequest.model_validate(
        {
            "query": (
                "What approval and finance review rules apply to "
                f"Athena {state.run_id} refund requests?"
            ),
            "answerMode": "quick",
            "sources": ["doc"],
            "resourceRefs": [
                {"family": "doc", "sourceId": document_id, "mode": "scope"}
                for document_id in document_ids
            ],
        }
    )


def _answer_request(request: RetrievalSearchRequest) -> RetrievalAnswerRequest:
    return RetrievalAnswerRequest.model_validate(request.model_dump(by_alias=True))


def _require_hit_provenance(
    hits: Sequence[object],
    documents: Mapping[str, DocumentState],
    manifests: Mapping[str, Mapping[str, ManifestEvidence]],
    code: str,
) -> set[str]:
    contributors: set[str] = set()
    for value in hits:
        source = getattr(value, "source", None)
        source_id = getattr(source, "source_id", None)
        _require(isinstance(source_id, str), code)
        source_id = cast(str, source_id)
        document = documents.get(source_id)
        _require(document is not None, code)
        document = cast(DocumentState, document)
        chunk_id = getattr(value, "chunk_id", None)
        _require(isinstance(chunk_id, str), code)
        chunk_id = cast(str, chunk_id)
        manifest = manifests[source_id].get(chunk_id)
        _require(
            manifest is not None
            and getattr(source, "revision", None) == document.revision_id
            and getattr(source, "source_content_hash", None) == document.source_content_hash
            and getattr(value, "chunk_content_hash", None) == manifest.chunk_content_hash
            and getattr(value, "logical_chunk_id", None)
            == logical_chunk_projection_id(LogicalChunkId(manifest.logical_chunk_id)),
            code,
        )
        contributors.add(cast(str, source_id))
    return contributors


def _answer_contributors(
    response: RetrievalAnswerResponse,
    documents: Mapping[str, DocumentState],
    manifests: Mapping[str, Mapping[str, ManifestEvidence]],
) -> set[str]:
    citations = response.citations
    claims = response.claims
    _require(bool(citations), "answer-citations")
    _require(bool(claims), "answer-claims")
    citation_ids = [citation.citation_id for citation in citations]
    _require(len(citation_ids) == len(set(citation_ids)), "answer-citation-identity")
    citation_chunks = [(citation.source.source_id, citation.chunk_id) for citation in citations]
    _require(
        len(citation_chunks) == len(set(citation_chunks)),
        "answer-citation-identity",
    )
    source_by_citation = {citation.citation_id: citation.source.source_id for citation in citations}
    referenced_citation_ids = {
        citation_id for claim in claims for citation_id in claim.citation_ids
    }
    _require(
        all(len(claim.citation_ids) == len(set(claim.citation_ids)) for claim in claims)
        and referenced_citation_ids == set(citation_ids),
        "answer-citation-closure",
    )
    _require_hit_provenance(list(citations), documents, manifests, "answer-provenance")
    contributors = {
        source_by_citation[citation_id]
        for claim in claims
        for citation_id in claim.citation_ids
        if citation_id in source_by_citation
    }
    _require(
        all(
            citation_id in source_by_citation
            for claim in claims
            for citation_id in claim.citation_ids
        ),
        "answer-claim-binding",
    )
    return contributors


async def _verify_composed_runtime(
    runtime: object,
    state: JourneyState,
    evidence: Mapping[str, RevisionEvidence],
) -> None:
    services = getattr(runtime, "http_services", None)
    service = getattr(services, "knowledge", None)
    _require(isinstance(service, KnowledgeHttpService), "runtime-knowledge-service")
    service = cast(KnowledgeHttpService, service)
    for document in state.survivors:
        detail = await service.get_document(document.document_id)
        _require(
            detail.status.value == "ready"
            and detail.revision_id == document.revision_id
            and detail.source_content_hash == document.source_content_hash,
            "runtime-document-binding",
        )
    preview = await service.citation(state.citation.citation_id)
    _require(
        preview.document_id == state.citation.document_id
        and preview.revision_id == state.citation.revision_id
        and preview.source_content_hash == state.citation.source_content_hash
        and preview.chunk_content_hash == state.citation.chunk_content_hash
        and _canonical_anchor_hash(preview.anchor.model_dump(by_alias=True))
        == state.citation.anchor_hash
        and _text_hash(preview.quote) == state.citation.quote_hash,
        "runtime-citation-binding",
    )

    dual_ids = (state.policy.document_id, state.reference.document_id)
    dual_documents = {
        document.document_id: document for document in (state.policy, state.reference)
    }
    dual_manifests = {document_id: evidence[document_id].manifest for document_id in dual_ids}
    dual_request = _scope_request(state, dual_ids)
    dual_search = await service.search(dual_request)
    _require(
        _require_hit_provenance(
            dual_search.hits,
            dual_documents,
            dual_manifests,
            "search-dual-provenance",
        )
        == set(dual_ids),
        "search-dual-scope",
    )
    dual_answer = await service.answer(_answer_request(dual_request))
    _require(
        not dual_answer.abstained
        and _answer_contributors(dual_answer, dual_documents, dual_manifests) == set(dual_ids),
        "answer-dual-scope",
    )

    policy_request = _scope_request(state, (state.policy.document_id,))
    policy_documents = {state.policy.document_id: state.policy}
    policy_manifests = {state.policy.document_id: evidence[state.policy.document_id].manifest}
    policy_search = await service.search(policy_request)
    _require(
        _require_hit_provenance(
            policy_search.hits,
            policy_documents,
            policy_manifests,
            "search-policy-provenance",
        )
        == {state.policy.document_id},
        "search-policy-scope",
    )
    policy_answer = await service.answer(_answer_request(policy_request))
    _require(
        not policy_answer.abstained
        and _answer_contributors(policy_answer, policy_documents, policy_manifests)
        == {state.policy.document_id},
        "answer-policy-scope",
    )


def _canonical_anchor_hash(value: object) -> str:
    item = _exact_mapping(
        value,
        {"bbox", "endOffset", "headingPath", "page", "startOffset", "type"},
        "citation-anchor-shape",
    )
    _require(item["type"] == "document", "citation-anchor-type")
    canonical = {
        "type": "document",
        "headingPath": item["headingPath"],
        "page": item["page"],
        "bbox": item["bbox"],
        "startOffset": item["startOffset"],
        "endOffset": item["endOffset"],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + sha256(encoded.encode()).hexdigest()


def _text_hash(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _exact_e2e_settings() -> AthenaSettings:
    settings = AthenaSettings.from_mapping(os.environ)
    _require(
        settings.e2e_mode
        and settings.compose_project == "tap-athena-e2e"
        and settings.api_port == 18000
        and settings.web_port == 15173
        and ":13306/" in settings.database_url
        and ":16379/0" in settings.redis_url
        and settings.milvus_uri == "http://127.0.0.1:29530"
        and "BlobEndpoint=http://127.0.0.1:11000/devstoreaccount1"
        in settings.blob_connection_string,
        "settings-isolation",
    )
    return settings


async def _run_verifier(settings: AthenaSettings, state: JourneyState) -> None:
    resources = OwnedResources()
    try:
        engine, _repository = await _create_database(settings)
        resources.push(engine)
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        search, reader, target = await _create_search(settings)
        resources.push(search)
        evidence = await _verify_database(engine, settings, state)
        await _verify_blobs(artifacts, settings, state, evidence)
        bound = await bind_target(reader, target)
        await _verify_milvus(
            reader,
            bound.physical_collection,
            settings,
            state,
            evidence,
        )
        for _ in range(2):
            await _verify_reconstructed_runtime(settings, state, evidence)
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("verifier resource settlement unexpectedly returned")
    await resources.aclose()


async def _verify_reconstructed_runtime(
    settings: AthenaSettings,
    state: JourneyState,
    evidence: Mapping[str, RevisionEvidence],
) -> None:
    resources = OwnedResources()
    try:
        runtime = await create_api_runtime(settings)
        resources.push(runtime)
        await _verify_composed_runtime(runtime, state, evidence)
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("runtime verification settlement unexpectedly returned")
    await resources.aclose()


def _closed_state_payload() -> dict[str, object]:
    def document(index: int) -> dict[str, str]:
        return {
            "documentId": f"doc-{index}",
            "jobId": f"job-{index}",
            "revisionId": f"rev-{index}",
            "sourceContentHash": "sha256:" + f"{index:x}" * 64,
        }

    policy = document(1)
    return {
        "schemaVersion": 1,
        "runId": "athena-0123456789abcdef",
        "policy": policy,
        "reference": document(2),
        "other": document(3),
        "injection": document(4),
        "deleted": document(8),
        "recovered": [document(5), document(6), document(7)],
        "citation": {
            "citationId": "citation-1",
            "chunkId": "chunk-1",
            "documentId": policy["documentId"],
            "revisionId": policy["revisionId"],
            "sourceContentHash": policy["sourceContentHash"],
            "chunkContentHash": "sha256:" + "a" * 64,
            "anchorHash": "sha256:" + "b" * 64,
            "quoteHash": "sha256:" + "c" * 64,
        },
    }


def _write_closed_state(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def test_verifier_state_loader_accepts_only_sanitized_ids_and_hashes(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _write_closed_state(path, _closed_state_payload())

    state = _load_state(str(path))

    assert state.run_id == "athena-0123456789abcdef"
    assert len(state.survivors) == 7


@pytest.mark.parametrize("extra", ["anchor", "quote", "count", "locator", "url"])
def test_verifier_state_loader_rejects_cross_phase_content_and_locations(
    tmp_path: Path,
    extra: str,
) -> None:
    path = tmp_path / "state.json"
    value = _closed_state_payload()
    value[extra] = "forbidden"
    _write_closed_state(path, value)

    with pytest.raises(VerificationFailure, match="state-shape"):
        _load_state(str(path))


@pytest.mark.parametrize(
    "drifted_field",
    ["parser_version", "chunker_version", "pipeline_version"],
)
def test_verifier_rejects_revision_version_drift(drifted_field: str) -> None:
    row = {
        "document_id": "doc-1",
        "source_content_hash": "sha256:" + "1" * 64,
        "parser_version": "athena-parser-v1",
        "chunker_version": "athena-structure-512-v1",
        "pipeline_version": "athena-ingestion-v1",
        "original_blob_locator": "original",
        "normalized_blob_locator": "normalized",
        "chunks_blob_locator": "chunks",
        "embeddings_blob_locator": "embeddings",
    }
    row[drifted_field] = "unexpected-version"
    document = DocumentState(
        document_id="doc-1",
        job_id="job-1",
        revision_id="rev-1",
        source_content_hash="sha256:" + "1" * 64,
    )

    with pytest.raises(VerificationFailure, match="mysql-revision-binding"):
        _require_revision_binding(
            row,
            document,
            (
                ArtifactLocator("original"),
                ArtifactLocator("normalized"),
                ArtifactLocator("chunks"),
                ArtifactLocator("embeddings"),
            ),
            "athena-ingestion-v1",
        )


def test_verifier_rejects_unlinked_answer_citations() -> None:
    document = DocumentState(
        document_id="doc-1",
        job_id="job-1",
        revision_id="rev-1",
        source_content_hash="sha256:" + "1" * 64,
    )
    manifest = ManifestEvidence(
        anchor_json="{}",
        chunk_content_hash="sha256:" + "2" * 64,
        chunk_id="chunk-1",
        logical_chunk_id="lc_" + "3" * 64,
        ordinal=0,
        parent_id=None,
        root_id="doc-1",
    )
    extra_manifest = ManifestEvidence(
        anchor_json="{}",
        chunk_content_hash="sha256:" + "4" * 64,
        chunk_id="chunk-2",
        logical_chunk_id="lc_" + "5" * 64,
        ordinal=1,
        parent_id=None,
        root_id="doc-1",
    )
    source = SimpleNamespace(
        revision="rev-1",
        source_content_hash=document.source_content_hash,
        source_id="doc-1",
    )
    response = SimpleNamespace(
        citations=(
            SimpleNamespace(
                chunk_content_hash=manifest.chunk_content_hash,
                chunk_id=manifest.chunk_id,
                citation_id="citation-used",
                source=source,
            ),
            SimpleNamespace(
                chunk_content_hash=extra_manifest.chunk_content_hash,
                chunk_id=extra_manifest.chunk_id,
                citation_id="citation-unlinked",
                source=source,
            ),
        ),
        claims=(SimpleNamespace(citation_ids=("citation-used",)),),
    )

    with pytest.raises(VerificationFailure, match="answer-citation-closure"):
        _answer_contributors(
            cast(RetrievalAnswerResponse, response),
            {document.document_id: document},
            {
                document.document_id: {
                    manifest.chunk_id: manifest,
                    extra_manifest.chunk_id: extra_manifest,
                }
            },
        )


@pytest.mark.asyncio
async def test_exact_athena_state_survives_application_and_compose_restarts() -> None:
    if os.environ.get("ATHENA_E2E_PHASE") != "verify":
        pytest.skip("requires the isolated Athena E2E verification phase")
    if os.environ.get("TAP_RUN_ATHENA_E2E") != "1":
        pytest.fail("Athena E2E verification gate is missing.", pytrace=False)

    failure_code: str | None = None
    try:
        settings = _exact_e2e_settings()
        state = _load_state(os.environ.get("ATHENA_E2E_STATE_FILE"))
        with suppress_pymilvus_rpc_logging():
            await _run_verifier(settings, state)
    except asyncio.CancelledError:
        raise
    except VerificationFailure as error:
        failure_code = error.code
    except Exception:
        failure_code = "provider-or-integrity"
    if failure_code is not None:
        pytest.fail(f"Athena persistence verification failed: {failure_code}.", pytrace=False)


@pytest.mark.asyncio
async def test_selected_verifier_suppresses_worker_thread_rpc_details_and_restores_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = sys.modules[__name__]
    settings = object()
    state = object()
    provider_logger = logging.getLogger("pymilvus.decorators")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    original_level = provider_logger.level
    original_propagate = provider_logger.propagate
    provider_logger.addHandler(handler)
    provider_logger.setLevel(logging.ERROR)
    provider_logger.propagate = False

    async def noisy_verifier(actual_settings: object, actual_state: object) -> None:
        assert actual_settings is settings
        assert actual_state is state
        await asyncio.to_thread(
            _emit_provider_rpc_error,
            "verifier-provider-secret-rpc-detail",
        )
        raise RuntimeError("verifier-provider-secret-exception-detail")

    monkeypatch.setenv("ATHENA_E2E_PHASE", "verify")
    monkeypatch.setenv("TAP_RUN_ATHENA_E2E", "1")
    monkeypatch.setattr(module, "_exact_e2e_settings", lambda: settings)
    monkeypatch.setattr(module, "_load_state", lambda _path: state)
    monkeypatch.setattr(module, "_run_verifier", noisy_verifier)
    try:
        with pytest.raises(pytest.fail.Exception) as captured:
            await test_exact_athena_state_survives_application_and_compose_restarts()
        assert str(captured.value) == (
            "Athena persistence verification failed: provider-or-integrity."
        )
        assert "verifier-provider-secret-exception-detail" not in str(captured.value)
        _emit_provider_rpc_error("verifier-filter-removed-after-operation")
    finally:
        provider_logger.removeHandler(handler)
        provider_logger.setLevel(original_level)
        provider_logger.propagate = original_propagate

    rendered = output.getvalue()
    assert "verifier-provider-secret-rpc-detail" not in rendered
    assert "verifier-filter-removed-after-operation" in rendered
