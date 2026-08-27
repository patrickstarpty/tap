"""Content-addressed private Azure Blob storage for Athena ingestion artifacts."""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobClient, BlobServiceClient
from pydantic import SecretStr

from tap.modules.knowledge.domain.documents import (
    BlockKind,
    ChunkDraft,
    DocumentId,
    LogicalChunkId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DeletionTarget,
    EmbeddingArtifact,
    StagedOriginal,
    UploadStream,
)

ORIGINALS_CONTAINER = "athena-originals"
ARTIFACTS_CONTAINER = "athena-artifacts"
_CONTAINERS = frozenset({ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER})
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,512}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NORMALIZED_SCHEMA = "normalized-v1"
_CHUNKS_SCHEMA = "chunks-v1"
_EMBEDDINGS_SCHEMA = "embeddings-v1"
_MAX_BLOCKS = 100_000
_MAX_CHUNKS = 10_000
_MAX_VECTORS = 10_000
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class ArtifactIntegrityError(Exception):
    """A Blob operation or persisted envelope failed the provider-neutral integrity contract."""


@dataclass(frozen=True, slots=True)
class AzureBlobArtifactConfig:
    connection_string: SecretStr = field(repr=False)
    operation_timeout_seconds: float = 15.0
    copy_poll_seconds: float = 0.05
    copy_sas_lifetime: timedelta = timedelta(minutes=5)
    api_version: str = "2023-11-03"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.connection_string, SecretStr)
            or not self.connection_string.get_secret_value()
        ):
            raise ValueError("Azure Blob connection string is required")
        if (
            not isinstance(self.operation_timeout_seconds, (int, float))
            or isinstance(self.operation_timeout_seconds, bool)
            or not math.isfinite(self.operation_timeout_seconds)
            or not 0 < self.operation_timeout_seconds <= 60
        ):
            raise ValueError("Azure Blob operation timeout must be finite and bounded")
        if (
            not isinstance(self.copy_poll_seconds, (int, float))
            or isinstance(self.copy_poll_seconds, bool)
            or not math.isfinite(self.copy_poll_seconds)
            or not 0 < self.copy_poll_seconds <= 1
        ):
            raise ValueError("Azure Blob copy polling must be finite and bounded")
        if not timedelta(seconds=1) <= self.copy_sas_lifetime <= timedelta(minutes=5):
            raise ValueError("Azure Blob copy SAS lifetime must be at most five minutes")
        if self.api_version != "2023-11-03":
            raise ValueError("Azure Blob API version must match the pinned Azurite contract")


@dataclass(frozen=True, slots=True)
class ArtifactScavengeReceipt:
    scanned: int
    removed: tuple[str, ...]


def artifact_locator(container: str, blob_name: str) -> ArtifactLocator:
    if container not in _CONTAINERS:
        raise ValueError("artifact locator container is outside the closed set")
    _blob_name(blob_name)
    if any(character in blob_name for character in ("?", "#", "\\")):
        raise ValueError("artifact locator must contain identity only")
    return ArtifactLocator(f"{container}/{blob_name}")


def encode_normalized_artifact(revision_id: str, artifact: NormalizedArtifact) -> bytes:
    _identity("revision_id", revision_id)
    if not isinstance(artifact, NormalizedArtifact) or str(artifact.revision_id) != revision_id:
        raise ArtifactIntegrityError("normalized artifact identity does not match revision")
    payload = {
        "blocks": [
            {
                "blockId": block.block_id,
                "endOffset": block.end_offset,
                "headingPath": list(block.heading_path),
                "kind": BlockKind(block.kind).value,
                "page": block.page,
                "paragraphIndex": block.paragraph_index,
                "startOffset": block.start_offset,
                "text": block.text,
            }
            for block in artifact.blocks
        ],
        "documentId": str(artifact.document_id),
        "filename": artifact.filename,
        "mediaType": artifact.media_type.value,
        "normalizedSchema": artifact.schema,
    }
    payload_bytes = _canonical_line(payload)
    envelope = {
        "blockCount": len(artifact.blocks),
        "payload": payload,
        "payloadSha256": canonical_sha256(payload_bytes),
        "revisionId": revision_id,
        "schemaVersion": _NORMALIZED_SCHEMA,
        "sourceContentHash": artifact.source_hash,
    }
    return _canonical_line(envelope)


