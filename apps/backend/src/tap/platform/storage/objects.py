"""Bounded bytes and opaque references; no module-specific artifact semantics."""

from collections.abc import AsyncIterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext

MAX_OBJECT_BYTES = 64 * 1024 * 1024


class ObjectRef(str):
    """Opaque immutable reference, never a provider URL or object key."""


class StagingRef(str):
    """Opaque temporary reference whose ownership comes from trusted composition."""


class ObjectIntegrityError(Exception):
    """Persisted bytes or descriptors do not match their expected integrity."""


class ObjectUnavailable(Exception):
    """A provider operation failed or exceeded its bounded deadline."""


class ObjectMissingError(ObjectIntegrityError):
    """A specifically addressed object is absent, allowing idempotent deletion."""


@dataclass(frozen=True, slots=True)
class PutObjectRequest:
    content: AsyncIterable[bytes]
    max_bytes: int
    content_type: str


@dataclass(frozen=True, slots=True)
class StagedObject:
    ref: StagingRef
    sha256: str
    size: int
    content_type: str


@dataclass(frozen=True, slots=True)
class ObjectDescriptor:
    """Ref-rooted manifest metadata; does not attest that payload bytes exist or match."""

    sha256: str
    size: int
    content_type: str
    identity: str
    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class VerifiedObject:
    data: bytes
    sha256: str
    size: int
    content_type: str
    identity: str
    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class StagingScavengeReceipt:
    scanned: int
    removed: tuple[StagingRef, ...]


class ObjectStorePort(Protocol):
    async def put_staged(self, request: PutObjectRequest) -> StagedObject: ...

    async def promote(
        self,
        staged: StagedObject,
        expected_sha256: str,
        *,
        identity: str,
        attributes: Mapping[str, str],
    ) -> ObjectRef: ...

    async def describe_verified(self, ref: ObjectRef) -> ObjectDescriptor: ...

    async def open_verified(self, ref: ObjectRef | StagingRef) -> VerifiedObject: ...

    async def delete(self, ref: ObjectRef | StagingRef) -> None: ...

    async def scavenge_staging(
        self, *, now: datetime, visible_refs: frozenset[StagingRef], limit: int = 100
    ) -> StagingScavengeReceipt: ...


class ManagedObjectStore(ObjectStorePort, Protocol):
    """Infrastructure lifecycle; separate from business object operations."""

    @property
    def scope(self) -> ProjectScopeContext: ...

    async def ensure_bucket(self) -> None: ...

    async def is_private(self) -> bool: ...

    async def aclose(self) -> None: ...
