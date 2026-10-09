"""Prompt suggestion store port, plus the knowledge/usage/generation/grounding
ports PromptSuggestionService depends on. Adapters for the knowledge-backed
ports live in entrypoints; the usage port is implemented by a chat adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tap.modules.chat.domain.suggestions import (
    Candidate,
    MainRelation,
    PromptSuggestion,
    RefreshReason,
    SuggestionInputs,
    SuggestionKey,
    SuggestionLocale,
    TopicSource,
)


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


@dataclass(frozen=True, slots=True)
class CurrentSource:
    source_id: str
    name: str
    version: str


class SuggestionKnowledge(Protocol):
    async def current_sources(self, actor_id: str) -> Mapping[str, CurrentSource]:
        """Published sources this actor can currently access."""
        ...

    async def topics(self, actor_id: str) -> tuple[TopicSource, ...]:
        """At most 30 sources, each with at most 8 section headings."""
        ...

    async def main_entities(self, actor_id: str, *, limit: int) -> tuple[str, ...]: ...

    async def main_relations(self, actor_id: str, *, limit: int) -> tuple[MainRelation, ...]: ...


class SuggestionUsage(Protocol):
    async def personal(
        self, actor_id: str, *, limit: int
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(question texts, source ids)."""
        ...

    async def popular_sources(
        self, *, excluding_actor_id: str, limit: int
    ) -> tuple[tuple[str, int], ...]: ...


class SuggestionGenerator(Protocol):
    async def generate(
        self, inputs: SuggestionInputs, locale: SuggestionLocale
    ) -> tuple[Candidate, ...]: ...


class GroundingCheck(Protocol):
    async def is_grounded(
        self, actor_id: str, question: str, source_ids: tuple[str, ...]
    ) -> bool: ...
