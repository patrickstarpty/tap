"""In-memory SuggestionStore double for unit tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from tap.modules.chat.application.suggestion_ports import ClaimedRefresh
from tap.modules.chat.domain.suggestions import (
    DAILY_REFRESH,
    REFRESH_THROTTLE,
    PromptSuggestion,
    RefreshReason,
    SuggestionKey,
    next_due_at,
)


@dataclass
class _Refresh:
    due_at: datetime | None = None
    last_reason: RefreshReason | None = None
    last_refreshed_at: datetime | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    claimed_due_at: datetime | None = None
    attempt_count: int = 0
    failure_code: str | None = None


class InMemorySuggestionStore:
    def __init__(self) -> None:
        self._refresh: dict[SuggestionKey, _Refresh] = {}
        self._suggestions: dict[SuggestionKey, tuple[PromptSuggestion, ...]] = {}

    async def load(self, key: SuggestionKey) -> tuple[PromptSuggestion, ...] | None:
        row = self._refresh.get(key)
        if row is None or row.last_refreshed_at is None:
            return None
        return self._suggestions.get(key, ())

    async def request_refresh(
        self, key: SuggestionKey, reason: RefreshReason, *, now: datetime
    ) -> None:
        self._apply_request(key, reason, now=now)

    def _apply_request(self, key: SuggestionKey, reason: RefreshReason, *, now: datetime) -> None:
        row = self._refresh.setdefault(key, _Refresh())
        # While a claim is actively leased, due_at is frozen at the claimed value
        # until completion; a fresh request must not be clamped to that stale
        # value, or it would collapse back to the value complete_refresh clears.
        active_lease = row.lease_expires_at is not None and row.lease_expires_at > now
        row.due_at = next_due_at(
            reason,
            now=now,
            last_refreshed_at=row.last_refreshed_at,
            current_due_at=None if active_lease else row.due_at,
        )
        row.last_reason = reason

    async def request_refresh_for_actor(
        self, actor_id: str, reason: RefreshReason, *, now: datetime
    ) -> int:
        touched = 0
        for key in list(self._refresh):
            if key.actor_id == actor_id:
                self._apply_request(key, reason, now=now)
                touched += 1
        return touched

    async def request_refresh_for_project_in_transaction(
        self, session: object, reason: RefreshReason, *, now: datetime
    ) -> int:
        del session
        touched = 0
        for key in list(self._refresh):
            self._apply_request(key, reason, now=now)
            touched += 1
        return touched

    async def request_daily_refresh(self, *, now: datetime) -> int:
        touched = 0
        for key, row in self._refresh.items():
            if (
                row.due_at is None
                and row.last_refreshed_at is not None
                and row.last_refreshed_at < now - DAILY_REFRESH
            ):
                row.due_at = next_due_at(
                    RefreshReason.DAILY,
                    now=now,
                    last_refreshed_at=row.last_refreshed_at,
                    current_due_at=None,
                )
                row.last_reason = RefreshReason.DAILY
                touched += 1
        return touched

    async def claim_due(
        self, *, worker_id: str, now: datetime, limit: int, lease_duration: timedelta
    ) -> tuple[ClaimedRefresh, ...]:
        claims: list[ClaimedRefresh] = []
        for key, row in self._refresh.items():
            if len(claims) >= limit:
                break
            if row.due_at is None or row.due_at > now:
                continue
            if row.lease_expires_at is not None and row.lease_expires_at > now:
                continue
            token = uuid4().hex
            row.lease_owner = worker_id
            row.lease_token = token
            row.lease_expires_at = now + lease_duration
            row.claimed_due_at = row.due_at
            claims.append(
                ClaimedRefresh(key=key, lease_token=token, attempt_count=row.attempt_count)
            )
        return tuple(claims)

    async def complete_refresh(
        self,
        claim: ClaimedRefresh,
        suggestions: tuple[PromptSuggestion, ...],
        *,
        now: datetime,
    ) -> bool:
        row = self._refresh.get(claim.key)
        if row is None or row.lease_token != claim.lease_token:
            return False
        self._suggestions[claim.key] = tuple(suggestions)
        row.last_refreshed_at = now
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.failure_code = None
        if row.due_at == row.claimed_due_at:
            row.due_at = None
        row.claimed_due_at = None
        return True

    async def fail_refresh(
        self, claim: ClaimedRefresh, failure_code: str, *, now: datetime
    ) -> bool:
        row = self._refresh.get(claim.key)
        if row is None or row.lease_token != claim.lease_token:
            return False
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.claimed_due_at = None
        row.attempt_count += 1
        row.failure_code = failure_code
        row.due_at = now + REFRESH_THROTTLE
        return True
