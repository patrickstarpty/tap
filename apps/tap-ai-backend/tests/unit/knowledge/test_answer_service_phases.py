"""`AnswerService`'s frozen answering path exposed as resumable prepare / generate /
verify / save phases, each independently re-authorizing the selected revisions so a
later LangGraph node can call them on their own."""

from __future__ import annotations

import asyncio

import pytest
from test_answer_service import (
    MemoryAnswerRepository,
    answer_response,
    ready,
    request_for,
)

from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.ai.domain.persona import CURRENT_PERSONA, TapperPersona
from tap.modules.knowledge.application.answers import AnswerService, AnswerSnapshotUnavailable


class PhaseKnowledge:
    """A minimal frozen-phase gateway double: records calls and lets each
    phase's return value (and whether `authorize` fires) be configured."""

    def __init__(
        self, *, prepared=None, generation=None, response=None, delay: float = 0.0
    ) -> None:
        self.prepared = prepared if prepared is not None else "bundle-marker"
        self.generation = generation if generation is not None else "generation-marker"
        self.response = response or answer_response()
        self.delay = delay
        self.calls: list[tuple[str, object]] = []
        self.authorize_calls = 0
        self.generate_personas: list[TapperPersona] = []
        self.prepare_include_relations: list[bool] = []

    async def prepare_frozen(
        self,
        request,
        policy,
        *,
        governance,
        graph_context=(),
        answer_execution=None,
        authorize=None,
        include_relations: bool = True,
    ):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(("prepare", request))
        self.prepare_include_relations.append(include_relations)
        if authorize is not None:
            await authorize()
            self.authorize_calls += 1
        return self.prepared

    async def generate_frozen(
        self,
        bundle,
        policy,
        *,
        governance,
        model_alias=None,
        answer_execution=None,
        authorize=None,
        persona: TapperPersona = CURRENT_PERSONA,
    ):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(("generate", bundle))
        self.generate_personas.append(persona)
        if authorize is not None:
            await authorize()
            self.authorize_calls += 1
        return self.generation

    async def verify_frozen(
        self,
        bundle,
        generation,
        policy,
        *,
        answer_execution=None,
        authorize=None,
    ):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(("verify", (bundle, generation)))
        if authorize is not None:
            await authorize()
            self.authorize_calls += 1
        return self.response


def _service(
    *, prepared=None, generation=None, response=None
) -> tuple[AnswerService, MemoryAnswerRepository, PhaseKnowledge]:
    repository = MemoryAnswerRepository((ready(),))
    knowledge = PhaseKnowledge(prepared=prepared, generation=generation, response=response)
    return (
        AnswerService(repository=repository, knowledge=knowledge),
        repository,
        knowledge,
    )


def _frozen_policy() -> RetrievalPolicyContext:
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context

    return build_demo_policy_context((ready(),))


class _Execution:
    """Stands in for `AuthorizedAnswerExecution`: only `remaining_seconds` is
    read by `AnswerService`/`KnowledgeAPI`'s timeout wrapping. It is a fixed
    budget captured once (not a countdown), which is exactly what makes a
    per-phase timeout dangerous: each phase alone would get the *whole*
    budget again."""

    def __init__(self, remaining_seconds: float) -> None:
        self.remaining_seconds = remaining_seconds


@pytest.mark.asyncio
async def test_save_frozen_persists_snapshot_once():
    revisions = (ready(),)
    policy = _frozen_policy()
    composed_service, composed_repository, _ = _service()
    expected = await composed_service.answer_frozen(
        request_for("doc_a"), revisions, policy, governance=None
    )

    answer_service, repository, _knowledge = _service()
    request = request_for("doc_a")

    prepared = await answer_service.prepare_frozen(request, revisions, policy, governance=None)
    assert not isinstance(prepared, type(None))
    generation = await answer_service.generate_frozen(prepared, revisions, policy, governance=None)
    response = await answer_service.verify_frozen(prepared, generation, revisions, policy)
    await answer_service.save_frozen(response, request.query, revisions)

    assert response == expected
    assert len(repository.snapshots) == 1
    assert len(composed_repository.snapshots) == 1
    assert repository.snapshots[0].citations == composed_repository.snapshots[0].citations


@pytest.mark.asyncio
async def test_generate_frozen_forwards_a_non_default_persona_to_the_knowledge_boundary():
    """Task 1 fix round 1: `AnswerService.generate_frozen`'s `persona` kwarg
    must reach `self._knowledge.generate_frozen` (here, `PhaseKnowledge` --
    standing in for the real `KnowledgeAPI`) rather than being dropped, since
    `KnowledgeHttpService.generate_conversation_answer` (the grounded
    conversation-turn path) now resolves the frozen Turn's own persona and
    passes it down to this call."""

    answer_service, _repository, knowledge = _service()
    revisions = (ready(),)
    policy = _frozen_policy()
    request = request_for("doc_a")
    other_persona = TapperPersona("tapper-persona-test", "Alternate platform voice.", "Boundary.")

    prepared = await answer_service.prepare_frozen(request, revisions, policy, governance=None)
    await answer_service.generate_frozen(
        prepared, revisions, policy, governance=None, persona=other_persona
    )

    assert knowledge.generate_personas[-1] is other_persona


