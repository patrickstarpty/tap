"""Read-time filtering and model-backed refresh for prompt suggestions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from tap.modules.chat.application.suggestion_ports import (
    CurrentSource,
    GroundingCheck,
    SuggestionGenerator,
    SuggestionKnowledge,
    SuggestionStore,
    SuggestionUsage,
)
from tap.modules.chat.domain.suggestions import (
    MAX_CANDIDATES,
    MAX_SOURCES_PER_SUGGESTION,
    Candidate,
    PromptSuggestion,
    RefreshReason,
    SuggestionInputs,
    SuggestionKey,
    SuggestionSource,
    is_displayable_question,
)

_PERSONAL_LIMIT = 20
_ENTITY_LIMIT = 20
_POPULAR_LIMIT = 10
_MAX_QUESTION_LENGTH = 500


@dataclass(frozen=True, slots=True)
class SuggestionView:
    suggestion_id: str
    question: str
    sources: tuple[CurrentSource, ...]


class NoGroundedCandidates(Exception):
    """Raised by PromptSuggestionService.refresh when the knowledge base was
    non-empty and generation produced candidates, but none passed grounding.

    This is distinct from the "no knowledge" case (refresh() returns () when
    there are no topics) so SuggestionRefreshWorker can avoid overwriting a
    previously good, non-empty cache with an empty one.
    """


class PromptSuggestionService:
    def __init__(
        self,
        *,
        store: SuggestionStore,
        knowledge: SuggestionKnowledge,
        usage: SuggestionUsage,
        generator: SuggestionGenerator,
        grounding: GroundingCheck,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._usage = usage
        self._generator = generator
        self._grounding = grounding
        self._id_factory = id_factory
        self._clock = clock

    async def list(self, key: SuggestionKey) -> tuple[SuggestionView, ...]:
        now = self._clock()
        stored = await self._store.load(key)
        if stored is None:
            await self._store.request_refresh(key, RefreshReason.MISSING, now=now)
            return ()

        current_sources = await self._knowledge.current_sources(key.actor_id)
        kept: list[SuggestionView] = []
        any_filtered = False
        for suggestion in stored:
            resolved: list[CurrentSource] = []
            for source in suggestion.sources:
                current_source = current_sources.get(source.source_id)
                if current_source is None or current_source.version != source.version:
                    resolved = []
                    break
                resolved.append(current_source)
            if resolved and len(resolved) == len(suggestion.sources):
                kept.append(
                    SuggestionView(
                        suggestion_id=suggestion.suggestion_id,
                        question=suggestion.question,
                        sources=tuple(resolved),
                    )
                )
            else:
                any_filtered = True

        if any_filtered:
            await self._store.request_refresh(key, RefreshReason.FILTERED, now=now)

        return tuple(kept)

    async def refresh(self, key: SuggestionKey) -> tuple[PromptSuggestion, ...]:
        topics = await self._knowledge.topics(key.actor_id)
        if not topics:
            return ()

        entities = await self._knowledge.main_entities(key.actor_id, limit=_ENTITY_LIMIT)
        personal_questions, personal_source_ids = await self._usage.personal(
            key.actor_id, limit=_PERSONAL_LIMIT
        )
        popular_sources = await self._usage.popular_sources(
            excluding_actor_id=key.actor_id, limit=_POPULAR_LIMIT
        )
        inputs = SuggestionInputs(
            topics=topics,
            entities=entities,
            personal_questions=personal_questions,
            personal_source_ids=personal_source_ids,
            popular_sources=popular_sources,
        )
        candidates = await self._generator.generate(inputs, key.locale)

        topic_versions = {topic.source_id: topic.version for topic in topics}
        seen_questions: set[str] = set()
        valid_candidates: list[Candidate] = []
        for candidate in candidates:
            question = candidate.question.strip()
            if not question or len(question) > _MAX_QUESTION_LENGTH:
                continue
            if not is_displayable_question(question, key.locale):
                continue
            if not (1 <= len(candidate.source_ids) <= MAX_SOURCES_PER_SUGGESTION):
                continue
            if any(source_id not in topic_versions for source_id in candidate.source_ids):
                continue
            dedupe_key = question.lower()
            if dedupe_key in seen_questions:
                continue
            seen_questions.add(dedupe_key)
            valid_candidates.append(Candidate(question=question, source_ids=candidate.source_ids))
            if len(valid_candidates) >= MAX_CANDIDATES:
                break

        suggestions: list[PromptSuggestion] = []
        for candidate in valid_candidates:
            grounded = await self._grounding.is_grounded(
                key.actor_id, candidate.question, candidate.source_ids
            )
            if not grounded:
                continue
            suggestions.append(
                PromptSuggestion(
                    suggestion_id=self._id_factory(),
                    question=candidate.question,
                    sources=tuple(
                        SuggestionSource(source_id=source_id, version=topic_versions[source_id])
                        for source_id in candidate.source_ids
                    ),
                )
            )

        if not suggestions and valid_candidates:
            raise NoGroundedCandidates(key)

        return tuple(suggestions)


class SuggestionRefreshWorker:
    def __init__(
        self,
        *,
        store: SuggestionStore,
        service: PromptSuggestionService,
        worker_id: str,
        clock: Callable[[], datetime],
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> None:
        self._store = store
        self._service = service
        self._worker_id = worker_id
        self._clock = clock
        self._lease_duration = lease_duration

    async def run_once(self, *, limit: int) -> int:
        now = self._clock()
        await self._store.request_daily_refresh(now=now)
        claims = await self._store.claim_due(
            worker_id=self._worker_id,
            now=now,
            limit=limit,
            lease_duration=self._lease_duration,
        )

        processed = 0
        for claim in claims:
            try:
                suggestions = await self._service.refresh(claim.key)
            except NoGroundedCandidates:
                previous = await self._store.load(claim.key)
                if previous:
                    await self._store.fail_refresh(
                        claim, "no_grounded_candidates", now=self._clock()
                    )
                else:
                    await self._store.complete_refresh(claim, (), now=self._clock())
                processed += 1
                continue
            except Exception as exc:  # noqa: BLE001 - recorded as a failure code, not logged
                await self._store.fail_refresh(claim, type(exc).__name__, now=self._clock())
                processed += 1
                continue
            await self._store.complete_refresh(claim, suggestions, now=self._clock())
            processed += 1

        return processed