def decode_normalized_artifact(data: bytes, *, expected_revision: str) -> NormalizedArtifact:
    try:
        envelope = _closed_json_line(data)
        _exact_keys(
            envelope,
            {
                "blockCount",
                "payload",
                "payloadSha256",
                "revisionId",
                "schemaVersion",
                "sourceContentHash",
            },
        )
        if (
            envelope["schemaVersion"] != _NORMALIZED_SCHEMA
            or envelope["revisionId"] != expected_revision
        ):
            raise ValueError
        source_hash = _digest(envelope["sourceContentHash"])
        payload = _mapping(envelope["payload"])
        _exact_keys(
            payload,
            {"blocks", "documentId", "filename", "mediaType", "normalizedSchema"},
        )
        if canonical_sha256(_canonical_line(payload)) != _digest(envelope["payloadSha256"]):
            raise ValueError
        raw_blocks = _sequence(payload["blocks"], maximum=_MAX_BLOCKS)
        if envelope["blockCount"] != len(raw_blocks):
            raise ValueError
        blocks = []
        for raw in raw_blocks:
            block = _mapping(raw)
            _exact_keys(
                block,
                {
                    "blockId",
                    "endOffset",
                    "headingPath",
                    "kind",
                    "page",
                    "paragraphIndex",
                    "startOffset",
                    "text",
                },
            )
            heading = _sequence(block["headingPath"], maximum=32, allow_empty=True)
            blocks.append(
                NormalizedBlock(
                    block_id=_text(block["blockId"], maximum=512),
                    kind=BlockKind(_text(block["kind"], maximum=32)),
                    text=_text(block["text"], maximum=8_000_000),
                    heading_path=tuple(_text(item, maximum=256) for item in heading),
                    page=_optional_int(block["page"], minimum=1),
                    paragraph_index=_integer(block["paragraphIndex"], minimum=0),
                    start_offset=_integer(block["startOffset"], minimum=0),
                    end_offset=_integer(block["endOffset"], minimum=1),
                )
            )
        return NormalizedArtifact(
            filename=_text(payload["filename"], maximum=1024),
            media_type=MediaType(_text(payload["mediaType"], maximum=128)),
            source_hash=source_hash,
            blocks=tuple(blocks),
            document_id=DocumentId(_text(payload["documentId"], maximum=256)),
            revision_id=RevisionId(expected_revision),
            schema=_text(payload["normalizedSchema"], maximum=128),
        )
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("normalized artifact integrity check failed") from error


def encode_chunks_artifact(revision_id: str, chunks: tuple[ChunkDraft, ...]) -> bytes:
    _identity("revision_id", revision_id)
    if not isinstance(chunks, tuple) or not 1 <= len(chunks) <= _MAX_CHUNKS:
        raise ArtifactIntegrityError("chunk artifact count is outside the bound")
    if not all(isinstance(chunk, ChunkDraft) for chunk in chunks):
        raise ArtifactIntegrityError("chunk artifact contains an invalid row")
    source_hashes = {chunk.source_content_hash for chunk in chunks}
    if len(source_hashes) != 1:
        raise ArtifactIntegrityError("chunk artifact source hash is not exact")
    payload = b"".join(_canonical_line(_chunk_payload(chunk)) for chunk in chunks)
    header = {
        "itemCount": len(chunks),
        "payloadSha256": canonical_sha256(payload),
        "revisionId": revision_id,
        "schemaVersion": _CHUNKS_SCHEMA,
        "sourceContentHash": next(iter(source_hashes)),
    }
    return gzip.compress(_canonical_line(header) + payload, mtime=0)