@pytest.mark.asyncio
async def test_save_frozen_resumed_with_same_trace_id_is_idempotent():
    """A resumed finalize (after the checkpoint write following save raised, or the
    worker was killed in between) replays `save_frozen` with the same trace_id; since
    the content is unchanged, the second call must be a no-op rather than a duplicate
    snapshot error."""
    revisions = (ready(),)
    policy = _frozen_policy()
    answer_service, repository, _knowledge = _service()
    request = request_for("doc_a")

    prepared = await answer_service.prepare_frozen(request, revisions, policy, governance=None)
    generation = await answer_service.generate_frozen(prepared, revisions, policy, governance=None)
    response = await answer_service.verify_frozen(prepared, generation, revisions, policy)

    await answer_service.save_frozen(response, request.query, revisions)
    await answer_service.save_frozen(response, request.query, revisions)

    assert len(repository.snapshots) == 1


@pytest.mark.asyncio
async def test_save_frozen_resumed_with_different_content_for_same_trace_id_raises():
    """A genuine mismatch (same trace_id, different content) is not idempotency -- it
    must still surface as a conflict, same as the original duplicate-insert failure."""
    revisions = (ready(),)
    policy = _frozen_policy()
    answer_service, repository, _knowledge = _service()
    request = request_for("doc_a")

    prepared = await answer_service.prepare_frozen(request, revisions, policy, governance=None)
    generation = await answer_service.generate_frozen(prepared, revisions, policy, governance=None)
    response = await answer_service.verify_frozen(prepared, generation, revisions, policy)
    await answer_service.save_frozen(response, request.query, revisions)

    with pytest.raises(AnswerSnapshotUnavailable, match="different content"):
        await answer_service.save_frozen(response, "a materially different question", revisions)

    assert len(repository.snapshots) == 1


@pytest.mark.asyncio
async def test_prepare_abstain_is_persisted_by_finalize():
    abstained = answer_response(abstained=True)
    revisions = (ready(),)
    policy = _frozen_policy()
    answer_service, repository, knowledge = _service(prepared=abstained)
    request = request_for("doc_a")

    prepared = await answer_service.prepare_frozen(request, revisions, policy, governance=None)

    assert prepared is abstained
    await answer_service.save_frozen(prepared, request.query, revisions)

    assert len(repository.snapshots) == 1
    # the abstained response's own citation set is what gets persisted
    assert tuple(item.citation_id for item in repository.snapshots[0].citations) == ("citation-a",)
    # abstention short-circuits generate/verify: only prepare was ever called
    assert [name for name, _ in knowledge.calls] == ["prepare"]


@pytest.mark.asyncio
async def test_answer_frozen_enforces_one_total_deadline():
    """`answer_frozen` must keep its single persisted-deadline guarantee: three
    resumable phases, each individually timed against the *same* fixed
    `remaining_seconds` budget, must not let the composed call run to ~3x that
    budget -- the whole prepare/generate/verify composition shares one
    deadline, same as before these phases were split out."""
    budget = 0.3
    # each phase alone fits comfortably in the budget; all three in sequence
    # overshoot it by 20%, so a per-phase (not composed) timeout would pass.
    delay = 0.4 * budget
    revisions = (ready(),)
    policy = _frozen_policy()

    bounded_repository = MemoryAnswerRepository((ready(),))
    bounded_knowledge = PhaseKnowledge(delay=delay)
    bounded_service = AnswerService(repository=bounded_repository, knowledge=bounded_knowledge)

    with pytest.raises(TimeoutError):
        await bounded_service.answer_frozen(
            request_for("doc_a"),
            revisions,
            policy,
            governance=None,
            answer_execution=_Execution(budget),
        )
    assert bounded_repository.snapshots == []

    # the same three slow phases, with no plan budget, still complete.
    unbounded_repository = MemoryAnswerRepository((ready(),))
    unbounded_knowledge = PhaseKnowledge(delay=delay)
    unbounded_service = AnswerService(
        repository=unbounded_repository, knowledge=unbounded_knowledge
    )

    response = await unbounded_service.answer_frozen(
        request_for("doc_a"), revisions, policy, governance=None
    )
    assert response == answer_response()
    assert len(unbounded_repository.snapshots) == 1


@pytest.mark.asyncio
async def test_prepare_frozen_threads_include_relations_to_the_knowledge_boundary():
    """I-1 (Task 6 fix round 1): `AnswerService.prepare_frozen` must forward
    `include_relations` to the Knowledge boundary's own `prepare_frozen`
    unchanged -- `KnowledgeHttpService.prepare_conversation_answer` ->
    `AnswerService.prepare_frozen` -> `KnowledgeAPI.prepare_frozen` ->
    `AuthorizedRetrieval.prepare_answer`, for Task 9's `seed_search` call."""
    revisions = (ready(),)
    policy = _frozen_policy()
    answer_service, _repository, knowledge = _service()

    await answer_service.prepare_frozen(
        request_for("doc_a"), revisions, policy, governance=None, include_relations=False
    )
    await answer_service.prepare_frozen(request_for("doc_a"), revisions, policy, governance=None)

    assert knowledge.prepare_include_relations == [False, True]
