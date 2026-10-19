"""`AuthorizedRetrieval.answer` is split into `prepare_answer` / `generate_answer`
/ `verify_answer` so later LangGraph nodes can checkpoint an `EvidenceBundle`
between them. These tests prove the split is a pure move: the phased calls
must produce exactly the same `AnswerResponse` as the single-shot `answer()`,
including after an `EvidenceBundle` round trip through its JSON codec."""

from __future__ import annotations

import json

import pytest

from tap.modules.ai.domain.persona import TapperPersona
from tap.modules.knowledge.application.evidence_bundle import (
    EvidenceBundle,
    decode_bundle,
    encode_bundle,
)
from tap.modules.knowledge.application.retrieve import AuthorizedRetrieval
from tap.modules.knowledge.domain.models import AbstentionReason, AnswerRequest, AnswerResponse
from tap.modules.knowledge.ports.answers import DocumentStateChanged
from tap.modules.knowledge.ports.models import AnswerGeneration
from tests.contract.test_knowledge_api import (
    FakeModelPort,
    FakeSearchPort,
    PassthroughRedactor,
    policy_context,
    search_hit,
)


def _id_factory():
    ids = iter(
        (
            "operation-1",
            "query-plan-1",
            "context-snapshot-1",
            "trace-1",
            "citation-1",
            "claim-1",
            *(f"generated-{index}" for index in range(100)),
        )
    )
    return lambda: next(ids)


class CurrentPolicyVerifier:
    async def verify_current(self, expected):
        return expected


class ToggledPolicyVerifier:
    """Succeeds like `CurrentPolicyVerifier` until `current` is flipped to
    `False`, simulating the selected revision/policy changing underneath an
    in-flight answer between the prepare and generate phases."""

    def __init__(self) -> None:
        self.current = True

    async def verify_current(self, expected):
        if not self.current:
            raise DocumentStateChanged("selected revision changed")
        return expected


def _retrieval(
    search: FakeSearchPort,
    model: FakeModelPort,
    *,
    policy_verifier=None,
) -> AuthorizedRetrieval:
    return AuthorizedRetrieval(
        search=search,
        embeddings=model,
        answers=model,
        policy_verifier=policy_verifier or CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )


@pytest.mark.asyncio
async def test_phased_answer_equals_single_shot_answer() -> None:
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    single_shot = AuthorizedRetrieval(
        search=FakeSearchPort((search_hit(),)),
        embeddings=(model_a := FakeModelPort()),
        answers=model_a,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )
    phased = AuthorizedRetrieval(
        search=FakeSearchPort((search_hit(),)),
        embeddings=(model_b := FakeModelPort()),
        answers=model_b,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )

    expected = await single_shot.answer(request, policy)

    prepared = await phased.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)
    round_tripped = decode_bundle(json.loads(json.dumps(encode_bundle(prepared))))
    generation = await phased.generate_answer(round_tripped, policy)
    actual = await phased.verify_answer(round_tripped, generation, policy)

    assert actual == expected
    assert expected.abstained is False


@pytest.mark.asyncio
async def test_prepare_returns_abstain_response_without_evidence() -> None:
    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort(()), model)

    result = await retrieval.prepare_answer(AnswerRequest(query="Unknown fact"), policy_context())

    assert isinstance(result, AnswerResponse)
    assert result.abstained is True
    assert result.abstention_reason is AbstentionReason.INSUFFICIENT_EVIDENCE
    assert model.answer_evidence == []


@pytest.mark.asyncio
async def test_generate_rechecks_current_revision_before_model_call() -> None:
    model = FakeModelPort()
    verifier = ToggledPolicyVerifier()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model, policy_verifier=verifier)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)

    verifier.current = False
    with pytest.raises(DocumentStateChanged):
        await retrieval.generate_answer(prepared, policy)

    assert model.answer_evidence == []


@pytest.mark.asyncio
async def test_generate_answer_forwards_a_non_default_persona_to_the_model_gateway() -> None:
    """Task 1 fix round 1: `generate_answer`'s `persona` kwarg (threaded down
    from `KnowledgeAPI.generate_frozen` -> `AnswerService.generate_frozen` ->
    `KnowledgeHttpService.generate_conversation_answer`, the grounded
    conversation-turn path) must reach the model gateway's `.answer()` call --
    not silently default to `CURRENT_PERSONA` once a Turn names a different
    one."""

    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()
    other_persona = TapperPersona("tapper-persona-test", "Alternate platform voice.", "Boundary.")

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)

    await retrieval.generate_answer(prepared, policy, persona=other_persona)

    assert model.answer_kwargs[-1]["persona"] is other_persona


@pytest.mark.asyncio
async def test_verify_passes_clarification_without_claims() -> None:
    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)

    generation = AnswerGeneration(
        text="你指的是哪个产品版本？",
        claims=(),
        model_id="tap-answer-v1",
        profile_id="grounded-answer-v1",
        provider_request_id="answer-request-1",
        answer_kind="clarification",
    )

    response = await retrieval.verify_answer(prepared, generation, policy)

    assert isinstance(response, AnswerResponse)
    assert response.abstained is False
    assert response.claims == ()
    assert response.citations == ()
    assert response.answer_kind == "clarification"
    assert response.answer == "你指的是哪个产品版本？"
