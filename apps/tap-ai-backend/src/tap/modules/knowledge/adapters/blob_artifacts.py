"""Content-addressed private Azure Blob storage for Tapper ingestion artifacts."""

from __future__ import annotations

import asyncio
import json
import math
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from hashlib import sha256
from typing import Any, ParamSpec, TypeVar, cast
from uuid import uuid4

from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobClient, BlobServiceClient
from pydantic import SecretStr

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.artifact_codecs import (
    ArtifactIntegrityError as ArtifactIntegrityError,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _bounded_gunzip as _bounded_gunzip,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _canonical_line as _canonical_line,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _chunk_from_payload as _chunk_from_payload,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _chunk_identity as _chunk_identity,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _chunk_payload as _chunk_payload,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _closed_json_line as _closed_json_line,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _closed_pairs as _closed_pairs,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _digest as _digest,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _exact_keys as _exact_keys,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _identity as _identity,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _integer as _integer,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _mapping as _mapping,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _optional_int as _optional_int,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _persisted_identity as _persisted_identity,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _reject_constant as _reject_constant,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _safe_path_segment as _safe_path_segment,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _sequence as _sequence,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _strict_float as _strict_float,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    _text as _text,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    decode_chunks_artifact as decode_chunks_artifact,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    decode_embeddings_artifact as decode_embeddings_artifact,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    decode_normalized_artifact as decode_normalized_artifact,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    encode_chunks_artifact as encode_chunks_artifact,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    encode_embeddings_artifact as encode_embeddings_artifact,
)
from tap.modules.knowledge.adapters.artifact_codecs import (
    encode_normalized_artifact as encode_normalized_artifact,
)
from tap.modules.knowledge.domain.documents import (
    ChunkDraft,
    NormalizedArtifact,
    canonical_sha256,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    ArtifactScavengeReceipt,
    DeletionTarget,
    EmbeddingArtifact,
    StagedOriginal,
    UploadStream,
)
from tap.modules.knowledge.ports.errors import ArtifactUnavailable
from tap.platform.db.project_scope import require_project_scope

ORIGINALS_CONTAINER = "tapper-originals"
ARTIFACTS_CONTAINER = "tapper-artifacts"
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
_COPY_RECOVERY_PROBE_TIMEOUT_SECONDS = 0.01
_P = ParamSpec("_P")
_R = TypeVar("_R")
_ACTIVE_COPY_SETTLEMENT_DEADLINE: ContextVar[float | None] = ContextVar(
    "tapper_blob_copy_settlement_deadline",
    default=None,
)


class ArtifactProviderUnavailable(ArtifactUnavailable):
    """A bounded Blob SDK operation could not reach a valid terminal provider result."""


class _ArtifactArgumentValueError(ValueError):
    """Validated caller input is invalid before any provider operation."""


class _ArtifactArgumentTypeError(TypeError):
    """Validated caller input has the wrong public port type."""


