"""Durable generation authority for mutable knowledge projections."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol


class ProjectionMutationLease(Protocol):
    """One cross-process exclusive mutation lease for a stable projection alias."""

    async def state(self) -> tuple[int, str | None]: ...

    async def initialize(self, physical: str) -> tuple[int, str]: ...

    async def is_fenced(self, revision_id: str) -> bool: ...

    async def record_fence(self, revision_id: str, document_id: str) -> None: ...

    async def fences(self, limit: int) -> tuple[tuple[str, str], ...]: ...

    async def activate(self, physical: str) -> tuple[int, str]: ...

    async def enqueue_cleanup(self, physical: str) -> None: ...

    async def pending_cleanup(self, limit: int) -> tuple[str, ...]: ...

    async def complete_cleanup(self, physical: str) -> None: ...


class ProjectionMutationCoordinator(Protocol):
    """Provider-neutral durable mutex, fence ledger, and cleanup owner."""

    def mutation(
        self,
        alias: str,
    ) -> AbstractAsyncContextManager[ProjectionMutationLease]: ...

    async def close(self) -> None: ...
