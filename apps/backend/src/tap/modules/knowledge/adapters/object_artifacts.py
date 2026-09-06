"""Canonical Knowledge artifacts composed over provider-neutral object bytes."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from datetime import datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.artifact_codecs import (
    _persisted_identity,
    decode_chunks_artifact,
    decode_embeddings_artifact,
    decode_normalized_artifact,
    encode_chunks_artifact,
    encode_embeddings_artifact,
    encode_normalized_artifact,
)
from tap.modules.knowledge.adapters.blob_artifacts import (
    AzureBlobArtifactStore,
    _parse_locator,
    _revision_from_artifact_name,
)
from tap.modules.knowledge.domain.documents import ChunkDraft, NormalizedArtifact
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    ArtifactScavengeReceipt,
    DeletionTarget,
    EmbeddingArtifact,
    StagedOriginal,
    UploadStream,
)
from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure, ArtifactUnavailable
from tap.platform.storage.objects import (
    ManagedObjectStore,
    ObjectDescriptor,
    ObjectIntegrityError,
    ObjectMissingError,
    ObjectRef,
    ObjectUnavailable,
    PutObjectRequest,
    StagedObject,
    StagingRef,
    VerifiedObject,
)

_P = ParamSpec("_P")
_T = TypeVar("_T")
_KINDS = frozenset({"original", "normalized", "chunks", "embeddings"})


def _boundary(operation: Callable[_P, Awaitable[_T]]) -> Callable[_P, Coroutine[Any, Any, _T]]:
    @wraps(operation)
    async def call(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return await operation(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except (ObjectIntegrityError, ArtifactIntegrityFailure):
            raise ArtifactIntegrityFailure("artifact integrity verification failed") from None
        except (ObjectUnavailable, ArtifactUnavailable):
            raise ArtifactUnavailable("artifact provider operation failed") from None

    return call


def _locator(revision: str, kind: str, ref: ObjectRef) -> ArtifactLocator:
    payload = json.dumps([revision, kind, str(ref)], separators=(",", ":")).encode()
    return ArtifactLocator("art1." + base64.urlsafe_b64encode(payload).decode().rstrip("="))


def _parse(locator: ArtifactLocator) -> tuple[str, str, ObjectRef]:
    try:
        if (
            not isinstance(locator, ArtifactLocator)
            or not locator.startswith("art1.")
            or len(locator) > 1024
        ):
            raise ValueError
        encoded = str(locator).removeprefix("art1.")
        payload = json.loads(
            base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(payload, list) or len(payload) != 3:
            raise ValueError
        revision, kind, ref = payload
        _persisted_identity("revision", revision)
        if kind not in _KINDS or not isinstance(ref, str) or not ref.startswith("obj1."):
            raise ValueError
        value = ObjectRef(ref)
        if _locator(revision, kind, value) != locator:
            raise ValueError
        return revision, kind, value
    except (ValueError, TypeError, KeyError):
        raise ArtifactIntegrityFailure("artifact reference is malformed") from None


class KnowledgeArtifactStore:
    def __init__(
        self, objects: ManagedObjectStore, *, legacy: AzureBlobArtifactStore | None = None
    ) -> None:
        if legacy is not None and legacy.scope != objects.scope:
            raise ValueError("artifact provider scope differs")
        self.objects = objects
        self.legacy = legacy

    @property
    def scope(self) -> ProjectScopeContext:
        return self.objects.scope

    async def ensure_containers(self) -> None:
        await self.objects.ensure_bucket()

    async def is_private(self) -> bool:
        return await self.objects.is_private()

    async def aclose(self) -> None:
        try:
            await self.objects.aclose()
        finally:
            if self.legacy is not None:
                await self.legacy.aclose()

    def _legacy(self, locator: ArtifactLocator) -> AzureBlobArtifactStore:
        try:
            _parse_locator(locator)
        except (ValueError, TypeError):
            raise ArtifactIntegrityFailure("unknown artifact reference") from None
        if self.legacy is None:
            raise ArtifactUnavailable("legacy artifact provider is not enabled")
        return self.legacy

    def _legacy_staging(self, staging: str) -> AzureBlobArtifactStore:
        if not isinstance(staging, str) or not staging.startswith("staging/"):
            raise ArtifactIntegrityFailure("unknown staged artifact reference")
        if self.legacy is None:
            raise ArtifactUnavailable("legacy artifact provider is not enabled")
        return self.legacy

    @_boundary
    async def stage_original(self, upload: UploadStream, *, max_bytes: int) -> StagedOriginal:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 25 * 1024 * 1024:
            raise ValueError("original upload byte bound is invalid")
        staged = await self.objects.put_staged(
            PutObjectRequest(upload.content, max_bytes, upload.media_type)
        )
        return StagedOriginal(
            str(staged.ref), upload.filename, upload.media_type, staged.size, staged.sha256
        )

    @_boundary
    async def commit_original(self, staged: StagedOriginal, revision_id: str) -> ArtifactLocator:
        revision_id = _persisted_identity("revision", revision_id)
        value = StagedObject(
            StagingRef(staged.staging_key),
            staged.source_content_hash,
            staged.size,
            staged.media_type,
        )
        ref = await self.objects.promote(
            value,
            staged.source_content_hash,
            identity=f"{revision_id}/original",
            attributes={"revision": revision_id, "kind": "original"},
        )
        return _locator(revision_id, "original", ref)

    @_boundary
    async def recover_original(self, staging_key: str, revision_id: str) -> ArtifactLocator:
        if not staging_key.startswith("stg1."):
            return await self._legacy_staging(staging_key).recover_original(
                staging_key, revision_id
            )
        value = await self.objects.open_verified(StagingRef(staging_key))
        return await self.commit_original(
            StagedOriginal(staging_key, "recovered", value.content_type, value.size, value.sha256),
            revision_id,
        )

    @_boundary
    async def discard_staged(self, staged: StagedOriginal) -> None:
        await self.discard_staging(staged.staging_key)

    @_boundary
    async def discard_staging(self, staging_key: str) -> None:
        if not staging_key.startswith("stg1."):
            await self._legacy_staging(staging_key).discard_staging(staging_key)
            return
        await self.objects.delete(StagingRef(staging_key))

    async def _read(self, locator: ArtifactLocator, kind: str) -> tuple[str, VerifiedObject]:
        revision, actual_kind, ref = _parse(locator)
        if kind != actual_kind:
            raise ArtifactIntegrityFailure("artifact kind differs")
        value = await self.objects.open_verified(ref)
        self._validate_binding(value, revision, kind)
        return revision, value

    @staticmethod
    def _validate_binding(
        value: ObjectDescriptor | VerifiedObject, revision: str, kind: str
    ) -> None:
        if dict(value.attributes) != {
            "revision": revision,
            "kind": kind,
        } or not value.identity.startswith(revision + "/" + kind):
            raise ArtifactIntegrityFailure("artifact manifest binding differs")

    @_boundary
    async def read_original(self, locator: ArtifactLocator) -> bytes:
        if not locator.startswith("art1."):
            return await self._legacy(locator).read_original(locator)
        return (await self._read(locator, "original"))[1].data

    async def _write(
        self, revision: str, kind: str, data: bytes, content_type: str, slot: str = ""
    ) -> ArtifactLocator:
        revision = _persisted_identity("revision", revision)

        async def content():  # type: ignore[no-untyped-def]
            yield data

        staged = await self.objects.put_staged(
            PutObjectRequest(content(), 64 * 1024 * 1024, content_type)
        )
        ref = await self.objects.promote(
            staged,
            staged.sha256,
            identity=f"{revision}/{kind}{slot}",
            attributes={"revision": revision, "kind": kind},
        )
        await self.objects.delete(staged.ref)
        return _locator(revision, kind, ref)

    @_boundary
    async def write_normalized(
        self, revision_id: str, artifact: NormalizedArtifact
    ) -> ArtifactLocator:
        return await self._write(
            revision_id,
            "normalized",
            encode_normalized_artifact(revision_id, artifact),
            "application/json",
        )

    @_boundary
    async def read_normalized(self, locator: ArtifactLocator) -> NormalizedArtifact:
        if not locator.startswith("art1."):
            return await self._legacy(locator).read_normalized(locator)
        revision, value = await self._read(locator, "normalized")
        return decode_normalized_artifact(value.data, expected_revision=revision)

    @_boundary
    async def write_chunks(
        self, revision_id: str, chunks: tuple[ChunkDraft, ...]
    ) -> ArtifactLocator:
        return await self._write(
            revision_id, "chunks", encode_chunks_artifact(revision_id, chunks), "application/gzip"
        )

    @_boundary
    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]:
        if not locator.startswith("art1."):
            return await self._legacy(locator).read_chunks(locator)
        revision, value = await self._read(locator, "chunks")
        return decode_chunks_artifact(value.data, expected_revision=revision)

    @_boundary
    async def write_embeddings(
        self, revision_id: str, artifact: EmbeddingArtifact, *, source_content_hash: str
    ) -> ArtifactLocator:
        data = encode_embeddings_artifact(revision_id, source_content_hash, artifact)
        return await self._write(
            revision_id,
            "embeddings",
            data,
            "application/gzip",
            f"/{artifact.model_alias}/{artifact.dimension}",
        )

    @_boundary
    async def read_embeddings(self, locator: ArtifactLocator) -> EmbeddingArtifact:
        if not locator.startswith("art1."):
            return await self._legacy(locator).read_embeddings(locator)
        revision, value = await self._read(locator, "embeddings")
        return decode_embeddings_artifact(value.data, expected_revision=revision)

    @_boundary
    async def delete_revision_artifacts(self, target: DeletionTarget) -> None:
        if not isinstance(target, DeletionTarget):
            raise TypeError("artifact deletion requires an exact target")
        refs: list[tuple[ArtifactLocator, str, ObjectRef]] = []
        legacy_refs: list[ArtifactLocator] = []
        # Validate the complete batch before any provider mutation, including mixed providers.
        for locator in target.artifact_locators:
            if locator.startswith("art1."):
                revision, kind, ref = _parse(locator)
                if revision != target.revision_id:
                    raise ArtifactIntegrityFailure("artifact deletion revision differs")
                refs.append((locator, kind, ref))
            else:
                self._legacy(locator)
                _, name = _parse_locator(locator)
                if _revision_from_artifact_name(name) != target.revision_id:
                    raise ArtifactIntegrityFailure("legacy deletion revision differs")
                legacy_refs.append(locator)
        for _, kind, ref in refs:
            try:
                descriptor = await self.objects.describe_verified(ref)
            except ObjectMissingError:
                continue
            self._validate_binding(descriptor, target.revision_id, kind)
        if legacy_refs and self.legacy is not None:
            await self.legacy.delete_revision_artifacts(
                replace(target, artifact_locators=tuple(legacy_refs))
            )
        for _, _, ref in refs:
            await self.objects.delete(ref)

    @_boundary
    async def scavenge_staging(
        self, *, now: datetime, visible_staging_keys: frozenset[str], limit: int = 100
    ) -> ArtifactScavengeReceipt:
        refs = frozenset(StagingRef(ref) for ref in visible_staging_keys if ref.startswith("stg1."))
        receipt = await self.objects.scavenge_staging(now=now, visible_refs=refs, limit=limit)
        scanned, removed = receipt.scanned, [str(ref) for ref in receipt.removed]
        if self.legacy is not None and scanned < limit:
            old = await self.legacy.scavenge_staging(
                now=now,
                visible_staging_keys=frozenset(
                    ref for ref in visible_staging_keys if ref.startswith("staging/")
                ),
                limit=limit - scanned,
            )
            scanned += old.scanned
            removed.extend(old.removed)
        return ArtifactScavengeReceipt(scanned=scanned, removed=tuple(removed))