def decode_chunks_artifact(data: bytes, *, expected_revision: str) -> tuple[ChunkDraft, ...]:
    try:
        decoded = _bounded_gunzip(data)
        lines = decoded.splitlines(keepends=True)
        if len(lines) < 2 or any(not line.endswith(b"\n") for line in lines):
            raise ValueError
        header = _closed_json_line(lines[0])
        _exact_keys(
            header,
            {"itemCount", "payloadSha256", "revisionId", "schemaVersion", "sourceContentHash"},
        )
        if header["schemaVersion"] != _CHUNKS_SCHEMA or header["revisionId"] != expected_revision:
            raise ValueError
        source_hash = _digest(header["sourceContentHash"])
        payload = b"".join(lines[1:])
        if canonical_sha256(payload) != _digest(header["payloadSha256"]):
            raise ValueError
        if header["itemCount"] != len(lines) - 1 or not 1 <= len(lines) - 1 <= _MAX_CHUNKS:
            raise ValueError
        chunks = tuple(
            _chunk_from_payload(_closed_json_line(line), source_hash) for line in lines[1:]
        )
        return chunks
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("chunk artifact integrity check failed") from error


def encode_embeddings_artifact(
    revision_id: str,
    source_content_hash: str,
    artifact: EmbeddingArtifact,
) -> bytes:
    _identity("revision_id", revision_id)
    source_hash = _digest(source_content_hash)
    if (
        not isinstance(artifact, EmbeddingArtifact)
        or not 1 <= len(artifact.vectors) <= _MAX_VECTORS
    ):
        raise ArtifactIntegrityError("embedding artifact count is outside the bound")
    payload = b"".join(
        _canonical_line({"ordinal": index, "vector": list(vector)})
        for index, vector in enumerate(artifact.vectors)
    )
    header = {
        "dimension": artifact.dimension,
        "itemCount": len(artifact.vectors),
        "model": artifact.model_alias,
        "payloadSha256": canonical_sha256(payload),
        "revisionId": revision_id,
        "schemaVersion": _EMBEDDINGS_SCHEMA,
        "sourceContentHash": source_hash,
    }
    return gzip.compress(_canonical_line(header) + payload, mtime=0)


def decode_embeddings_artifact(data: bytes, *, expected_revision: str) -> EmbeddingArtifact:
    try:
        decoded = _bounded_gunzip(data)
        lines = decoded.splitlines(keepends=True)
        if len(lines) < 2 or any(not line.endswith(b"\n") for line in lines):
            raise ValueError
        header = _closed_json_line(lines[0])
        _exact_keys(
            header,
            {
                "dimension",
                "itemCount",
                "model",
                "payloadSha256",
                "revisionId",
                "schemaVersion",
                "sourceContentHash",
            },
        )
        if (
            header["schemaVersion"] != _EMBEDDINGS_SCHEMA
            or header["revisionId"] != expected_revision
        ):
            raise ValueError
        _digest(header["sourceContentHash"])
        payload = b"".join(lines[1:])
        if canonical_sha256(payload) != _digest(header["payloadSha256"]):
            raise ValueError
        count = _integer(header["itemCount"], minimum=1, maximum=_MAX_VECTORS)
        dimension = _integer(header["dimension"], minimum=1, maximum=4096)
        if count != len(lines) - 1:
            raise ValueError
        vectors: list[tuple[float, ...]] = []
        for expected_ordinal, line in enumerate(lines[1:]):
            row = _closed_json_line(line)
            _exact_keys(row, {"ordinal", "vector"})
            if row["ordinal"] != expected_ordinal:
                raise ValueError
            raw_vector = _sequence(row["vector"], maximum=dimension)
            if len(raw_vector) != dimension:
                raise ValueError
            vectors.append(tuple(_strict_float(item) for item in raw_vector))
        return EmbeddingArtifact(
            model_alias=_text(header["model"], maximum=256),
            dimension=dimension,
            vectors=tuple(vectors),
        )
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("embedding artifact integrity check failed") from error


