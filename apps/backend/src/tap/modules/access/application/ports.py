"""Framework-free ports for authoritative access-policy refresh."""

from __future__ import annotations

from typing import Protocol

from tap.modules.access.domain.policy import RetrievalPolicyContext


class CurrentPolicyVerificationPort(Protocol):
    """Return the authoritative current context, or ``None`` when unavailable."""

    async def verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext | None: ...
