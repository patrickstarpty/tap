"""Prompt suggestion domain types and refresh due-time rule."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

SuggestionLocale = Literal["en", "zh"]


class RefreshReason(StrEnum):
    KNOWLEDGE_PUBLISHED = "knowledge_published"
    TURN_COMPLETED = "turn_completed"
    DAILY = "daily"
    FILTERED = "filtered"
    MISSING = "missing"


REFRESH_THROTTLE = timedelta(minutes=10)
DAILY_REFRESH = timedelta(hours=24)

MAX_CANDIDATES = 8
MAX_SOURCES_PER_SUGGESTION = 3

_THROTTLED_REASONS = frozenset({RefreshReason.TURN_COMPLETED, RefreshReason.FILTERED})


@dataclass(frozen=True, slots=True)
class SuggestionKey:
    actor_id: str
    locale: SuggestionLocale


@dataclass(frozen=True, slots=True)
class SuggestionSource:
    source_id: str
    version: str


@dataclass(frozen=True, slots=True)
class PromptSuggestion:
    suggestion_id: str
    question: str
    sources: tuple[SuggestionSource, ...]


@dataclass(frozen=True, slots=True)
class TopicSource:
    source_id: str
    name: str
    version: str
    headings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuggestionInputs:
    topics: tuple[TopicSource, ...]
    entities: tuple[str, ...]
    personal_questions: tuple[str, ...]
    personal_source_ids: tuple[str, ...]
    popular_sources: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    question: str
    source_ids: tuple[str, ...]


def next_due_at(
    reason: RefreshReason,
    *,
    now: datetime,
    last_refreshed_at: datetime | None,
    current_due_at: datetime | None,
) -> datetime:
    if reason in _THROTTLED_REASONS:
        due = now if last_refreshed_at is None else max(now, last_refreshed_at + REFRESH_THROTTLE)
    else:
        due = now
    if current_due_at is not None:
        due = min(due, current_due_at)
    return due


# Questions shown to a user must not expose internal identifiers.
_IDENTIFIER = re.compile(r"\b(?:src|rev|doc)_[0-9a-z]{6,}|[0-9a-f]{16,}", re.IGNORECASE)
# Quoted titles may keep their original language, e.g. a Chinese product name in English.
_QUOTED = re.compile(r"《[^》]*》|“[^”]*”|\"[^\"]*\"|'[^']*'")
_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def is_displayable_question(question: str, locale: SuggestionLocale) -> bool:
    """A suggestion is shown only in the interface language and without internal IDs."""

    if _IDENTIFIER.search(question):
        return False
    body = _QUOTED.sub("", question)
    cjk = len(_CJK.findall(body))
    latin = len(_LATIN.findall(body))
    if locale == "zh":
        return cjk > 0 and cjk * 4 >= latin
    return latin > cjk