class AzureBlobArtifactStore:
    """Bounded async Blob adapter; locator values never carry credentials or SAS authority."""

    def __init__(self, config: AzureBlobArtifactConfig) -> None:
        if not isinstance(config, AzureBlobArtifactConfig):
            raise TypeError("Azure Blob artifact store requires validated configuration")
        self._config = config
        self._service = BlobServiceClient.from_connection_string(
            config.connection_string.get_secret_value(),
            api_version=config.api_version,
        )
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def ensure_containers(self) -> None:
        for name in (ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER):
            try:
                await self._bounded(self._service.create_container(name, public_access=None))
            except ResourceExistsError:
                continue

    async def stage_original(self, upload: UploadStream, *, max_bytes: int) -> StagedOriginal:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 25 * 1024 * 1024:
            raise ValueError("original upload byte bound is invalid")
        data = bytearray()
        async for part in upload.content:
            if not isinstance(part, bytes):
                raise ArtifactIntegrityError("original upload yielded non-bytes")
            data.extend(part)
            if len(data) > max_bytes:
                raise ArtifactIntegrityError("original upload exceeds the byte bound")
        if not data:
            raise ArtifactIntegrityError("original upload is empty")
        digest = canonical_sha256(bytes(data))
        blob_name = f"staging/{uuid4().hex}"
        blob = self._blob(ORIGINALS_CONTAINER, blob_name)
        try:
            await self._bounded(
                blob.upload_blob(
                    bytes(data),
                    overwrite=False,
                    metadata={
                        "blobsha256": digest.removeprefix("sha256:"),
                        "size": str(len(data)),
                        "stagedat": datetime.now(timezone.utc).isoformat(),
                    },
                    content_settings=ContentSettings(content_type=upload.media_type),
                )
            )
        except asyncio.CancelledError:
            await _terminal_cleanup(blob.delete_blob(delete_snapshots="include"))
            raise
        except Exception as error:
            await _terminal_cleanup(blob.delete_blob(delete_snapshots="include"))
            raise ArtifactIntegrityError("original staging failed") from error
        return StagedOriginal(
            staging_key=blob_name,
            filename=upload.filename,
            media_type=upload.media_type,
            size=len(data),
            source_content_hash=digest,
        )

    async def commit_original(
        self,
        staged: StagedOriginal,
        revision_id: str,
    ) -> ArtifactLocator:
        if not isinstance(staged, StagedOriginal):
            raise TypeError("original promotion requires a staged original")
        return await self._promote(
            staged.staging_key,
            revision_id,
            expected_size=staged.size,
            expected_hash=staged.source_content_hash,
        )

    async def recover_original(self, staging_key: str, revision_id: str) -> ArtifactLocator:
        properties = await self._staging_properties(staging_key)
        digest = _metadata_digest(properties.metadata)
        size = _metadata_size(properties.metadata)
        return await self._promote(
            staging_key,
            revision_id,
            expected_size=size,
            expected_hash=digest,
        )

    async def discard_staged(self, staged: StagedOriginal) -> None:
        await self.discard_staging(staged.staging_key)

    async def discard_staging(self, staging_key: str) -> None:
        _staging_name(staging_key)
        await self._delete_if_exists(self._blob(ORIGINALS_CONTAINER, staging_key))

    async def read_original(self, locator: ArtifactLocator) -> bytes:
        container, blob_name = _parse_locator(locator, expected_container=ORIGINALS_CONTAINER)
        return await self._download_verified(self._blob(container, blob_name))

    async def write_normalized(
        self,
        revision_id: str,
        artifact: NormalizedArtifact,
    ) -> ArtifactLocator:
        blob_name = f"revisions/{revision_id}/normalized-v1.json"
        return await self._write_artifact(
            blob_name,
            encode_normalized_artifact(revision_id, artifact),
            content_type="application/json",
        )

    async def read_normalized(self, locator: ArtifactLocator) -> NormalizedArtifact:
        container, blob_name = _parse_locator(locator, expected_container=ARTIFACTS_CONTAINER)
        return decode_normalized_artifact(
            await self._download_verified(self._blob(container, blob_name)),
            expected_revision=_revision_from_artifact_name(blob_name),
        )

    async def write_chunks(
        self,
        revision_id: str,
        chunks: tuple[ChunkDraft, ...],
    ) -> ArtifactLocator:
        blob_name = f"revisions/{revision_id}/chunks-v1.jsonl.gz"
        return await self._write_artifact(
            blob_name,
            encode_chunks_artifact(revision_id, chunks),
            content_type="application/gzip",
        )

    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]:
        container, blob_name = _parse_locator(locator, expected_container=ARTIFACTS_CONTAINER)
        return decode_chunks_artifact(
            await self._download_verified(self._blob(container, blob_name)),
            expected_revision=_revision_from_artifact_name(blob_name),
        )

    async def write_embeddings(
        self,
        revision_id: str,
        artifact: EmbeddingArtifact,
        *,
        source_content_hash: str,
    ) -> ArtifactLocator:
        model = _safe_path_segment(artifact.model_alias)
        blob_name = f"revisions/{revision_id}/embeddings/{model}/{artifact.dimension}-v1.jsonl.gz"
        return await self._write_artifact(
            blob_name,
            encode_embeddings_artifact(revision_id, source_content_hash, artifact),
            content_type="application/gzip",
        )

    async def read_embeddings(self, locator: ArtifactLocator) -> EmbeddingArtifact:
        container, blob_name = _parse_locator(locator, expected_container=ARTIFACTS_CONTAINER)
        return decode_embeddings_artifact(
            await self._download_verified(self._blob(container, blob_name)),
            expected_revision=_revision_from_artifact_name(blob_name),
        )

    async def delete_revision_artifacts(self, target: DeletionTarget) -> None:
        if not isinstance(target, DeletionTarget):
            raise TypeError("artifact deletion requires an exact target")
        for locator in target.artifact_locators:
            container, blob_name = _parse_locator(locator)
            await self._delete_if_exists(self._blob(container, blob_name))

    async def scavenge_staging(
        self,
        *,
        now: datetime,
        visible_staging_keys: frozenset[str],
        limit: int = 100,
    ) -> ArtifactScavengeReceipt:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("staging scavenger limit is outside the bound")
        container = self._service.get_container_client(ORIGINALS_CONTAINER)
        scanned = 0
        removed: list[str] = []
        async with asyncio.timeout(self._config.operation_timeout_seconds):
            async for item in container.list_blobs(
                name_starts_with="staging/",
                include=["metadata"],
            ):
                if scanned >= limit:
                    break
                scanned += 1
                age = now - _metadata_staged_at(item.metadata)
                invisible = item.name not in visible_staging_keys
                if age >= timedelta(hours=24) or (invisible and age >= timedelta(hours=1)):
                    await self._delete_if_exists(container.get_blob_client(item.name))
                    removed.append(item.name)
        return ArtifactScavengeReceipt(scanned=scanned, removed=tuple(removed))

    async def container_properties(self, container: str) -> Mapping[str, object]:
        if container not in _CONTAINERS:
            raise ValueError("container is outside the closed set")
        properties = await self._bounded(
            self._service.get_container_client(container).get_container_properties()
        )
        return cast(Mapping[str, object], properties)

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            await self._service.close()

    async def _promote(
        self,
        staging_key: str,
        revision_id: str,
        *,
        expected_size: int,
        expected_hash: str,
    ) -> ArtifactLocator:
        _staging_name(staging_key)
        _identity("revision_id", revision_id)
        digest = _digest(expected_hash)
        source = self._blob(ORIGINALS_CONTAINER, staging_key)
        source_properties = await self._staging_properties(staging_key)
        if (
            source_properties.size != expected_size
            or _metadata_digest(source_properties.metadata) != digest
            or canonical_sha256(await self._download(source)) != digest
        ):
            raise ArtifactIntegrityError("staged original integrity check failed")
        blob_name = f"revisions/{revision_id}/{digest.removeprefix('sha256:')}"
        destination = self._blob(ORIGINALS_CONTAINER, blob_name)
        if await destination.exists():
            if await self._download_verified(destination) != await self._download(source):
                raise ArtifactIntegrityError(
                    "content-addressed original conflicts with existing data"
                )
            await self._delete_if_exists(source)
            return artifact_locator(ORIGINALS_CONTAINER, blob_name)

        copy_id: str | None = None
        try:
            result = await self._bounded(
                destination.start_copy_from_url(self._source_copy_url(source))
            )
            copy_id = result.get("copy_id") if isinstance(result, Mapping) else None
            await self._wait_copy_terminal(destination)
            properties = await self._bounded(destination.get_blob_properties())
            if properties.size != expected_size or await self._download_verified(
                destination
            ) != await self._download(source):
                raise ArtifactIntegrityError("promoted original integrity check failed")
        except asyncio.CancelledError as cancellation:
            await _terminal_cleanup(self._abort_and_delete(destination, copy_id))
            raise cancellation
        except Exception as error:
            await _terminal_cleanup(self._abort_and_delete(destination, copy_id))
            if isinstance(error, ArtifactIntegrityError):
                raise error
            raise ArtifactIntegrityError("server-side original copy failed") from None
        await self._delete_if_exists(source)
        return artifact_locator(ORIGINALS_CONTAINER, blob_name)

    def _source_copy_url(self, source: BlobClient) -> str:
        credential = self._service.credential
        account_key = getattr(credential, "account_key", None)
        if not isinstance(account_key, str) or not account_key:
            raise ArtifactIntegrityError("server-side copy credential is unavailable")
        account_name = self._service.account_name
        if not isinstance(account_name, str) or not account_name:
            raise ArtifactIntegrityError("server-side copy account identity is unavailable")
        now = datetime.now(timezone.utc)
        sas = generate_blob_sas(
            account_name=account_name,
            account_key=account_key,
            container_name=source.container_name,
            blob_name=source.blob_name,
            permission=BlobSasPermissions(read=True),
            start=now - timedelta(seconds=5),
            expiry=now + self._config.copy_sas_lifetime,
        )
        return f"{source.url}?{sas}"

    async def _wait_copy_terminal(self, blob: BlobClient) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.operation_timeout_seconds
        while True:
            properties = await self._bounded(blob.get_blob_properties())
            copy = properties.copy
            status = getattr(copy, "status", None)
            if status == "success":
                return
            if status in {"failed", "aborted"}:
                raise ArtifactIntegrityError("server-side original copy did not succeed")
            if status != "pending" or loop.time() >= deadline:
                raise ArtifactIntegrityError(
                    "server-side original copy did not reach terminal success"
                )
            await asyncio.sleep(self._config.copy_poll_seconds)

    async def _abort_and_delete(self, blob: BlobClient, copy_id: str | None) -> None:
        try:
            properties = await blob.get_blob_properties()
            if getattr(properties.copy, "status", None) == "pending" and copy_id:
                await blob.abort_copy(copy_id)
        except Exception:
            pass
        await self._delete_if_exists(blob)

    async def _write_artifact(
        self,
        blob_name: str,
        data: bytes,
        *,
        content_type: str,
    ) -> ArtifactLocator:
        digest = canonical_sha256(data)
        blob = self._blob(ARTIFACTS_CONTAINER, blob_name)
        try:
            await self._bounded(
                blob.upload_blob(
                    data,
                    overwrite=True,
                    metadata={"blobsha256": digest.removeprefix("sha256:"), "size": str(len(data))},
                    content_settings=ContentSettings(content_type=content_type),
                )
            )
            if await self._download_verified(blob) != data:
                raise ArtifactIntegrityError("artifact Blob readback mismatch")
        except asyncio.CancelledError as cancellation:
            await _terminal_cleanup(blob.delete_blob(delete_snapshots="include"))
            raise cancellation
        except Exception as error:
            await _terminal_cleanup(blob.delete_blob(delete_snapshots="include"))
            if isinstance(error, ArtifactIntegrityError):
                raise error
            raise ArtifactIntegrityError("artifact Blob write failed") from error
        return artifact_locator(ARTIFACTS_CONTAINER, blob_name)

    async def _download_verified(self, blob: BlobClient) -> bytes:
        try:
            properties = await self._bounded(blob.get_blob_properties())
            data = await self._download(blob)
            if properties.size != len(data) or _metadata_size(properties.metadata) != len(data):
                raise ValueError
            if canonical_sha256(data) != _metadata_digest(properties.metadata):
                raise ValueError
            return data
        except ResourceNotFoundError:
            raise
        except Exception as error:
            raise ArtifactIntegrityError("Blob content hash verification failed") from error

    async def _download(self, blob: BlobClient) -> bytes:
        stream = await self._bounded(blob.download_blob(max_concurrency=1))
        data = await self._bounded(stream.readall())
        if not isinstance(data, bytes) or len(data) > _MAX_ARTIFACT_BYTES:
            raise ArtifactIntegrityError("Blob download exceeds the artifact bound")
        return data

    async def _staging_properties(self, staging_key: str):  # type: ignore[no-untyped-def]
        _staging_name(staging_key)
        return await self._bounded(
            self._blob(ORIGINALS_CONTAINER, staging_key).get_blob_properties()
        )

    async def _delete_if_exists(self, blob: BlobClient) -> None:
        try:
            await self._bounded(blob.delete_blob(delete_snapshots="include"))
        except ResourceNotFoundError:
            return

    async def _bounded(self, operation):  # type: ignore[no-untyped-def]
        self._ensure_open()
        async with asyncio.timeout(self._config.operation_timeout_seconds):
            return await operation

    def _blob(self, container: str, blob_name: str) -> BlobClient:
        self._ensure_open()
        return self._service.get_blob_client(container, blob_name)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ArtifactIntegrityError("Azure Blob artifact store is closed")


