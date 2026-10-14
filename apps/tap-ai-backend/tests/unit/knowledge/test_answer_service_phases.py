"""`AnswerService`'s frozen answering path exposed as resumable prepare / generate /
verify / save phases, each independently re-authorizing the selected revisions so a
later LangGraph node can call them on their own."""

from __future__ import annotations

import pytest
from test_answer_service import (
    MemoryAnswerRepository,
    answer_response,
    ready,
    request_for,
)

from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.knowledge.application.answers import AnswerService


class PhaseKnowledge:
    """A minimal frozen-phase gateway double: records calls and lets each
    phase's return value (and whether `authorize` fires) be configured."""

    def __init__(self, *, prepared=None, generation=None, response=None) -> None:
        self.prepared = prepared if prepared is not None else "bundle-marker"
        self.generation = generation if generation is not None else "generation-marker"
        self.response = response or answer_response()
        self.calls: list[tuple[str, object]] = []
        self.authorize_calls = 0

    async def prepare_frozen(
        self,
        request,
        policy,
        *,
        governance,
        graph_context=(),
        answer_execution=None,
        authorize=None,
    ):
        self.calls.append(("prepare", request))
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
    ):
        self.calls.append(("generate", bundle))
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