def _artifact_boundary(
    operation: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Keep every public Blob operation inside one provider-neutral error family."""

    @wraps(operation)
    async def translated(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return await operation(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except ArtifactProviderUnavailable as error:
            # Rebuild at the public edge so a secret-bearing provider exception
            # cannot survive as a formatted cause/context chain.
            raise ArtifactProviderUnavailable(str(error)) from None
        except ArtifactIntegrityError as error:
            raise ArtifactIntegrityError(str(error)) from None
        except (_ArtifactArgumentValueError, _ArtifactArgumentTypeError):
            raise
        except ResourceNotFoundError:
            # A public administration/write operation cannot infer artifact
            # staleness from a missing provider resource. Reads and promotion
            # translate their context-specific missing cases before this edge.
            raise ArtifactProviderUnavailable("Azure Blob provider operation failed") from None
        except (ResourceExistsError, ResourceModifiedError):
            raise ArtifactIntegrityError("Blob artifact state is conflicting") from None
        except Exception:
            raise ArtifactProviderUnavailable("Azure Blob provider operation failed") from None

    return translated


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


def artifact_locator(container: str, blob_name: str) -> ArtifactLocator:
    if container not in _CONTAINERS:
        raise ValueError("artifact locator container is outside the closed set")
    _blob_name(blob_name)
    if any(character in blob_name for character in ("?", "#", "\\")):
        raise ValueError("artifact locator must contain identity only")
    return ArtifactLocator(f"{container}/{blob_name}")


def _persisted_staging_name(value: object) -> str:
    try:
        _staging_name(cast(str, value))
    except (TypeError, ValueError):
        raise ArtifactIntegrityError("persisted staging locator is malformed") from None
    return cast(str, value)


def _persisted_digest(name: str, value: object) -> str:
    try:
        return _digest(value)
    except (TypeError, ValueError):
        raise ArtifactIntegrityError(f"persisted {name} is malformed") from None


def _persisted_locator(
    locator: object,
    *,
    expected_container: str | None = None,
) -> tuple[str, str]:
    if not isinstance(locator, ArtifactLocator):
        raise ArtifactIntegrityError("persisted artifact locator is malformed")
    try:
        return _parse_locator(locator, expected_container=expected_container)
    except (TypeError, ValueError):
        raise ArtifactIntegrityError("persisted artifact locator is malformed") from None


def staging_prefix(scope: ProjectScopeContext) -> str:
    scope = require_project_scope(scope)
    identity = json.dumps([scope.enterprise_id, scope.project_id], separators=(",", ":"))
    return "staging/project-" + sha256(identity.encode()).hexdigest() + "/"


class AzureBlobArtifactStore:
    """Bounded async Blob adapter; locator values never carry credentials or SAS authority."""

    def __init__(self, config: AzureBlobArtifactConfig, *, scope: ProjectScopeContext) -> None:
        if not isinstance(config, AzureBlobArtifactConfig):
            raise TypeError("Azure Blob artifact store requires validated configuration")
        self._scope = require_project_scope(scope)
        self._staging_prefix = staging_prefix(self._scope)
        self._config = config
        service: BlobServiceClient | None = None
        try:
            service = BlobServiceClient.from_connection_string(
                config.connection_string.get_secret_value(),
                api_version=config.api_version,
            )
        except Exception:
            pass
        if service is None:
            raise ArtifactProviderUnavailable("Azure Blob provider construction failed") from None
        self._service = service
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def scope(self) -> ProjectScopeContext:
        return self._scope

    def _require_staging_scope(self, key: str) -> None:
        _staging_name(key)
        if key.count("/") > 1 and not key.startswith(self._staging_prefix):
            raise _ArtifactArgumentValueError("staging scope differs from bound Project")

    @_artifact_boundary
    async def ensure_containers(self) -> None:
        for name in (ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER):
            try:
                await self._bounded(self._service.create_container(name, public_access=None))
            except ResourceExistsError:
                continue

    @_artifact_boundary
    async def stage_original(self, upload: UploadStream, *, max_bytes: int) -> StagedOriginal:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 25 * 1024 * 1024:
            raise _ArtifactArgumentValueError("original upload byte bound is invalid")
        digest_builder = sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            async for part in upload.content:
                if not isinstance(part, bytes):
                    raise ArtifactIntegrityError("original upload yielded non-bytes")
                if len(part) > max_bytes - size:
                    raise ArtifactIntegrityError("original upload exceeds the byte bound")
                size += len(part)
                digest_builder.update(part)
                spool.write(part)
            if size == 0:
                raise ArtifactIntegrityError("original upload is empty")
            digest = "sha256:" + digest_builder.hexdigest()
            blob_name = f"{self._staging_prefix}{uuid4().hex}"
            blob = self._blob(ORIGINALS_CONTAINER, blob_name)
            spool.seek(0)
            try:
                await self._bounded(
                    blob.upload_blob(
                        spool,
                        length=size,
                        overwrite=False,
                        metadata={
                            "blobsha256": digest.removeprefix("sha256:"),
                            "size": str(size),
                            "stagedat": datetime.now(timezone.utc).isoformat(),
                        },
                        content_settings=ContentSettings(content_type=upload.media_type),
                    )
                )
            except asyncio.CancelledError as cancellation:
                await self._cleanup(lambda: blob.delete_blob(delete_snapshots="include"))
                raise cancellation
            except ArtifactProviderUnavailable as unavailable:
                await self._cleanup(lambda: blob.delete_blob(delete_snapshots="include"))
                raise unavailable
            except Exception as error:
                await self._cleanup(lambda: blob.delete_blob(delete_snapshots="include"))
                raise ArtifactProviderUnavailable("original staging failed") from error
        return StagedOriginal(
            staging_key=blob_name,
            filename=upload.filename,
            media_type=upload.media_type,
            size=size,
            source_content_hash=digest,
        )

    @_artifact_boundary
    async def commit_original(
        self,
        staged: StagedOriginal,
        revision_id: str,
    ) -> ArtifactLocator:
        if not isinstance(staged, StagedOriginal):
            raise _ArtifactArgumentTypeError("original promotion requires a staged original")
        revision_id = _persisted_identity("revision identity", revision_id)
        return await self._promote(
            staged.staging_key,
            revision_id,
            expected_size=staged.size,
            expected_hash=staged.source_content_hash,
        )

    @_artifact_boundary
    async def recover_original(self, staging_key: str, revision_id: str) -> ArtifactLocator:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.operation_timeout_seconds
        staging_key = _persisted_staging_name(staging_key)
        self._require_staging_scope(staging_key)
        revision_id = _persisted_identity("revision identity", revision_id)
        try:
            properties = await self._promotion_call(
                lambda: self._staging_properties(staging_key), deadline
            )
        except asyncio.CancelledError:
            raise
        except ArtifactProviderUnavailable:
            raise
        except ArtifactIntegrityError:
            raise
        except ResourceNotFoundError as error:
            raise ArtifactIntegrityError("staged original does not exist") from error
        except Exception as error:
            raise ArtifactProviderUnavailable("staged original provider failed") from error
        try:
            digest = _metadata_digest(properties.metadata)
            size = _metadata_size(properties.metadata)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ArtifactIntegrityError("staged original metadata is malformed") from error
        return await self._promote(
            staging_key,
            revision_id,
            expected_size=size,
            expected_hash=digest,
            deadline=deadline,
            source_properties=properties,
        )

    @_artifact_boundary
    async def discard_staged(self, staged: StagedOriginal) -> None:
        if not isinstance(staged, StagedOriginal):
            raise _ArtifactArgumentTypeError("staging discard requires a staged original")
        await self.discard_staging(staged.staging_key)

    @_artifact_boundary
    async def discard_staging(self, staging_key: str) -> None:
        staging_key = _persisted_staging_name(staging_key)
        self._require_staging_scope(staging_key)
        await self._delete_if_exists(self._blob(ORIGINALS_CONTAINER, staging_key))

    @_artifact_boundary
    async def read_original(self, locator: ArtifactLocator) -> bytes:
        container, blob_name = _persisted_locator(locator, expected_container=ORIGINALS_CONTAINER)
        return await self._download_verified(self._blob(container, blob_name))

    @_artifact_boundary
    async def write_normalized(
        self,
        revision_id: str,
        artifact: NormalizedArtifact,
    ) -> ArtifactLocator:
        revision_id = _persisted_identity("revision identity", revision_id)
        blob_name = f"revisions/{revision_id}/normalized-v1.json"
        return await self._write_artifact(
            blob_name,
            encode_normalized_artifact(revision_id, artifact),
            content_type="application/json",
        )

    @_artifact_boundary
    async def read_normalized(self, locator: ArtifactLocator) -> NormalizedArtifact:
        container, blob_name = _persisted_locator(locator, expected_container=ARTIFACTS_CONTAINER)
        return decode_normalized_artifact(
            await self._download_verified(self._blob(container, blob_name)),
            expected_revision=_revision_from_artifact_name(blob_name),
        )

    @_artifact_boundary
    async def write_chunks(
        self,
        revision_id: str,
        chunks: tuple[ChunkDraft, ...],
    ) -> ArtifactLocator:
        revision_id = _persisted_identity("revision identity", revision_id)
        blob_name = f"revisions/{revision_id}/chunks-v1.jsonl.gz"
        return await self._write_artifact(
            blob_name,
            encode_chunks_artifact(revision_id, chunks),
            content_type="application/gzip",
        )

    @_artifact_boundary
    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]:
        container, blob_name = _persisted_locator(locator, expected_container=ARTIFACTS_CONTAINER)
        return decode_chunks_artifact(
            await self._download_verified(self._blob(container, blob_name)),
            expected_revision=_revision_from_artifact_name(blob_name),
        )

    @_artifact_boundary
    async def write_embeddings(
        self,
        revision_id: str,
        artifact: EmbeddingArtifact,
        *,
        source_content_hash: str,
    ) -> ArtifactLocator:
        revision_id = _persisted_identity("revision identity", revision_id)
        source_content_hash = _persisted_digest("source content hash", source_content_hash)
        try:
            model = _safe_path_segment(artifact.model_alias)
        except (AttributeError, TypeError, ValueError) as error:
            raise ArtifactIntegrityError(
                "embedding artifact model identity is malformed"
            ) from error
        blob_name = f"revisions/{revision_id}/embeddings/{model}/{artifact.dimension}-v1.jsonl.gz"
        return await self._write_artifact(
            blob_name,
            encode_embeddings_artifact(revision_id, source_content_hash, artifact),
            content_type="application/gzip",
        )

    @_artifact_boundary
    async def read_embeddings(self, locator: ArtifactLocator) -> EmbeddingArtifact:
        container, blob_name = _persisted_locator(locator, expected_container=ARTIFACTS_CONTAINER)
        return decode_embeddings_artifact(
            await self._download_verified(self._blob(container, blob_name)),
            expected_revision=_revision_from_artifact_name(blob_name),
        )

    @_artifact_boundary
    async def delete_revision_artifacts(self, target: DeletionTarget) -> None:
        if not isinstance(target, DeletionTarget):
            raise _ArtifactArgumentTypeError("artifact deletion requires an exact target")
        try:
            resolved = tuple(_parse_locator(locator) for locator in target.artifact_locators)
        except (AttributeError, TypeError, ValueError) as error:
            raise ArtifactIntegrityError("artifact deletion target is malformed") from error
        if any(
            _revision_from_artifact_name(blob_name) != target.revision_id
            for _, blob_name in resolved
        ):
            raise ArtifactIntegrityError("artifact deletion locator revision does not match")
        for container, blob_name in resolved:
            await self._delete_if_exists(self._blob(container, blob_name))

    @_artifact_boundary
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
            raise _ArtifactArgumentValueError("staging scavenger limit is outside the bound")
        container = self._service.get_container_client(ORIGINALS_CONTAINER)
        scanned = 0
        removed: list[str] = []
        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self._config.operation_timeout_seconds
        pages = container.list_blobs(
            name_starts_with=self._staging_prefix,
            include=["metadata"],
        ).__aiter__()
        while scanned < limit:
            remaining = deadline_at - loop.time()
            if remaining <= 0:
                raise ArtifactProviderUnavailable("Azure Blob provider operation exceeded deadline")
            try:
                item = await self._bounded(pages.__anext__(), timeout_seconds=remaining)
            except StopAsyncIteration:
                break
            scanned += 1
            try:
                item_name = item.name
                _staging_name(item_name)
                age = now - _metadata_staged_at(item.metadata)
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ArtifactIntegrityError("staging listing metadata is malformed") from error
            if not item_name.startswith(self._staging_prefix):
                continue
            invisible = item_name not in visible_staging_keys
            if invisible and age >= timedelta(hours=1):
                remaining = deadline_at - loop.time()
                if remaining <= 0:
                    raise ArtifactProviderUnavailable(
                        "Azure Blob provider operation exceeded deadline"
                    )
                etag = getattr(item, "etag", None)
                if not isinstance(etag, str) or not etag:
                    raise ArtifactIntegrityError("staging listing ETag is missing")
                try:
                    await self._bounded(
                        container.get_blob_client(item_name).delete_blob(
                            delete_snapshots="include",
                            etag=etag,
                            match_condition=MatchConditions.IfNotModified,
                        ),
                        timeout_seconds=remaining,
                    )
                except (ResourceModifiedError, ResourceNotFoundError):
                    continue
                if loop.time() >= deadline_at:
                    raise ArtifactProviderUnavailable(
                        "Azure Blob provider operation exceeded deadline"
                    )
                removed.append(item_name)
        return ArtifactScavengeReceipt(scanned=scanned, removed=tuple(removed))

    @_artifact_boundary
    async def container_properties(self, container: str) -> Mapping[str, object]:
        if container not in _CONTAINERS:
            raise _ArtifactArgumentValueError("container is outside the closed set")
        properties = await self._bounded(
            self._service.get_container_client(container).get_container_properties()
        )
        public_access = properties["public_access"]
        if public_access is not None and (
            not isinstance(public_access, str) or public_access not in {"blob", "container"}
        ):
            raise ArtifactProviderUnavailable("Azure Blob container properties are malformed")
        return {"public_access": public_access}

    @_artifact_boundary
    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            try:
                await self._bounded(self._service.close())
            finally:
                self._closed = True

    @_artifact_boundary
    async def aclose(self) -> None:
        await self.close()

    async def _promote(
        self,
        staging_key: str,
        revision_id: str,
        *,
        expected_size: int,
        expected_hash: str,
        deadline: float | None = None,
        source_properties: object | None = None,
    ) -> ArtifactLocator:
        try:
            self._require_staging_scope(staging_key)
            _identity("revision_id", revision_id)
            digest = _digest(expected_hash)
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError("original promotion source is invalid") from error
        promotion_deadline = (
            asyncio.get_running_loop().time() + self._config.operation_timeout_seconds
            if deadline is None
            else deadline
        )
        settlement_deadline = promotion_deadline + min(
            _COPY_RECOVERY_PROBE_TIMEOUT_SECONDS,
            self._config.operation_timeout_seconds,
        )
        settlement_token = _ACTIVE_COPY_SETTLEMENT_DEADLINE.set(settlement_deadline)
        try:
            return await self._promote_checked(
                staging_key,
                revision_id,
                expected_size=expected_size,
                expected_hash=digest,
                deadline=promotion_deadline,
                source_properties=source_properties,
            )
        except asyncio.CancelledError:
            raise
        except ArtifactProviderUnavailable:
            raise
        except ArtifactIntegrityError:
            raise
        except ResourceNotFoundError as error:
            raise ArtifactIntegrityError("original promotion source is invalid") from error
        except Exception as error:
            raise ArtifactProviderUnavailable("original promotion provider failed") from error
        finally:
            _ACTIVE_COPY_SETTLEMENT_DEADLINE.reset(settlement_token)

    async def _promote_checked(
        self,
        staging_key: str,
        revision_id: str,
        *,
        expected_size: int,
        expected_hash: str,
        deadline: float,
        source_properties: object | None = None,
    ) -> ArtifactLocator:
        digest = expected_hash
        source = self._blob(ORIGINALS_CONTAINER, staging_key)
        if source_properties is None:
            source_properties = await self._promotion_call(
                lambda: self._staging_properties(staging_key), deadline
            )
        try:
            source_size = source_properties.size  # type: ignore[attr-defined]
            source_digest = _metadata_digest(source_properties.metadata)  # type: ignore[attr-defined]
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ArtifactIntegrityError("staged original metadata is malformed") from error
        if source_size != expected_size or source_digest != digest:
            raise ArtifactIntegrityError("staged original integrity check failed")
        source_content = await self._promotion_call(lambda: self._download(source), deadline)
        if canonical_sha256(source_content) != digest:
            raise ArtifactIntegrityError("staged original integrity check failed")
        blob_name = f"revisions/{revision_id}/{digest.removeprefix('sha256:')}"
        destination = self._blob(ORIGINALS_CONTAINER, blob_name)
        owner_token = _copy_owner_token(staging_key, revision_id, digest)
        if await self._promotion_call(lambda: self._bounded(destination.exists()), deadline):
            if await self._resolve_copy_destination(
                destination,
                owner_token,
                source,
                expected_size=expected_size,
                deadline=deadline,
            ):
                await self._promotion_call(lambda: self._delete_if_exists(source), deadline)
                return artifact_locator(ORIGINALS_CONTAINER, blob_name)

        copy_id: str | None = None
        try:
            result = await self._promotion_call(
                lambda: self._bounded(
                    destination.start_copy_from_url(
                        self._source_copy_url(source),
                        match_condition=MatchConditions.IfMissing,
                        metadata={
                            "blobsha256": digest.removeprefix("sha256:"),
                            "copyowner": owner_token,
                            "size": str(expected_size),
                        },
                    )
                ),
                deadline,
            )
            copy_id = result.get("copy_id") if isinstance(result, Mapping) else None
            if not await self._resolve_copy_destination(
                destination,
                owner_token,
                source,
                expected_size=expected_size,
                expected_copy_id=copy_id,
                deadline=deadline,
            ):
                raise ArtifactIntegrityError("server-side original copy did not succeed")
        except (ResourceExistsError, ResourceModifiedError):
            if await self._resolve_copy_destination(
                destination,
                owner_token,
                source,
                expected_size=expected_size,
                deadline=deadline,
            ):
                await self._promotion_call(lambda: self._delete_if_exists(source), deadline)
                return artifact_locator(ORIGINALS_CONTAINER, blob_name)
            raise ArtifactIntegrityError("server-side original copy did not succeed") from None
        except asyncio.CancelledError as cancellation:
            settlement_deadline = deadline + min(
                _COPY_RECOVERY_PROBE_TIMEOUT_SECONDS,
                self._config.operation_timeout_seconds,
            )
            await self._cleanup(
                lambda: self._abort_and_delete(
                    destination, owner_token, copy_id, deadline=settlement_deadline
                ),
                timeout_seconds=max(0.0, settlement_deadline - asyncio.get_running_loop().time()),
            )
            raise cancellation
        except Exception as error:
            recovery_failure: ArtifactProviderUnavailable | None = None
            recovery_integrity: ArtifactIntegrityError | None = None
            settlement_deadline = deadline + min(
                _COPY_RECOVERY_PROBE_TIMEOUT_SECONDS,
                self._config.operation_timeout_seconds,
            )
            try:
                recovered = await self._resolve_copy_destination(
                    destination,
                    owner_token,
                    source,
                    expected_size=expected_size,
                    expected_copy_id=copy_id,
                    deadline=settlement_deadline,
                    content_deadline=deadline,
                    wait_for_pending=False,
                )
            except ArtifactProviderUnavailable as unavailable:
                recovered = False
                recovery_failure = unavailable
            except ResourceNotFoundError:
                recovered = False
                recovery_integrity = ArtifactIntegrityError(
                    "server-side original copy destination is missing"
                )
            except ArtifactIntegrityError as integrity:
                recovered = False
                recovery_integrity = integrity
            except Exception:
                recovered = False
                recovery_failure = ArtifactProviderUnavailable(
                    "server-side original copy recovery failed"
                )
            if recovered:
                await self._promotion_call(lambda: self._delete_if_exists(source), deadline)
                return artifact_locator(ORIGINALS_CONTAINER, blob_name)
            if recovery_failure is not None:
                raise recovery_failure
            if recovery_integrity is not None:
                raise recovery_integrity
            if isinstance(error, ArtifactProviderUnavailable):
                raise error
            if isinstance(error, ArtifactIntegrityError):
                raise error
            if isinstance(error, ResourceNotFoundError):
                raise ArtifactIntegrityError(
                    "server-side original copy source does not exist"
                ) from error
            raise ArtifactProviderUnavailable("server-side original copy failed") from None
        await self._promotion_call(lambda: self._delete_if_exists(source), deadline)
        return artifact_locator(ORIGINALS_CONTAINER, blob_name)

    async def _promotion_call(
        self,
        operation: Callable[[], Awaitable[_R]],
        deadline: float,
    ) -> _R:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ArtifactProviderUnavailable(
                "server-side original copy exceeded its client deadline"
            )
        try:
            async with asyncio.timeout(remaining):
                return await operation()
        except TimeoutError:
            raise ArtifactProviderUnavailable(
                "server-side original copy exceeded its client deadline"
            ) from None

    def _source_copy_url(self, source: BlobClient) -> str:
        credential = self._service.credential
        account_key = getattr(credential, "account_key", None)
        if not isinstance(account_key, str) or not account_key:
            raise ArtifactProviderUnavailable("server-side copy credential is unavailable")
        account_name = self._service.account_name
        if not isinstance(account_name, str) or not account_name:
            raise ArtifactProviderUnavailable("server-side copy account identity is unavailable")
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

    async def _wait_copy_terminal(
        self,
        blob: BlobClient,
        owner_token: str,
        expected_copy_id: str | None,
        *,
        deadline: float | None = None,
    ):  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        terminal_deadline = (
            loop.time() + self._config.operation_timeout_seconds if deadline is None else deadline
        )
        while True:
            remaining = terminal_deadline - loop.time()
            if remaining <= 0:
                raise ArtifactProviderUnavailable(
                    "server-side original copy exceeded its client deadline"
                )
            properties = await self._promotion_call(
                lambda: self._bounded(blob.get_blob_properties(), timeout_seconds=remaining),
                terminal_deadline,
            )
            try:
                copy_owner = _metadata_copy_owner(properties.metadata)
                copy = properties.copy
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ArtifactIntegrityError(
                    "server-side original copy state is malformed"
                ) from error
            if copy_owner != owner_token:
                raise ArtifactIntegrityError("server-side original copy ownership changed")
            status = getattr(copy, "status", None)
            actual_copy_id = getattr(copy, "id", None) or getattr(copy, "copy_id", None)
            if expected_copy_id is not None and actual_copy_id != expected_copy_id:
                raise ArtifactIntegrityError("server-side original copy identity changed")
            if status in {"success", "failed", "aborted"}:
                return properties
            if status != "pending":
                raise ArtifactIntegrityError(
                    "server-side original copy did not reach terminal success"
                )
            await asyncio.sleep(
                min(
                    self._config.copy_poll_seconds,
                    max(0.0, terminal_deadline - loop.time()),
                )
            )

    async def _resolve_copy_destination(
        self,
        blob: BlobClient,
        owner_token: str,
        source: BlobClient,
        *,
        expected_size: int,
        expected_copy_id: str | None = None,
        deadline: float | None = None,
        content_deadline: float | None = None,
        wait_for_pending: bool = True,
    ) -> bool:
        terminal_deadline = (
            asyncio.get_running_loop().time() + self._config.operation_timeout_seconds
            if deadline is None
            else deadline
        )
        properties = await self._promotion_call(
            lambda: self._bounded(blob.get_blob_properties()),
            terminal_deadline,
        )
        try:
            owner = _metadata_copy_owner_optional(properties.metadata)
            copy = properties.copy
            status = getattr(copy, "status", None)
            actual_copy_id = getattr(copy, "id", None) or getattr(copy, "copy_id", None)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ArtifactIntegrityError("server-side original copy state is malformed") from error
        if owner == owner_token:
            if expected_copy_id is not None and actual_copy_id != expected_copy_id:
                raise ArtifactIntegrityError("server-side original copy identity changed")
            if status == "pending":
                if not wait_for_pending:
                    return False
                properties = await self._wait_copy_terminal(
                    blob,
                    owner_token,
                    expected_copy_id or actual_copy_id,
                    deadline=terminal_deadline,
                )
                status = getattr(properties.copy, "status", None)
            if status in {"failed", "aborted"}:
                await self._promotion_call(
                    lambda: self._delete_if_exists(blob),
                    terminal_deadline,
                )
                return False
            if status != "success":
                raise ArtifactIntegrityError("server-side original copy state is malformed")
        elif status not in {None, "success"}:
            raise ArtifactIntegrityError("unowned original copy is not terminal")
        verification_deadline = terminal_deadline if content_deadline is None else content_deadline
        if asyncio.get_running_loop().time() >= verification_deadline:
            return False
        destination_content = await self._promotion_call(
            lambda: self._download_verified(blob), verification_deadline
        )
        source_content = await self._promotion_call(
            lambda: self._download(source), verification_deadline
        )
        try:
            destination_size = properties.size
        except AttributeError as error:
            raise ArtifactIntegrityError("server-side original copy state is malformed") from error
        if destination_size != expected_size or destination_content != source_content:
            raise ArtifactIntegrityError("content-addressed original conflicts with existing data")
        return True

    async def _abort_and_delete(
        self,
        blob: BlobClient,
        owner_token: str,
        copy_id: str | None,
        *,
        deadline: float | None = None,
    ) -> None:
        cleanup_deadline = (
            asyncio.get_running_loop().time() + self._config.operation_timeout_seconds
            if deadline is None
            else deadline
        )
        try:
            properties = await self._promotion_call(
                lambda: self._bounded(blob.get_blob_properties()),
                cleanup_deadline,
            )
            if _metadata_copy_owner_optional(properties.metadata) != owner_token:
                return
            actual_copy_id = getattr(properties.copy, "id", None) or getattr(
                properties.copy, "copy_id", None
            )
            if copy_id is not None and actual_copy_id != copy_id:
                return
            if getattr(properties.copy, "status", None) == "pending":
                if not isinstance(actual_copy_id, str) or not actual_copy_id:
                    return
                await self._promotion_call(
                    lambda: self._bounded(blob.abort_copy(actual_copy_id)),
                    cleanup_deadline,
                )
                properties = await self._promotion_call(
                    lambda: self._bounded(blob.get_blob_properties()),
                    cleanup_deadline,
                )
        except Exception:
            return
        if getattr(properties.copy, "status", None) in {"failed", "aborted"}:
            await self._promotion_call(
                lambda: self._delete_if_exists(blob),
                cleanup_deadline,
            )

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
                    overwrite=False,
                    metadata={"blobsha256": digest.removeprefix("sha256:"), "size": str(len(data))},
                    content_settings=ContentSettings(content_type=content_type),
                )
            )
        except ResourceExistsError:
            if await self._download_verified(blob) == data:
                return artifact_locator(ARTIFACTS_CONTAINER, blob_name)
            raise ArtifactIntegrityError(
                "immutable artifact conflicts with existing data"
            ) from None
        except asyncio.CancelledError:
            raise
        except ArtifactProviderUnavailable:
            raise
        except Exception as error:
            if isinstance(error, ArtifactIntegrityError):
                raise error
            raise ArtifactProviderUnavailable("artifact Blob write failed") from error
        if await self._download_verified(blob) != data:
            raise ArtifactIntegrityError("artifact Blob readback mismatch")
        return artifact_locator(ARTIFACTS_CONTAINER, blob_name)

    async def _download_verified(self, blob: BlobClient) -> bytes:
        try:
            properties = await self._bounded(blob.get_blob_properties())
            data = await self._download(blob)
        except ResourceNotFoundError as error:
            raise ArtifactIntegrityError("artifact Blob does not exist") from error
        except asyncio.CancelledError:
            raise
        except ArtifactProviderUnavailable:
            raise
        except ArtifactIntegrityError:
            raise
        except Exception as error:
            raise ArtifactProviderUnavailable("Blob provider read failed") from error
        try:
            if properties.size != len(data) or _metadata_size(properties.metadata) != len(data):
                raise ValueError
            if canonical_sha256(data) != _metadata_digest(properties.metadata):
                raise ValueError
            return data
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ArtifactIntegrityError("Blob content hash verification failed") from error

    async def _download(self, blob: BlobClient) -> bytes:
        stream = await self._bounded(blob.download_blob(max_concurrency=1))
        data = await self._bounded(stream.readall())
        if not isinstance(data, bytes) or len(data) > _MAX_ARTIFACT_BYTES:
            raise ArtifactIntegrityError("Blob download exceeds the artifact bound")
        return data

    async def _staging_properties(self, staging_key: str):  # type: ignore[no-untyped-def]
        self._require_staging_scope(staging_key)
        return await self._bounded(
            self._blob(ORIGINALS_CONTAINER, staging_key).get_blob_properties()
        )

    async def _delete_if_exists(
        self,
        blob: BlobClient,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        try:
            await self._bounded(
                blob.delete_blob(delete_snapshots="include"),
                timeout_seconds=timeout_seconds,
            )
        except ResourceNotFoundError:
            return
        except asyncio.CancelledError:
            raise
        except ArtifactProviderUnavailable:
            raise
        except Exception as error:
            raise ArtifactProviderUnavailable("Azure Blob delete failed") from error

    async def _bounded(  # type: ignore[no-untyped-def]
        self, operation, *, timeout_seconds: float | None = None
    ):
        self._ensure_open()
        task = asyncio.ensure_future(operation)
        timeout = (
            self._config.operation_timeout_seconds
            if timeout_seconds is None
            else min(timeout_seconds, self._config.operation_timeout_seconds)
        )
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.shield(task)
        except TimeoutError:
            task.cancel()
            await _wait_terminal(
                task,
                timeout_seconds=self._terminal_settlement_timeout(),
            )
            raise ArtifactProviderUnavailable(
                "Azure Blob provider operation exceeded deadline"
            ) from None
        except asyncio.CancelledError as cancellation:
            caller = asyncio.current_task()
            if task.done() and task.cancelled() and caller is not None and not caller.cancelling():
                await _wait_terminal(
                    task,
                    timeout_seconds=self._terminal_settlement_timeout(),
                )
                raise ArtifactProviderUnavailable(
                    "Azure Blob provider operation was cancelled"
                ) from None
            task.cancel()
            await _wait_terminal(
                task,
                timeout_seconds=self._terminal_settlement_timeout(),
            )
            raise cancellation

    def _terminal_settlement_timeout(self) -> float:
        timeout = min(
            _COPY_RECOVERY_PROBE_TIMEOUT_SECONDS,
            self._config.operation_timeout_seconds,
        )
        active_deadline = _ACTIVE_COPY_SETTLEMENT_DEADLINE.get()
        if active_deadline is None:
            return timeout
        return max(
            0.0,
            min(timeout, active_deadline - asyncio.get_running_loop().time()),
        )

    async def _cleanup(  # type: ignore[no-untyped-def]
        self,
        operation_factory,
        *,
        timeout_seconds: float | None = None,
    ):
        try:
            await self._bounded(operation_factory(), timeout_seconds=timeout_seconds)
        except (asyncio.CancelledError, Exception):
            return

    def _blob(self, container: str, blob_name: str) -> BlobClient:
        self._ensure_open()
        return self._service.get_blob_client(container, blob_name)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ArtifactProviderUnavailable("Azure Blob artifact store is closed")


async def _wait_terminal(
    task: asyncio.Future[Any],
    *,
    timeout_seconds: float,
) -> None:
    if not task.done() and timeout_seconds > 0:
        try:
            async with asyncio.timeout(timeout_seconds):
                await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    if not task.done():
        task.add_done_callback(_consume_terminal_result)
        return
    _consume_terminal_result(task)


def _consume_terminal_result(task: asyncio.Future[Any]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


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
    if not value.startswith("staging/") or value.count("/") not in {1, 2}:
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
    return _persisted_identity("revision identity", parts[1])


def _metadata_digest(metadata: Mapping[str, str]) -> str:
    value = metadata.get("blobsha256")
    return _digest("sha256:" + value if isinstance(value, str) else value)


def _copy_owner_token(staging_key: str, revision_id: str, digest: str) -> str:
    return sha256(
        f"tapper-original-copy-v1\0{staging_key}\0{revision_id}\0{digest}".encode()
    ).hexdigest()


def _metadata_copy_owner_optional(metadata: Mapping[str, str]) -> str | None:
    value = metadata.get("copyowner")
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ArtifactIntegrityError("original copy ownership metadata is malformed")
    return value


def _metadata_copy_owner(metadata: Mapping[str, str]) -> str:
    value = _metadata_copy_owner_optional(metadata)
    if value is None:
        raise ArtifactIntegrityError("original copy ownership metadata is missing")
    return value


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
