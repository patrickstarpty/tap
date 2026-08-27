"""Durable generation authority for mutable knowledge projections."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ProjectionOwnershipReceipt:
    physical_collection: str
    operation_id: str
    predecessor_collection: str
    status: Literal["building", "active", "cleanup"]


class ProjectionMutationLease(Protocol):
    """One cross-process exclusive mutation lease for a stable projection alias."""

    async def state(self) -> tuple[int, str | None]: ...

    async def initialize(self, physical: str) -> tuple[int, str]: ...

    async def is_fenced(self, revision_id: str) -> bool: ...

    async def record_fence(self, revision_id: str, document_id: str) -> None: ...

    async def fences(self, limit: int) -> tuple[tuple[str, str], ...]: ...

    async def reserve_build(
        self,
        physical: str,
        predecessor: str,
        operation_id: str,
    ) -> ProjectionOwnershipReceipt: ...

    async def ownership(self, physical: str) -> ProjectionOwnershipReceipt | None: ...

    async def activate_build(
        self,
        receipt: ProjectionOwnershipReceipt,
    ) -> tuple[int, str]: ...

    async def abandon_build(self, receipt: ProjectionOwnershipReceipt) -> None: ...

    async def owned_cleanup(self, limit: int) -> tuple[ProjectionOwnershipReceipt, ...]: ...

    async def verify_cleanup(self, receipt: ProjectionOwnershipReceipt) -> bool: ...

    async def complete_owned_cleanup(self, receipt: ProjectionOwnershipReceipt) -> None: ...


class ProjectionMutationCoordinator(Protocol):
    """Provider-neutral durable mutex, fence ledger, and cleanup owner."""

    def mutation(
        self,
        alias: str,
    ) -> AbstractAsyncContextManager[ProjectionMutationLease]: ...

    async def close(self) -> None: ...