async def _terminal_cleanup(operation: Any) -> None:
    task = asyncio.create_task(operation)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    try:
        task.result()
    except Exception:
        pass


def _chunk_payload(chunk: ChunkDraft) -> dict[str, object]:
    if not isinstance(chunk, ChunkDraft):
        raise ArtifactIntegrityError("chunk artifact contains an invalid row")
    try:
        anchor = json.loads(chunk.anchor_json, parse_constant=_reject_constant)
        content_hash = canonical_sha256(chunk.content.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError("chunk artifact anchor is malformed") from error
    if (
        not isinstance(anchor, dict)
        or json.dumps(anchor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        != chunk.anchor_json
        or content_hash != chunk.chunk_content_hash
    ):
        raise ArtifactIntegrityError("chunk artifact provenance is inconsistent")
    return {
        "anchorJson": chunk.anchor_json,
        "chunkContentHash": chunk.chunk_content_hash,
        "chunkId": str(chunk.chunk_id),
        "content": chunk.content,
        "logicalChunkId": str(chunk.logical_chunk_id),
        "parentId": chunk.parent_id,
        "rootId": str(chunk.root_id),
    }


def _chunk_from_payload(value: Mapping[str, object], source_hash: str) -> ChunkDraft:
    _exact_keys(
        value,
        {
            "anchorJson",
            "chunkContentHash",
            "chunkId",
            "content",
            "logicalChunkId",
            "parentId",
            "rootId",
        },
    )
    parent = value["parentId"]
    chunk = ChunkDraft(
        chunk_id=cast(Any, _text(value["chunkId"], maximum=128)),
        logical_chunk_id=LogicalChunkId(_text(value["logicalChunkId"], maximum=128)),
        root_id=DocumentId(_text(value["rootId"], maximum=256)),
        parent_id=None if parent is None else _text(parent, maximum=256),
        content=_text(value["content"], maximum=32768),
        anchor_json=_text(value["anchorJson"], maximum=16384),
        source_content_hash=source_hash,
        chunk_content_hash=_digest(value["chunkContentHash"]),
    )
    _chunk_payload(chunk)
    return chunk


def _canonical_line(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ArtifactIntegrityError("artifact is not canonically serializable") from error


def _closed_json_line(data: bytes) -> Mapping[str, object]:
    if not isinstance(data, bytes) or not data.endswith(b"\n") or data.count(b"\n") != 1:
        raise ValueError("canonical JSON line is malformed")
    value = json.loads(
        data,
        object_pairs_hook=_closed_pairs,
        parse_constant=_reject_constant,
    )
    return _mapping(value)


def _closed_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("artifact JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise ValueError(f"artifact JSON constant is unsupported: {value}")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("artifact JSON value must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, maximum: int, allow_empty: bool = False) -> Sequence[object]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) > maximum
        or (not value and not allow_empty)
    ):
        raise ValueError("artifact JSON array is outside the bound")
    return cast(Sequence[object], value)


def _exact_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("artifact JSON object fields are not closed")


def _bounded_gunzip(data: bytes) -> bytes:
    if not isinstance(data, bytes) or len(data) > _MAX_ARTIFACT_BYTES:
        raise ValueError("compressed artifact is outside the bound")
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
        decoded = stream.read(_MAX_ARTIFACT_BYTES + 1)
    if len(decoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError("decompressed artifact is outside the bound")
    return decoded


def _text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 and character not in "\t\n\r" for character in value)
    ):
        raise ValueError("artifact text is outside the bound")
    return value


