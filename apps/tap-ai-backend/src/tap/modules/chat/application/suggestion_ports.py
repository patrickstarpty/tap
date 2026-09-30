"""Prompt suggestion store port. Other ports (topics, source versions, evidence
validation) are added in Task 3 by their owning adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tap.modules.chat.domain.suggestions import PromptSuggestion, RefreshReason, SuggestionKey


@dataclass(frozen=True, slots=True)
class ClaimedRefresh:
    key: SuggestionKey
    lease_token: str
    attempt_count: int


class SuggestionStore(Protocol):
    async def load(self, key: SuggestionKey) -> tuple[PromptSuggestion, ...] | None:
        """None means this key has never completed a refresh."""
        ...

    async def request_refresh(
        self, key: SuggestionKey, reason: RefreshReason, *, now: datetime
    ) -> None: ...

    async def request_refresh_for_actor(
        self, actor_id: str, reason: RefreshReason, *, now: datetime
    ) -> int:
        """Only updates rows that already exist for this actor; returns rows touched."""
        ...

    async def request_refresh_for_project_in_transaction(
        self, session: AsyncSession, reason: RefreshReason, *, now: datetime
    ) -> int:
        """Runs inside a caller-managed transaction (e.g. knowledge publish)."""
        ...

    async def request_daily_refresh(self, *, now: datetime) -> int:
        """Marks rows whose last_refreshed_at is older than DAILY_REFRESH and have
        no due_at pending; returns rows touched."""
        ...

    async def claim_due(
        self, *, worker_id: str, now: datetime, limit: int, lease_duration: timedelta
    ) -> tuple[ClaimedRefresh, ...]: ...

    async def complete_refresh(
        self,
        claim: ClaimedRefresh,
        suggestions: tuple[PromptSuggestion, ...],
        *,
        now: datetime,
    ) -> bool: ...

    async def fail_refresh(
        self, claim: ClaimedRefresh, failure_code: str, *, now: datetime
    ) -> bool: ...
