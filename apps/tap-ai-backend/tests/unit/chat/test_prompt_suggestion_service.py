"""PromptSuggestionService read-filter/refresh and SuggestionRefreshWorker,
exercised with InMemorySuggestionStore plus hand-written fake ports."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from tap.modules.chat.adapters.memory_suggestions import InMemorySuggestionStore
from tap.modules.chat.application.suggestion_ports import CurrentSource, SuggestionStore
from tap.modules.chat.application.suggestions import (
    PromptSuggestionService,
    SuggestionRefreshWorker,
)
from tap.modules.chat.domain.suggestions import (
    Candidate,
    PromptSuggestion,
    RefreshReason,
    SuggestionInputs,
    SuggestionKey,
    SuggestionLocale,
    SuggestionSource,
    TopicSource,
)

T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
KEY = SuggestionKey(actor_id="actor-1", locale="en")


def _id_factory() -> Callable[[], str]:
    counter = itertools.count(1)

    def factory() -> str:
        return f"{next(counter):032x}"

    return factory


def _clock(now: datetime = T0) -> Callable[[], datetime]:
    return lambda: now


@dataclass
class FakeKnowledge:
    current_by_actor: dict[str, dict[str, CurrentSource]] = field(default_factory=dict)
    topics_result: tuple[TopicSource, ...] = ()
    entities_result: tuple[str, ...] = ()

    async def current_sources(self, actor_id: str) -> Mapping[str, CurrentSource]:
        return self.current_by_actor.get(actor_id, {})

    async def topics(self, actor_id: str) -> tuple[TopicSource, ...]:
        return self.topics_result

    async def main_entities(self, actor_id: str, *, limit: int) -> tuple[str, ...]:
        return self.entities_result[:limit]


@dataclass
class FakeUsage:
    personal_questions: tuple[str, ...] = ()
    personal_source_ids: tuple[str, ...] = ()
    popular: tuple[tuple[str, int], ...] = ()

    async def personal(
        self, actor_id: str, *, limit: int
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self.personal_questions[:limit], self.personal_source_ids[:limit]

    async def popular_sources(
        self, *, excluding_actor_id: str, limit: int
    ) -> tuple[tuple[str, int], ...]:
        return self.popular[:limit]


class FakeGenerator:
    def __init__(
        self,
        candidates: tuple[Candidate, ...] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.candidates = candidates
        self.error = error
        self.calls: list[tuple[SuggestionInputs, SuggestionLocale]] = []

    async def generate(
        self, inputs: SuggestionInputs, locale: SuggestionLocale
    ) -> tuple[Candidate, ...]:
        self.calls.append((inputs, locale))
        if self.error is not None:
            raise self.error
        return self.candidates


class FakeGrounding:
    def __init__(self, grounded: dict[str, bool] | None = None, *, default: bool = True) -> None:
        self.grounded = grounded or {}
        self.default = default
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    async def is_grounded(self, actor_id: str, question: str, source_ids: tuple[str, ...]) -> bool:
        self.calls.append((actor_id, question, source_ids))
        return self.grounded.get(question, self.default)


def _service(
    *,
    store: SuggestionStore,
    knowledge: FakeKnowledge | None = None,
    usage: FakeUsage | None = None,
    generator: FakeGenerator | None = None,
    grounding: FakeGrounding | None = None,
    now: datetime = T0,
) -> PromptSuggestionService:
    return PromptSuggestionService(
        store=store,
        knowledge=knowledge or FakeKnowledge(),
        usage=usage or FakeUsage(),
        generator=generator or FakeGenerator(),
        grounding=grounding or FakeGrounding(),
        id_factory=_id_factory(),
        clock=_clock(now),
    )


async def _seed_completed_refresh(
    store: SuggestionStore,
    key: SuggestionKey,
    suggestions: tuple[PromptSuggestion, ...],
    *,
    now: datetime = T0,
) -> None:
    await store.request_refresh(key, RefreshReason.MISSING, now=now)
    (claim,) = await store.claim_due(
        worker_id="seed", now=now, limit=10, lease_duration=timedelta(minutes=5)
    )
    await store.complete_refresh(claim, suggestions, now=now)


@pytest.mark.asyncio
async def test_first_read_requests_refresh():
    store = InMemorySuggestionStore()
    service = _service(store=store)

    result = await service.list(KEY)

    assert result == ()
    claims = await store.claim_due(
        worker_id="w1", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )
    assert len(claims) == 1
    assert claims[0].key == KEY


@pytest.mark.asyncio
async def test_read_drops_deleted_or_unpublished_sources():
    store = InMemorySuggestionStore()
    stored = PromptSuggestion(
        suggestion_id="s1",
        question="What is X?",
        sources=(SuggestionSource(source_id="src-1", version="rev-1:1"),),
    )
    await _seed_completed_refresh(store, KEY, (stored,))

    knowledge = FakeKnowledge(current_by_actor={"actor-1": {}})
    service = _service(store=store, knowledge=knowledge)

    result = await service.list(KEY)

    assert result == ()
    # FILTERED is throttled relative to the seeded refresh's last_refreshed_at.
    claims = await store.claim_due(
        worker_id="w1",
        now=T0 + timedelta(minutes=10),
        limit=10,
        lease_duration=timedelta(minutes=5),
    )
    assert len(claims) == 1


@pytest.mark.asyncio
async def test_read_drops_sources_with_newer_version():
    store = InMemorySuggestionStore()
    stored = PromptSuggestion(
        suggestion_id="s1",
        question="What is X?",
        sources=(SuggestionSource(source_id="src-1", version="rev-1:1"),),
    )
    await _seed_completed_refresh(store, KEY, (stored,))

    knowledge = FakeKnowledge(
        current_by_actor={
            "actor-1": {"src-1": CurrentSource(source_id="src-1", name="Doc 1", version="rev-2:1")}
        }
    )
    service = _service(store=store, knowledge=knowledge)

    result = await service.list(KEY)

    assert result == ()
    claims = await store.claim_due(
        worker_id="w1",
        now=T0 + timedelta(minutes=10),
        limit=10,
        lease_duration=timedelta(minutes=5),
    )
    assert len(claims) == 1


@pytest.mark.asyncio
async def test_read_drops_sources_the_user_cannot_access():
    store = InMemorySuggestionStore()
    stored = PromptSuggestion(
        suggestion_id="s1",
        question="What is X?",
        sources=(SuggestionSource(source_id="src-1", version="rev-1:1"),),
    )
    await _seed_completed_refresh(store, KEY, (stored,))

    # The source exists and is current, but only for a different actor.
    knowledge = FakeKnowledge(
        current_by_actor={
            "some-other-actor": {
                "src-1": CurrentSource(source_id="src-1", name="Doc 1", version="rev-1:1")
            }
        }
    )
    service = _service(store=store, knowledge=knowledge)

    result = await service.list(KEY)

    assert result == ()


@pytest.mark.asyncio
async def test_read_returns_current_source_names():
    store = InMemorySuggestionStore()
    stored = PromptSuggestion(
        suggestion_id="s1",
        question="What is X?",
        sources=(SuggestionSource(source_id="src-1", version="rev-1:1"),),
    )
    await _seed_completed_refresh(store, KEY, (stored,))

    knowledge = FakeKnowledge(
        current_by_actor={
            "actor-1": {
                "src-1": CurrentSource(source_id="src-1", name="Current Name", version="rev-1:1")
            }
        }
    )
    service = _service(store=store, knowledge=knowledge)

    result = await service.list(KEY)

    assert len(result) == 1
    assert result[0].suggestion_id == "s1"
    assert result[0].question == "What is X?"
    assert result[0].sources == (
        CurrentSource(source_id="src-1", name="Current Name", version="rev-1:1"),
    )


@pytest.mark.asyncio
async def test_refresh_keeps_only_grounded_candidates():
    store = InMemorySuggestionStore()
    topics = (
        TopicSource(source_id="src-1", name="Doc 1", version="rev-1:1", headings=("A",)),
        TopicSource(source_id="src-2", name="Doc 2", version="rev-1:1", headings=("B",)),
    )
    knowledge = FakeKnowledge(topics_result=topics)
    generator = FakeGenerator(
        candidates=(
            Candidate(question="Q1?", source_ids=("src-1",)),
            Candidate(question="Q2?", source_ids=("src-2",)),
        )
    )
    grounding = FakeGrounding(grounded={"Q1?": True, "Q2?": False})
    service = _service(store=store, knowledge=knowledge, generator=generator, grounding=grounding)

    result = await service.refresh(KEY)

    assert [s.question for s in result] == ["Q1?"]


@pytest.mark.asyncio
async def test_refresh_drops_invalid_candidates():
    store = InMemorySuggestionStore()
    topics = (TopicSource(source_id="src-1", name="Doc 1", version="rev-1:1", headings=("A",)),)
    knowledge = FakeKnowledge(topics_result=topics)
    generator = FakeGenerator(
        candidates=(
            Candidate(question="Unknown source?", source_ids=("src-unknown",)),
            Candidate(
                question="Too many sources?",
                source_ids=("src-1", "src-1", "src-1", "src-1"),
            ),
            Candidate(question="   ", source_ids=("src-1",)),
            Candidate(question="Duplicate?", source_ids=("src-1",)),
            Candidate(question="duplicate?", source_ids=("src-1",)),
            Candidate(question="Kept?", source_ids=("src-1",)),
        )
    )
    service = _service(store=store, knowledge=knowledge, generator=generator)

    result = await service.refresh(KEY)

    assert [s.question for s in result] == ["Duplicate?", "Kept?"]


@pytest.mark.asyncio
async def test_refresh_without_knowledge_skips_the_model():
    store = InMemorySuggestionStore()
    knowledge = FakeKnowledge(topics_result=())
    generator = FakeGenerator(candidates=(Candidate(question="Q?", source_ids=("src-1",)),))
    service = _service(store=store, knowledge=knowledge, generator=generator)

    result = await service.refresh(KEY)

    assert result == ()
    assert generator.calls == []


@pytest.mark.asyncio
async def test_generator_inputs_contain_only_counts_from_other_users():
    store = InMemorySuggestionStore()
    topics = (TopicSource(source_id="src-1", name="Doc 1", version="rev-1:1", headings=("A",)),)
    knowledge = FakeKnowledge(topics_result=topics, entities_result=("Entity A",))

    class SecretHoldingUsage:
        """Mimics the real adapter: it may hold other users' text internally, but
        popular_sources must only surface counts."""

        def __init__(self) -> None:
            self._other_user_question = "OTHER-USER-SECRET"
            self._popular = (("src-1", 3),)

        async def personal(
            self, actor_id: str, *, limit: int
        ) -> tuple[tuple[str, ...], tuple[str, ...]]:
            return (), ()

        async def popular_sources(
            self, *, excluding_actor_id: str, limit: int
        ) -> tuple[tuple[str, int], ...]:
            return self._popular[:limit]

    usage = SecretHoldingUsage()
    generator = FakeGenerator(candidates=())
    service = _service(store=store, knowledge=knowledge, usage=usage, generator=generator)

    await service.refresh(KEY)

    assert len(generator.calls) == 1
    captured_inputs, _locale = generator.calls[0]
    assert captured_inputs.popular_sources == (("src-1", 3),)
    assert "OTHER-USER-SECRET" not in repr(captured_inputs)


@pytest.mark.asyncio
async def test_failed_refresh_keeps_previous_suggestions():
    store = InMemorySuggestionStore()
    topics = (TopicSource(source_id="src-1", name="Doc 1", version="rev-1:1", headings=("A",)),)
    knowledge = FakeKnowledge(topics_result=topics)
    generator = FakeGenerator(candidates=(Candidate(question="Q1?", source_ids=("src-1",)),))
    service = _service(store=store, knowledge=knowledge, generator=generator)
    worker = SuggestionRefreshWorker(store=store, service=service, worker_id="w1", clock=_clock(T0))

    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    processed = await worker.run_once(limit=10)
    assert processed == 1
    first_load = await store.load(KEY)
    assert [s.question for s in first_load] == ["Q1?"]

    generator.error = RuntimeError("model timeout")
    later = T0 + timedelta(minutes=30)
    worker_later = SuggestionRefreshWorker(
        store=store, service=service, worker_id="w1", clock=_clock(later)
    )
    await store.request_refresh(KEY, RefreshReason.KNOWLEDGE_PUBLISHED, now=later)
    processed_later = await worker_later.run_once(limit=10)

    assert processed_later == 1
    second_load = await store.load(KEY)
    assert [s.question for s in second_load] == ["Q1?"]


@pytest.mark.asyncio
async def test_worker_marks_daily_refresh_before_claiming():
    store = InMemorySuggestionStore()
    topics = (TopicSource(source_id="src-1", name="Doc 1", version="rev-1:1", headings=("A",)),)
    knowledge = FakeKnowledge(topics_result=topics)
    generator = FakeGenerator(candidates=(Candidate(question="Q1?", source_ids=("src-1",)),))

    await store.request_refresh(KEY, RefreshReason.MISSING, now=T0)
    (claim,) = await store.claim_due(
        worker_id="seed", now=T0, limit=10, lease_duration=timedelta(minutes=5)
    )
    await store.complete_refresh(
        claim,
        (
            PromptSuggestion(
                suggestion_id="s1",
                question="Q1?",
                sources=(SuggestionSource(source_id="src-1", version="rev-1:1"),),
            ),
        ),
        now=T0,
    )

    much_later = T0 + timedelta(hours=25)
    service = _service(store=store, knowledge=knowledge, generator=generator, now=much_later)
    worker = SuggestionRefreshWorker(
        store=store, service=service, worker_id="w1", clock=_clock(much_later)
    )

    processed = await worker.run_once(limit=10)

    assert processed == 1