def _digest(value: object) -> str:
    text = _text(value, maximum=71)
    if _DIGEST.fullmatch(text) is None:
        raise ValueError("artifact digest is not canonical")
    return text


def _integer(value: object, *, minimum: int, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("artifact integer is outside the bound")
    return value


def _optional_int(value: object, *, minimum: int) -> int | None:
    return None if value is None else _integer(value, minimum=minimum)


def _strict_float(value: object) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("embedding value must be a finite float")
    return value


def _identity(name: str, value: object) -> str:
    try:
        return _safe_path_segment(cast(str, value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a safe identity") from error


def _safe_path_segment(value: str) -> str:
    if not isinstance(value, str) or _SAFE_SEGMENT.fullmatch(value) is None:
        raise ValueError("artifact path segment is unsafe")
    return value


def _blob_name(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or value.startswith("/")
        or value.endswith("/")
        or any(
            segment in {"", ".", ".."} or _SAFE_SEGMENT.fullmatch(segment) is None
            for segment in value.split("/")
        )
    ):
        raise ValueError("artifact Blob name is unsafe")


def _staging_name(value: str) -> None:
    _blob_name(value)
    if not value.startswith("staging/") or value.count("/") != 1:
        raise ValueError("staging Blob name is outside the closed prefix")


def _parse_locator(
    locator: ArtifactLocator,
    *,
    expected_container: str | None = None,
) -> tuple[str, str]:
    if not isinstance(locator, ArtifactLocator):
        raise ValueError("artifact locator type is invalid")
    parts = str(locator).split("/", 1)
    if len(parts) != 2:
        raise ValueError("artifact locator is malformed")
    container, blob_name = parts
    artifact_locator(container, blob_name)
    if expected_container is not None and container != expected_container:
        raise ValueError("artifact locator container does not match operation")
    return container, blob_name


def _revision_from_artifact_name(blob_name: str) -> str:
    parts = blob_name.split("/")
    if len(parts) < 3 or parts[0] != "revisions":
        raise ArtifactIntegrityError("artifact locator has no revision identity")
    return _identity("revision_id", parts[1])


def _metadata_digest(metadata: Mapping[str, str]) -> str:
    value = metadata.get("blobsha256")
    return _digest("sha256:" + value if isinstance(value, str) else value)


def _metadata_size(metadata: Mapping[str, str]) -> int:
    value = metadata.get("size")
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("Blob size metadata is malformed")
    return _integer(int(value), minimum=1, maximum=_MAX_ARTIFACT_BYTES)


def _metadata_staged_at(metadata: Mapping[str, str]) -> datetime:
    value = metadata.get("stagedat")
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ArtifactIntegrityError("staging timestamp metadata is malformed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ArtifactIntegrityError("staging timestamp metadata is malformed") from error
    if parsed.tzinfo is None:
        raise ArtifactIntegrityError("staging timestamp metadata is timezone-naive")
    return parsed.astimezone(timezone.utc)
