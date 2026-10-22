"""`KnowledgeHttpService.answer_conversation` split into resumable, JSON-serializable
prepare / generate / verify / finalize steps so a later LangGraph node can call each
phase on its own, checkpointing `ConversationAnswerStep` between them."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_answer_service import MemoryAnswerRepository, answer_response, ready

from tap.contracts.http import AbstentionReason, RetrievalAnswerRequest
from tap.entrypoints.tapper_chat_turn import ChatTurnNodes
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.persona import TAPPER_PERSONA_V1, UnknownPersonaVersion
from tap.modules.chat.application.plan_answer import AnswerPlanner, PlanningInput, planning_input
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.chat.domain.conversations import (
    FrozenResource,
    TurnInput,
    TurnInputSnapshot,
    content_digest,
)
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.application.answers import AnswerService
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.application.evidence_bundle import EvidenceBundle
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    AnswerRequest,
    Claim,
    ContentRole,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    DocumentAnchor,
    IndexRevision,
    ModelCallProvenance,
    QueryPlan,
    RetrievalProfileId,
    RevisionKind,
    SearchResponse,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    GeneratedToolCall,
    RedactionResult,
    SearchHit,
)


def _bundle(*, query: str) -> EvidenceBundle:
    plan = QueryPlan(
        query_plan_id="plan-a",
        operation_id="operation-a",
        tenant_id="tenant-a",
        project_id=VALIDATION_SCOPE.project_id,
        policy_decision_id="decision-a",
        policy_version="policy-a",
        acl_digest="sha256:" + "a" * 64,
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=(SourceFamily.DOC,),
        resources=(),
        effective_environment="production",
        corpus_version="tapper-demo-v1",
        candidate_limit=20,
        raw_request_hash="sha256:" + "b" * 64,
        sanitized_query=query,
        sanitized_query_hash="sha256:" + "d" * 64,
        redaction_version="redaction-v1",
        embedding_model_id="research-embedding-v1",
        embedding_dimension=1536,
    )
    context_snapshot = ContextSnapshot(
        context_snapshot_id="snapshot-a",
        operation_id="operation-a",
        tenant_id="tenant-a",
        project_id=VALIDATION_SCOPE.project_id,
        policy_decision_id="decision-a",
        policy_version="policy-a",
        acl_digest="sha256:" + "a" * 64,
        layers=(
            ContextLayer(
                kind=ContextLayerKind.CURRENT_TURN,
                ref_ids=(),
                content_hash="sha256:" + "d" * 64,
                token_count=7,
            ),
        ),
    )
    return EvidenceBundle(
        request=AnswerRequest(query=query),
        generation_query=query,
        response=SearchResponse(
            trace_id="trace-a",
            query_plan_id="plan-a",
            context_snapshot_id="snapshot-a",
            corpus_version="tapper-demo-v1",
            retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
            evidence=(),
            embedding_provenance=ModelCallProvenance("text-embedding-v4", "embed-a"),
        ),
        plan=plan,
        context_snapshot=context_snapshot,
        publication=None,
        relation=None,
        answer_input=None,
        evidence_map={},
        graph_context=(),
        flow_context={},
        missing=False,
        relation_first=False,
    )


class Scoped:
    scope = VALIDATION_SCOPE


class PhaseAnswers:
    """A resumable-phase double standing in for `AnswerService`'s frozen answer
    surface: records how many times each phase's knowledge port was entered,
    the same shape the real `AnswerService`/`KnowledgeAPI` expose."""

    scope = VALIDATION_SCOPE

    def __init__(self) -> None:
        self.prepare_calls = 0
        self.generate_calls = 0
        self.verify_calls = 0
        self.save_calls: list[object] = []
        self.generate_personas: list[object] = []
        self.prepare_include_relations: list[bool] = []

    async def prepare_frozen(
        self,
        request,
        revisions,
        policy,
        *,
        governance,
        graph_context=(),
        answer_execution=None,
        authorize=None,
        include_relations: bool = True,
    ):
        self.prepare_calls += 1
        self.prepare_include_relations.append(include_relations)
        if authorize is not None:
            await authorize()
        return _bundle(query=request.query)

    async def generate_frozen(
        self,
        bundle,
        revisions,
        policy,
        *,
        governance,
        model_alias=None,
        answer_execution=None,
        authorize=None,
        persona=None,
    ):
        self.generate_calls += 1
        self.generate_personas.append(persona)
        if authorize is not None:
            await authorize()
        return AnswerGeneration(
            "The rule is grounded.",
            (GeneratedClaim("The rule is grounded.", ("S1",)),),
            "qwen-plus",
            "quick-hybrid-v1",
            "provider-request-a",
        )

    async def verify_frozen(
        self,
        bundle,
        generation,
        revisions,
        policy,
        *,
        answer_execution=None,
        authorize=None,
    ):
        self.verify_calls += 1
        if authorize is not None:
            await authorize()
        return answer_response()

    async def save_frozen(self, response, query, revisions) -> None:
        self.save_calls.append(response)


async def _retrieve_route(*, persona_version: str | None = None) -> tuple[TurnInput, object]:
    """A frozen turn input and admitted `retrieve`-route plan selecting the one
    revision `ready()`/`PhaseAnswers` are built around."""

    revision = ready()
    authority = build_demo_policy_context((revision,))
    frozen = TurnInput(
        "错误码 E104 如何处理？",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        resolved_resources=(
            FrozenResource(
                revision.source_id,
                revision.document_id,
                revision.revision_id,
                revision.source_content_hash,
            ),
        ),
        acl_digest=authority.acl_digest,
        retrieval_policy_digest=content_digest(
            {
                "decisionId": authority.decision_id,
                "policyVersion": authority.policy_version,
                "corpusVersion": authority.active_corpus_version,
            }
        ),
        persona_version=persona_version,
    )
    plan = await AnswerPlanner().plan(
        PlanningInput(
            VALIDATION_SCOPE.project_id,
            "turn-a",
            content_digest(
                frozen.material(project_id=VALIDATION_SCOPE.project_id, turn_id="turn-a")
            ),
            frozen.acl_digest,
            frozen.retrieval_policy_digest,
            frozen.message,
            frozen.model_alias,
            (revision.source_id,),
        )
    )
    assert plan.route == "retrieve"
    return frozen, plan


async def _direct_route() -> tuple[TurnInput, object]:
    """A frozen turn input and admitted `direct`-route plan (a greeting), which
    never reaches retrieval."""

    frozen = TurnInput(
        "你好",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
    )
    snapshot = TurnInputSnapshot.create(
        snapshot_id="snapshot-a",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn-a",
        value=frozen,
        now=datetime.now(UTC),
    )
    plan = await AnswerPlanner().plan(planning_input(snapshot))
    assert plan.route == "direct"
    return frozen, plan


def _out_of_scope_plan() -> AnswerPlan:
    """An admitted `out_of_scope` plan, as `guard_in` would persist it after a
    planner suggestion -- `refuse_out_of_scope` never retrieves or calls a
    model, so the plan's own digests need not bind to any particular
    `frozen_input`."""
    digest = "sha256:" + "a" * 64
    return AnswerPlan(
        plan_id="plan-out-of-scope",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn-out-of-scope",
        input_digest=digest,
        acl_digest=digest,
        policy_digest=digest,
        model_alias="qwen-plus",
        original_question="What's the weather today?",
        intent="out_of_scope",
        route="direct",
        confidence=0.9,
        standalone_query="What's the weather today?",
        constraints=(),
        missing=(),
        queries=(),
        source_ids=(),
        template_id="template-1",
        template_version="v1",
        template_digest=digest,
        deadline_at=time.time() + 30,
        remaining_seconds=30,
        agent_intent="out_of_scope",
    )


def _service(
    answers: PhaseAnswers | None = None, *, models=None, redactor=None
) -> KnowledgeHttpService:
    return KnowledgeHttpService(
        documents=Scoped(),
        answers=answers or PhaseAnswers(),
        citations=Scoped(),
        models=models,
        redactor=redactor,
    )


@pytest.mark.asyncio
async def test_steps_compose_to_answer_conversation() -> None:
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)

    composed_service = _service(PhaseAnswers())
    composed = await composed_service.answer_conversation(request, frozen, answer_plan=plan)

    stepped_service = _service(PhaseAnswers())
    step = await stepped_service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    step = await stepped_service.generate_conversation_answer(step, frozen, answer_plan=plan)
    step = await stepped_service.verify_conversation_answer(step, frozen, answer_plan=plan)
    stepped = await stepped_service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert stepped.model_dump(mode="json", by_alias=True) == composed.model_dump(
        mode="json", by_alias=True
    )


@pytest.mark.asyncio
async def test_prepare_conversation_answer_threads_include_relations_false() -> None:
    """I-1 (Task 6 fix round 1): `include_relations=False` must reach
    `AuthorizedRetrieval.prepare_answer` through `prepare_conversation_answer`
    -> `AnswerService.prepare_frozen` -> `KnowledgeAPI.prepare_frozen` --
    Task 9's `seed_search` graph node call -- not silently default to `True`
    at any hop."""
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    answers = PhaseAnswers()
    service = _service(answers)

    await service.prepare_conversation_answer(
        request, frozen, answer_plan=plan, include_relations=False
    )

    assert answers.prepare_include_relations == [False]


@pytest.mark.asyncio
async def test_prepare_conversation_answer_defaults_include_relations_true() -> None:
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    answers = PhaseAnswers()
    service = _service(answers)

    await service.prepare_conversation_answer(request, frozen, answer_plan=plan)

    assert answers.prepare_include_relations == [True]


@pytest.mark.asyncio
async def test_each_step_survives_json_round_trip() -> None:
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    service = _service(PhaseAnswers())

    def roundtrip(step: dict[str, object]) -> dict[str, object]:
        return json.loads(json.dumps(step))

    step = roundtrip(await service.prepare_conversation_answer(request, frozen, answer_plan=plan))
    assert step["kind"] == "prepared"
    step = roundtrip(await service.generate_conversation_answer(step, frozen, answer_plan=plan))
    assert step["kind"] == "generated"
    step = roundtrip(await service.verify_conversation_answer(step, frozen, answer_plan=plan))
    assert step["kind"] == "verified"
    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    expected = await _service(PhaseAnswers()).answer_conversation(request, frozen, answer_plan=plan)
    assert result.model_dump(mode="json", by_alias=True) == expected.model_dump(
        mode="json", by_alias=True
    )


@pytest.mark.asyncio
async def test_steps_share_persisted_plan_deadline() -> None:
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    answers = PhaseAnswers()
    service = _service(answers)

    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert step["kind"] == "prepared"

    expired_plan = replace(plan, deadline_at=time.time() - 1)
    with pytest.raises(TimeoutError):
        await service.generate_conversation_answer(step, frozen, answer_plan=expired_plan)
    assert answers.generate_calls == 0


@pytest.mark.asyncio
async def test_generate_rechecks_authority_before_model_call() -> None:
    """The recheck must be observable at the service level, not merely
    inside the double: a `frozen_input` whose ACL changed after `prepare`
    (e.g. the source was re-permissioned) must stop `generate` from ever
    entering the generation port -- proving `generate_conversation_answer`
    re-derives and re-validates from `frozen_input` itself, not just from
    the checkpointed `step`."""
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    changed_frozen = replace(frozen, acl_digest="sha256:" + "0" * 64)

    stepped_answers = PhaseAnswers()
    stepped_service = _service(stepped_answers)
    step = await stepped_service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert step["kind"] == "prepared"

    with pytest.raises(ValueError, match="answer plan input or authorization binding changed"):
        await stepped_service.generate_conversation_answer(step, changed_frozen, answer_plan=plan)
    assert stepped_answers.generate_calls == 0

    # Same condition (the plan's bound ACL digest no longer matches the
    # current `frozen_input`), exercised through the composed call instead of
    # the standalone step, raises the identical exception type.
    composed_answers = PhaseAnswers()
    composed_service = _service(composed_answers)
    with pytest.raises(ValueError, match="answer plan input or authorization binding changed"):
        await composed_service.answer_conversation(request, changed_frozen, answer_plan=plan)
    assert composed_answers.prepare_calls == 0
    assert composed_answers.generate_calls == 0


@pytest.mark.asyncio
async def test_generate_conversation_answer_resolves_and_forwards_the_frozen_persona() -> None:
    """Task 1 fix round 1 (Important): the grounded conversation-turn path
    (`generate_conversation_answer`) must resolve `frozen_input.persona_version`
    via `persona_for` and pass that `TapperPersona` down to the generation
    port -- a Turn frozen under a different persona version must not silently
    answer under `CURRENT_PERSONA` once it reaches the grounded `model` node.
    `persona_version=None` (every Turn frozen before this field existed, and
    the default for a fresh one) must still resolve to `TAPPER_PERSONA_V1`."""

    frozen, plan = await _retrieve_route(persona_version="tapper-persona-v1")
    request = RetrievalAnswerRequest(query=frozen.message)
    answers = PhaseAnswers()
    service = _service(answers)

    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    await service.generate_conversation_answer(step, frozen, answer_plan=plan)

    assert answers.generate_personas == [TAPPER_PERSONA_V1]

    legacy_frozen, legacy_plan = await _retrieve_route(persona_version=None)
    legacy_answers = PhaseAnswers()
    legacy_service = _service(legacy_answers)
    legacy_request = RetrievalAnswerRequest(query=legacy_frozen.message)

    legacy_step = await legacy_service.prepare_conversation_answer(
        legacy_request, legacy_frozen, answer_plan=legacy_plan
    )
    await legacy_service.generate_conversation_answer(
        legacy_step, legacy_frozen, answer_plan=legacy_plan
    )

    assert legacy_answers.generate_personas == [TAPPER_PERSONA_V1]


@pytest.mark.asyncio
async def test_generate_conversation_answer_fails_deterministically_on_unknown_persona() -> None:
    """A Turn frozen with a persona version this build does not carry must
    fail closed (`UnknownPersonaVersion`, a `ValueError`) before ever
    entering the generation port -- never a silent fallback to the current
    persona. `_authorized_frozen_context` resolves the persona on every
    phase (both `prepare` and `generate`), so an unknown version is already
    caught at `prepare`, before any retrieval or model call."""

    frozen, plan = await _retrieve_route(persona_version="no-such-persona")
    request = RetrievalAnswerRequest(query=frozen.message)
    answers = PhaseAnswers()
    service = _service(answers)

    with pytest.raises(UnknownPersonaVersion):
        await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert answers.prepare_calls == 0
    assert answers.generate_calls == 0


@pytest.mark.asyncio
async def test_expired_direct_plan_times_out_before_the_model_call() -> None:
    """`prepare_conversation_answer` must bound its non-retrieval model call
    by the plan's persisted deadline, same as `answer_conversation`: an
    already-expired `direct` plan must raise `TimeoutError` without ever
    calling the chat model."""
    chat_calls = 0

    class Models:
        async def chat(self, *args, **kwargs):
            nonlocal chat_calls
            chat_calls += 1
            return AnswerGeneration("Hello", (), "qwen-plus", "direct-chat-v1", None)

    frozen, plan = await _direct_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    service = _service(PhaseAnswers(), models=Models())

    expired_plan = replace(plan, deadline_at=time.time() - 1)
    with pytest.raises(TimeoutError):
        await service.prepare_conversation_answer(request, frozen, answer_plan=expired_plan)
    assert chat_calls == 0


@pytest.mark.asyncio
async def test_non_retrieval_route_returns_final_step() -> None:
    class Models:
        async def chat(self, *args, **kwargs):
            return AnswerGeneration("Hello", (), "qwen-plus", "direct-chat-v1", None)

    frozen, plan = await _direct_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    service = _service(PhaseAnswers(), models=Models())

    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert step["kind"] == "final"

    unchanged_after_generate = await service.generate_conversation_answer(
        step, frozen, answer_plan=plan
    )
    assert unchanged_after_generate == step

    unchanged_after_verify = await service.verify_conversation_answer(
        unchanged_after_generate, frozen, answer_plan=plan
    )
    assert unchanged_after_verify == step

    result = await service.finalize_conversation_answer(
        unchanged_after_verify, frozen, answer_plan=plan
    )
    assert result.answer == "Hello"


@pytest.mark.asyncio
async def test_refuse_out_of_scope_abstains_without_retrieval_or_generation() -> None:
    """`KnowledgeHttpService.refuse_out_of_scope` must produce a closed,
    abstained `RetrievalAnswerResponse` keyed on the plan's own identity,
    the same way `_stopped_answer` does for other non-retrieval routes --
    without calling a model or touching retrieval."""

    class Models:
        async def chat(self, *args, **kwargs):
            raise AssertionError("refuse_out_of_scope must never call the chat model")

    frozen, _unused_plan = await _direct_route()
    plan = _out_of_scope_plan()
    service = _service(PhaseAnswers(), models=Models())

    response = await service.refuse_out_of_scope(frozen, answer_plan=plan)

    assert response.answer == ""
    assert response.abstained is True
    assert response.abstention_reason == AbstentionReason.OUT_OF_SCOPE
    assert response.claims == []
    assert response.citations == []
    assert response.trace_id == plan.plan_id
    assert response.query_plan_id == plan.plan_id
    assert response.context_snapshot_id == plan.plan_id
    assert response.retrieval_profile_id == "direct-chat-v1"
    assert response.graph_context_status == "NOT_SELECTED"


@pytest.mark.asyncio
async def test_answer_conversation_short_circuits_out_of_scope_without_generation() -> None:
    """M4 (defence in depth): `answer_conversation` is also called directly
    by prompt-suggestion grounding outside the graph (which never runs
    `guard_in`'s own out-of-scope routing), so it must itself refuse an
    out-of-scope plan without calling the model, the same way the graph's
    `finalize` node does."""

    class Models:
        async def chat(self, *args, **kwargs):
            raise AssertionError("answer_conversation must never call the chat model")

    frozen, _unused_plan = await _direct_route()
    plan = _out_of_scope_plan()
    service = _service(PhaseAnswers(), models=Models())
    request = RetrievalAnswerRequest(query=frozen.message)

    response = await service.answer_conversation(request, frozen, answer_plan=plan)

    assert response.answer == ""
    assert response.abstained is True
    assert response.abstention_reason == AbstentionReason.OUT_OF_SCOPE
    assert response.trace_id == plan.plan_id


@pytest.mark.asyncio
async def test_output_redaction_keeps_claims_locatable() -> None:
    """`finalize_conversation_answer` must redact the answer body and every
    claim's text with the same egress redactor before `save_frozen` --
    the saved snapshot and the returned response must agree, and each
    claim's (now-redacted) text must still be a complete paragraph of the
    (now-redacted) answer."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    claim_one = "联系电话 13812345678 请尽快回复。"
    claim_two = "另一段落没有敏感信息。"
    answer_text = f"{claim_one}\n\n{claim_two}"
    response = replace(
        base,
        answer=answer_text,
        claims=(
            Claim(
                claim_id="claim-a",
                text=claim_one,
                answer_start=0,
                answer_end=len(claim_one),
                citation_ids=(citation_id,),
            ),
            Claim(
                claim_id="claim-b",
                text=claim_two,
                answer_start=len(claim_one) + 2,
                answer_end=len(claim_one) + 2 + len(claim_two),
                citation_ids=(citation_id,),
            ),
        ),
    )
    step = {"kind": "verified", "response": to_json(response)}

    answers = PhaseAnswers()
    service = _service(answers, redactor=PatternEgressRedactor(personal_data=True))

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert result.abstained is False
    assert "13812345678" not in result.answer
    assert "[REDACTED_PHONE]" in result.answer
    assert result.claims[0].text in result.answer
    assert result.claims[1].text in result.answer
    assert result.claims[1].text == claim_two

    saved = answers.save_calls[0]
    assert saved.answer == result.answer
    assert [claim.text for claim in saved.claims] == [claim.text for claim in result.claims]


@pytest.mark.asyncio
async def test_output_redaction_relocates_sentence_claim_after_prior_hit() -> None:
    """I-1 regression: a PR 3 claim is a cited *sentence* inside a paragraph,
    not a whole paragraph, so re-finding it with `_paragraph_offsets` (whole-
    paragraph match) fails once an earlier redaction hit in the same
    paragraph shifts the text -- the claim then kept its stale pre-redaction
    offset and `save_frozen`'s `_validate_gateway_response` rejected it.
    `_redact_answer_response` must instead locate the claim's own redacted
    text directly in the redacted answer."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    answer_text = "Email claims@example.com for help. The waiting period is 30 days."
    cited_sentence = "The waiting period is 30 days."
    claim_start = answer_text.index(cited_sentence)
    response = replace(
        base,
        answer=answer_text,
        claims=(
            Claim(
                claim_id="claim-a",
                text=cited_sentence,
                answer_start=claim_start,
                answer_end=claim_start + len(cited_sentence),
                citation_ids=(citation_id,),
            ),
        ),
    )
    step = {"kind": "verified", "response": to_json(response)}

    answers = PhaseAnswers()
    service = _service(answers, redactor=PatternEgressRedactor(personal_data=True))

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert result.abstained is False
    assert "claims@example.com" not in result.answer
    assert "[REDACTED_EMAIL]" in result.answer
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.text == cited_sentence
    assert result.answer[claim.answer_start : claim.answer_end] == claim.text

    saved = answers.save_calls[0]
    assert saved.answer == result.answer
    assert [c.text for c in saved.claims] == [c.text for c in result.claims]


@pytest.mark.asyncio
async def test_output_redaction_relocates_sentence_claim_containing_hit() -> None:
    """I-1 regression, second shape: the redaction hit is *inside* the cited
    sentence itself (not just before it), so the claim's redacted text
    differs from its pre-redaction text -- `_redact_answer_response` must
    still find the (now-redacted) claim text in the redacted answer and
    keep it unique and ordered after the previous claim."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    answer_text = "See the notice first. Email claims@example.com before the deadline."
    cited_sentence = "Email claims@example.com before the deadline."
    claim_start = answer_text.index(cited_sentence)
    response = replace(
        base,
        answer=answer_text,
        claims=(
            Claim(
                claim_id="claim-a",
                text=cited_sentence,
                answer_start=claim_start,
                answer_end=claim_start + len(cited_sentence),
                citation_ids=(citation_id,),
            ),
        ),
    )
    step = {"kind": "verified", "response": to_json(response)}

    answers = PhaseAnswers()
    service = _service(answers, redactor=PatternEgressRedactor(personal_data=True))

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert result.abstained is False
    assert "claims@example.com" not in result.answer
    assert "[REDACTED_EMAIL]" in result.answer
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert "[REDACTED_EMAIL]" in claim.text
    assert result.answer[claim.answer_start : claim.answer_end] == claim.text

    saved = answers.save_calls[0]
    assert saved.answer == result.answer
    assert [c.text for c in saved.claims] == [c.text for c in result.claims]


def _sentence_claims(
    answer_text: str, cited: tuple[str, ...], citation_id: str
) -> tuple[Claim, ...]:
    claims = []
    search_from = 0
    for index, sentence in enumerate(cited):
        start = answer_text.index(sentence, search_from)
        search_from = start + len(sentence)
        claims.append(
            Claim(
                claim_id=f"claim-{index}",
                text=sentence,
                answer_start=start,
                answer_end=start + len(sentence),
                citation_ids=(citation_id,),
            )
        )
    return tuple(claims)


@pytest.mark.asyncio
async def test_output_redaction_accepts_an_uncited_sentence_duplicating_a_claim() -> None:
    """PR 3 boundary residual: after redaction an *uncited* sentence before an
    earlier claim's end becomes identical to a later cited one. Searching only
    after the previous claim kept that later claim, and the whole-answer
    validators (one unique span per claim text) then rejected the entire
    answer. The answer must instead be accepted: the claims whose redacted
    text still has one unique span keep their exact spans, and only the claim
    that redaction made indistinguishable by text is dropped."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.ports.answers import AnswerSnapshot
    from tap.platform.serialization.dataclass_json import to_json

    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    uncited = "Call 13812345678 today."
    first = "The waiting period is 30 days."
    second = "Call 13900001111 today."
    answer_text = f"{uncited} {first} {second}"
    response = replace(
        base,
        answer=answer_text,
        claims=_sentence_claims(answer_text, (first, second), citation_id),
    )
    step = {"kind": "verified", "response": to_json(response)}

    answers = PhaseAnswers()
    service = _service(answers, redactor=PatternEgressRedactor(personal_data=True))

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    redacted_duplicate = "Call [REDACTED_PHONE] today."
    assert result.answer == f"{redacted_duplicate} {first} {redacted_duplicate}"
    assert [(c.text, c.answer_start, c.answer_end) for c in result.claims] == [
        (first, len(redacted_duplicate) + 1, len(redacted_duplicate) + 1 + len(first))
    ]
    (saved,) = answers.save_calls
    assert saved.answer == result.answer
    assert [(c.text, c.answer_start) for c in saved.claims] == [
        (c.text, c.answer_start) for c in result.claims
    ]
    # The persisted snapshot passes the same whole-answer gateway validator.
    AnswerSnapshot.from_response(
        response=saved, query=frozen.message, selected_revisions=(ready(),)
    )


@pytest.mark.asyncio
async def test_output_redaction_fails_closed_when_its_only_claim_becomes_ambiguous() -> None:
    """A grounded answer whose only claim cannot be told apart from an uncited
    sentence after redaction has no locatable claim left, so the snapshot the
    real `save_frozen` builds still fails closed instead of persisting an
    uncited grounded answer."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.ports.answers import AnswerSnapshot
    from tap.platform.serialization.dataclass_json import to_json

    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    cited = "Call 13900001111 today."
    answer_text = f"Call 13812345678 today. {cited}"
    response = replace(
        base, answer=answer_text, claims=_sentence_claims(answer_text, (cited,), citation_id)
    )
    step = {"kind": "verified", "response": to_json(response)}
    answers = PhaseAnswers()
    service = _service(answers, redactor=PatternEgressRedactor(personal_data=True))

    await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    (saved,) = answers.save_calls
    assert saved.claims == ()
    with pytest.raises(ValueError, match="grounded answer shape"):
        AnswerSnapshot.from_response(
            response=saved, query=frozen.message, selected_revisions=(ready(),)
        )


@pytest.mark.asyncio
async def test_final_route_redaction_accepts_an_uncited_duplicate_of_a_claim() -> None:
    """The same residual on the direct/model-only `"final"` route, whose
    redacted response is re-validated by the HTTP contract."""
    from tap.contracts.http import RetrievalClaim
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.api import answer_response_to_http

    base = answer_response_to_http(answer_response())
    citation_id = base.citations[0].citation_id
    uncited = "Call 13812345678 today."
    first = "The waiting period is 30 days."
    second = "Call 13900001111 today."
    answer_text = f"{uncited} {first} {second}"
    response = base.model_copy(
        update={
            "answer": answer_text,
            "claims": [
                RetrievalClaim(
                    claim_id=claim.claim_id,
                    text=claim.text,
                    answer_start=claim.answer_start,
                    answer_end=claim.answer_end,
                    citation_ids=[citation_id],
                )
                for claim in _sentence_claims(answer_text, (first, second), citation_id)
            ],
        }
    )
    service = _service(redactor=PatternEgressRedactor(personal_data=True))

    result = await service._redact_final_response(response)

    assert [c.text for c in result.claims] == [first]
    claim = result.claims[0]
    assert result.answer[claim.answer_start : claim.answer_end] == first


@pytest.mark.asyncio
async def test_redaction_never_moves_a_claim_onto_another_sentence() -> None:
    """A claim is anchored at its own redacted position: when the answer's
    redaction at the claim's position differs from the claim's standalone
    redaction, a unique match elsewhere (an uncited sentence) is not the
    claim, so the claim is dropped rather than re-pointed at that sentence."""
    from tap.contracts.http import RetrievalClaim
    from tap.modules.knowledge.api import answer_response_to_http

    class ContextualRedactor:
        _redactions = {
            "Note B. Note A.": "Note A. Note X.",
            "Note B. ": "Note A. ",
            "Note A.": "Note A.",
        }

        async def redact_with_report(self, text):
            return self._redactions.get(text, text), {}

    base = answer_response_to_http(answer_response())
    citation_id = base.citations[0].citation_id
    response = base.model_copy(
        update={
            "answer": "Note B. Note A.",
            "claims": [
                RetrievalClaim(
                    claim_id="claim-a",
                    text="Note A.",
                    answer_start=8,
                    answer_end=15,
                    citation_ids=[citation_id],
                )
            ],
        }
    )
    service = _service(redactor=ContextualRedactor())

    result = await service._redact_final_response(response)

    assert result.answer == "Note A. Note X."
    assert result.claims == []


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


@pytest.mark.asyncio
async def test_output_redaction_records_claims_it_drops_on_the_span(monkeypatch) -> None:
    """A claim dropped because redaction left it without one unique span is
    counted on the redaction span next to the output hits, so the drop is
    observable rather than silent."""
    import tap.interfaces.http.knowledge_service as knowledge_service_module
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    span = _RecordingSpan()
    monkeypatch.setattr(knowledge_service_module, "get_current_span", lambda: span)
    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    first = "The waiting period is 30 days."
    second = "Call 13900001111 today."
    answer_text = f"Call 13812345678 today. {first} {second}"
    response = replace(
        base,
        answer=answer_text,
        claims=_sentence_claims(answer_text, (first, second), citation_id),
    )
    step = {"kind": "verified", "response": to_json(response)}
    service = _service(PhaseAnswers(), redactor=PatternEgressRedactor(personal_data=True))

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert [c.text for c in result.claims] == [first]
    assert span.attributes["tap.guard.redaction.dropped_claims"] == 1
    assert span.attributes["tap.guard.redaction.output_hits"] == json.dumps({"phone": 2})


@pytest.mark.asyncio
async def test_final_route_records_claims_it_drops_on_the_span(monkeypatch) -> None:
    import tap.interfaces.http.knowledge_service as knowledge_service_module
    from tap.contracts.http import RetrievalClaim
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.api import answer_response_to_http

    span = _RecordingSpan()
    monkeypatch.setattr(knowledge_service_module, "get_current_span", lambda: span)
    base = answer_response_to_http(answer_response())
    citation_id = base.citations[0].citation_id
    cited = "Call 13900001111 today."
    answer_text = f"Call 13812345678 today. {cited}"
    response = base.model_copy(
        update={
            "answer": answer_text,
            "claims": [
                RetrievalClaim(
                    claim_id="claim-a",
                    text=cited,
                    answer_start=answer_text.index(cited),
                    answer_end=len(answer_text),
                    citation_ids=[citation_id],
                )
            ],
        }
    )
    service = _service(redactor=PatternEgressRedactor(personal_data=True))

    result = await service._redact_final_response(response)

    assert result.claims == []
    assert span.attributes["tap.guard.redaction.dropped_claims"] == 1


@pytest.mark.asyncio
async def test_output_redaction_without_drops_records_no_dropped_claims(monkeypatch) -> None:
    import tap.interfaces.http.knowledge_service as knowledge_service_module
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    span = _RecordingSpan()
    monkeypatch.setattr(knowledge_service_module, "get_current_span", lambda: span)
    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    cited = "The waiting period is 30 days."
    answer_text = f"Email claims@example.com for help. {cited}"
    response = replace(
        base, answer=answer_text, claims=_sentence_claims(answer_text, (cited,), citation_id)
    )
    step = {"kind": "verified", "response": to_json(response)}
    service = _service(PhaseAnswers(), redactor=PatternEgressRedactor(personal_data=True))

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert [c.text for c in result.claims] == [cited]
    assert "tap.guard.redaction.dropped_claims" not in span.attributes


@pytest.mark.asyncio
async def test_unredactable_claim_prefix_falls_back_to_the_unique_span_checks() -> None:
    """Redacting a claim's answer prefix is only an anchoring aid. A prefix can
    end right after `password=` and so be rejected by the pattern redactor
    even though the whole answer redacts cleanly; that must not fail the
    answer -- the claim is located by the uniqueness and ordering checks alone
    (here the first claim, whose secret value was redacted away, is dropped
    and the second claim keeps its exact span)."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.ports.answers import AnswerSnapshot
    from tap.modules.knowledge.ports.redaction import RedactionUnavailable
    from tap.platform.serialization.dataclass_json import to_json

    redactor = PatternEgressRedactor(personal_data=True)
    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    first = "Hunter2 is rotated monthly."
    second = "The waiting period is 30 days."
    answer_text = f"Set password={first} {second}"
    with pytest.raises(RedactionUnavailable):
        await redactor.redact_with_report(answer_text[: answer_text.index(first)])
    response = replace(
        base,
        answer=answer_text,
        claims=_sentence_claims(answer_text, (first, second), citation_id),
    )
    step = {"kind": "verified", "response": to_json(response)}
    answers = PhaseAnswers()
    service = _service(answers, redactor=redactor)

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert "Hunter2" not in result.answer
    assert [c.text for c in result.claims] == [second]
    claim = result.claims[0]
    assert result.answer[claim.answer_start : claim.answer_end] == second
    (saved,) = answers.save_calls
    AnswerSnapshot.from_response(
        response=saved, query=frozen.message, selected_revisions=(ready(),)
    )


@pytest.mark.asyncio
async def test_unredactable_claim_prefix_still_rejects_a_misplaced_claim() -> None:
    """Without an anchor the fallback still requires one unique span after the
    previous claim; an unredactable prefix never relaxes that."""
    from tap.contracts.http import RetrievalClaim
    from tap.modules.knowledge.api import answer_response_to_http
    from tap.modules.knowledge.ports.redaction import RedactionUnavailable

    class PrefixRejectingRedactor:
        async def redact_with_report(self, text):
            if text == "Note B. ":
                raise RedactionUnavailable("prefix ends inside a credential")
            return {"Note B. Note A.": "Note A. Note A."}.get(text, text), {}

    base = answer_response_to_http(answer_response())
    citation_id = base.citations[0].citation_id
    response = base.model_copy(
        update={
            "answer": "Note B. Note A.",
            "claims": [
                RetrievalClaim(
                    claim_id="claim-a",
                    text="Note A.",
                    answer_start=8,
                    answer_end=15,
                    citation_ids=[citation_id],
                )
            ],
        }
    )
    service = _service(redactor=PrefixRejectingRedactor())

    result = await service._redact_final_response(response)

    assert result.answer == "Note A. Note A."
    assert result.claims == []


@pytest.mark.asyncio
async def test_output_redaction_hits_land_on_current_span(monkeypatch) -> None:
    """Only the answer-body hit counts land on `tap.guard.redaction.output_hits`
    -- a claim is always a verbatim paragraph of the answer, so counting its
    hits too (on top of the answer-level count that already covers the same
    match) would double-count."""
    import tap.interfaces.http.knowledge_service as knowledge_service_module
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    span = _RecordingSpan()
    monkeypatch.setattr(knowledge_service_module, "get_current_span", lambda: span)

    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    claim_text = "联系电话 13812345678 请尽快回复。"
    response = replace(
        base,
        answer=claim_text,
        claims=(
            Claim(
                claim_id="claim-a",
                text=claim_text,
                answer_start=0,
                answer_end=len(claim_text),
                citation_ids=(citation_id,),
            ),
        ),
    )
    step = {"kind": "verified", "response": to_json(response)}
    service = _service(PhaseAnswers(), redactor=PatternEgressRedactor(personal_data=True))

    await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert span.attributes["tap.guard.redaction.output_hits"] == json.dumps({"phone": 1})


@pytest.mark.asyncio
async def test_output_redaction_handles_answers_over_eight_thousand_chars() -> None:
    """The output redactor composed for `finalize_conversation_answer` must be
    bounded like `_redact_model_context` (262144 chars), not the smaller
    8,000-char query-only default `PatternEgressRedactor()` -- a saved answer
    can be as long as `litellm.py`'s `max_answer_chars=16000`, which would
    otherwise make `finalize` raise `RedactionUnavailable` on every long
    answer."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    frozen, plan = await _retrieve_route()
    base = answer_response()
    citation_id = base.citations[0].citation_id
    long_answer = "无敏感信息的长段落。" * 1000
    assert len(long_answer) > 8000
    response = replace(
        base,
        answer=long_answer,
        claims=(
            Claim(
                claim_id="claim-a",
                text=long_answer,
                answer_start=0,
                answer_end=len(long_answer),
                citation_ids=(citation_id,),
            ),
        ),
    )
    step = {"kind": "verified", "response": to_json(response)}

    answers = PhaseAnswers()
    service = _service(answers, redactor=PatternEgressRedactor(max_chars=262144))

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert result.answer == long_answer
    assert answers.save_calls[0].answer == long_answer


@pytest.mark.asyncio
async def test_final_route_answer_is_redacted() -> None:
    """The direct/model-only `"final"` step bypasses `save_frozen` entirely,
    but still returns the answer straight to the caller -- it must be
    redacted the same as the `save_frozen` path."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    class Models:
        async def chat(self, *args, **kwargs):
            return AnswerGeneration(
                "联系电话 13812345678 请回复。", (), "qwen-plus", "direct-chat-v1", None
            )

    frozen, plan = await _direct_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    service = _service(
        PhaseAnswers(), models=Models(), redactor=PatternEgressRedactor(personal_data=True)
    )

    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert step["kind"] == "final"

    result = await service.finalize_conversation_answer(step, frozen, answer_plan=plan)

    assert "13812345678" not in result.answer
    assert "[REDACTED_PHONE]" in result.answer


@pytest.mark.asyncio
async def test_final_route_redaction_drops_unlocatable_claim() -> None:
    """I-1 (updates former M5 test): `_redact_final_response` no longer
    keeps a claim's stale pre-redaction span when its redacted text cannot
    be relocated in the redacted answer -- it drops the claim instead, so
    `model_validate`'s fail-closed check at the end has nothing stale left
    to reject. The answer itself is kept unredacted-shape-wise (still the
    redacted text), just without that claim."""
    from tap.modules.knowledge.api import answer_response_to_http

    class InconsistentRedactor:
        """Redacts the answer unchanged but always returns an unmatched
        value for a claim, so the claim is never locatable in the redacted
        answer."""

        def __init__(self) -> None:
            self.calls = 0

        async def redact_with_report(self, text):
            self.calls += 1
            if self.calls == 1:
                return text, {}
            return "unmatched redacted text", {"phone": 1}

    response = answer_response_to_http(answer_response())
    service = _service(redactor=InconsistentRedactor())

    result = await service._redact_final_response(response)

    assert result.answer == response.answer
    assert result.claims == []


@pytest.mark.asyncio
async def test_redact_final_response_relocates_sentence_claim_after_prior_hit() -> None:
    """I-1 regression for the `"final"` route's shared helper: a sentence
    claim inside a paragraph, with a redaction hit earlier in that
    paragraph, must still be relocated in the redacted answer rather than
    keeping its stale pre-redaction offset."""
    from tap.contracts.http import RetrievalClaim
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.api import answer_response_to_http

    base = answer_response_to_http(answer_response())
    citation_id = base.citations[0].citation_id
    answer_text = "Email claims@example.com for help. The waiting period is 30 days."
    cited_sentence = "The waiting period is 30 days."
    claim_start = answer_text.index(cited_sentence)
    response = base.model_copy(
        update={
            "answer": answer_text,
            "claims": [
                RetrievalClaim(
                    claim_id="claim-a",
                    text=cited_sentence,
                    answer_start=claim_start,
                    answer_end=claim_start + len(cited_sentence),
                    citation_ids=[citation_id],
                )
            ],
        }
    )
    service = _service(redactor=PatternEgressRedactor(personal_data=True))

    result = await service._redact_final_response(response)

    assert "claims@example.com" not in result.answer
    assert "[REDACTED_EMAIL]" in result.answer
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.text == cited_sentence
    assert result.answer[claim.answer_start : claim.answer_end] == claim.text


@pytest.mark.asyncio
async def test_redact_final_response_relocates_sentence_claim_containing_hit() -> None:
    """I-1 regression, second shape, for the `"final"` route's shared
    helper: the redaction hit is inside the cited sentence itself."""
    from tap.contracts.http import RetrievalClaim
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.api import answer_response_to_http

    base = answer_response_to_http(answer_response())
    citation_id = base.citations[0].citation_id
    answer_text = "See the notice first. Email claims@example.com before the deadline."
    cited_sentence = "Email claims@example.com before the deadline."
    claim_start = answer_text.index(cited_sentence)
    response = base.model_copy(
        update={
            "answer": answer_text,
            "claims": [
                RetrievalClaim(
                    claim_id="claim-a",
                    text=cited_sentence,
                    answer_start=claim_start,
                    answer_end=claim_start + len(cited_sentence),
                    citation_ids=[citation_id],
                )
            ],
        }
    )
    service = _service(redactor=PatternEgressRedactor(personal_data=True))

    result = await service._redact_final_response(response)

    assert "claims@example.com" not in result.answer
    assert "[REDACTED_EMAIL]" in result.answer
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert "[REDACTED_EMAIL]" in claim.text
    assert result.answer[claim.answer_start : claim.answer_end] == claim.text


@pytest.mark.asyncio
async def test_final_route_redaction_failure_propagates() -> None:
    """Fail-closed: if the direct/model-only answer can't be redacted (an
    unterminated credential-looking value, here), `RedactionUnavailable`
    must propagate out of `finalize_conversation_answer`, not be swallowed
    or return the unredacted answer."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.ports.redaction import RedactionUnavailable

    class Models:
        async def chat(self, *args, **kwargs):
            return AnswerGeneration(
                'password="unterminated', (), "qwen-plus", "direct-chat-v1", None
            )

    frozen, plan = await _direct_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    service = _service(PhaseAnswers(), models=Models(), redactor=PatternEgressRedactor())

    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert step["kind"] == "final"

    with pytest.raises(RedactionUnavailable):
        await service.finalize_conversation_answer(step, frozen, answer_plan=plan)


@pytest.mark.asyncio
async def test_prepared_step_carries_retrieval_hits_labels_for_evidence_and_relations() -> None:
    """PR 3: a `"prepared"` step must carry a `retrieval_hits` payload the
    `seed_search` graph node can emit as `retrieval.hits_ready` immediately,
    before `generate_conversation_answer` ever runs -- one label per
    `S`-evidence chunk (title, location from its anchor) and one per
    `R`-relation (title built from its subject/relation/object), built
    from the evidence/relation context while it is still at hand (before
    `encode_bundle` turns it opaque)."""
    from tap.modules.knowledge.application.relation_analysis import (
        RelationContext,
        RelationEvidence,
        RelationSupport,
    )
    from tap.modules.knowledge.domain.models import (
        ContentRole,
        DocumentAnchor,
        Evidence,
        IndexRevision,
        RelationContextStatus,
        RevisionKind,
        SourceFamily,
        SourceRevisionRef,
    )

    source_hash = "sha256:" + "a" * 64
    chunk_hash = "sha256:" + "b" * 64
    doc_evidence = Evidence(
        family=SourceFamily.DOC,
        chunk_id="h_" + "1" * 64,
        logical_chunk_id="h_" + "2" * 64,
        title="理赔手册.pdf",
        content="理赔需在 30 日内提交。",
        source=SourceRevisionRef(
            source_id="doc-a",
            source_type="document",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision="rev-1",
            source_content_hash=source_hash,
            anchor=DocumentAnchor(heading_path=("理赔流程", "提交材料"), page=3),
        ),
        chunk_content_hash=chunk_hash,
        content_role=ContentRole.SOURCE,
        citation_id="citation-1",
        evidence_label="S1",
        index_revision=IndexRevision(
            physical_index="kb-doc-v1", schema_version="search-schema-v1", corpus_version="v1"
        ),
        embedding_model_version="tap-embed-fixed-v1",
        acl_decision_id="decision-1",
        score=0.9,
    )
    relation = RelationContext(
        status=RelationContextStatus.APPLIED,
        graph_version="7",
        relations=(
            RelationEvidence(
                label="R1",
                edge_id="edge-1",
                subject_node_id="node-a",
                subject_label="核保流程",
                subject_aliases=(),
                object_node_id="node-b",
                object_label="健康告知",
                object_aliases=(),
                relation_type="requires",
                relation_label="需要",
                origin="project",
                confidence=0.8,
                support=(
                    RelationSupport(
                        chunk_id="h_" + "3" * 64,
                        source_revision_id="rev-1",
                        document_revision_id="rev-1",
                        content_digest=chunk_hash,
                        anchor={},
                        evidence_label="S1",
                    ),
                ),
                on_path=True,
            ),
        ),
    )

    class RelationAndEvidencePhaseAnswers(PhaseAnswers):
        async def prepare_frozen(self, request, revisions, policy, **kwargs):
            bundle = await super().prepare_frozen(request, revisions, policy, **kwargs)
            return replace(
                bundle,
                response=replace(bundle.response, evidence=(doc_evidence,)),
                relation=relation,
            )

    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)
    service = _service(RelationAndEvidencePhaseAnswers())

    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)

    assert step["kind"] == "prepared"
    hits = step["retrieval_hits"]
    assert hits["traceId"] == "trace-a"
    assert hits["authorizedHitCount"] == 1
    assert hits["labels"] == [
        {"label": "S1", "title": "理赔手册.pdf", "location": "理赔流程 / 提交材料"},
        {"label": "R1", "title": "核保流程 需要 健康告知", "location": None},
    ]


class ToolPhaseAnswers(PhaseAnswers):
    """A `PhaseAnswers` double additionally standing in for the `knowledge.search`
    /`graph.relations` tool surface (`search_more_frozen`/`relations_frozen`),
    for `run_conversation_tool` tests."""

    def __init__(
        self,
        *,
        search_result: object = None,
        relations_result: object = None,
        relations_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._search_result = search_result
        self._relations_result = relations_result
        self._relations_error = relations_error
        self.search_calls: list[dict[str, object]] = []
        self.relations_calls: list[dict[str, object]] = []

    async def search_more_frozen(
        self, bundle, query, source_ids, revisions, policy, *, answer_execution=None, authorize=None
    ):
        self.search_calls.append({"bundle": bundle, "query": query, "source_ids": source_ids})
        if authorize is not None:
            await authorize()
        return self._search_result

    async def relations_frozen(
        self,
        bundle,
        query,
        revisions,
        policy,
        *,
        authorize=None,
        turn_id=None,
        answer_execution=None,
    ):
        self.relations_calls.append({"bundle": bundle, "query": query, "turn_id": turn_id})
        if authorize is not None:
            await authorize()
        if self._relations_error is not None:
            raise self._relations_error
        return self._relations_result


@pytest.mark.asyncio
async def test_run_conversation_tool_search_returns_prepared_step_and_hits() -> None:
    """Task 6: a `knowledge.search` tool call grows the evidence pool and
    returns a fresh `"prepared"` step; `retrieval_hits` carries only the
    newly added labels, and the summary reports the count and label range."""
    from tap.modules.ai.domain.tool_calls import AdmittedToolCall
    from tap.modules.knowledge.application.evidence_bundle import encode_bundle
    from tap.modules.knowledge.domain.models import (
        ContentRole,
        DocumentAnchor,
        Evidence,
        IndexRevision,
        RevisionKind,
        SourceFamily,
        SourceRevisionRef,
    )

    frozen, plan = await _retrieve_route()
    bundle = _bundle(query=frozen.message)
    step = {
        "kind": "prepared",
        "bundle": encode_bundle(bundle),
        "retrieval_hits": {},
        "transcript": [],
    }

    source_hash = "sha256:" + "a" * 64
    chunk_hash = "sha256:" + "b" * 64

    def new_evidence_item(index: int) -> Evidence:
        return Evidence(
            family=SourceFamily.DOC,
            chunk_id=f"h_{index}" + "1" * 60,
            logical_chunk_id=f"h_{index}" + "2" * 60,
            title="新文档.pdf",
            content="新检索到的内容。",
            source=SourceRevisionRef(
                source_id="doc-a",
                source_type="document",
                revision_kind=RevisionKind.BLOB_VERSION,
                revision="rev-1",
                source_content_hash=source_hash,
                anchor=DocumentAnchor(heading_path=("章节",)),
            ),
            chunk_content_hash=chunk_hash,
            content_role=ContentRole.SOURCE,
            citation_id=f"citation-new-{index}",
            evidence_label=f"S{index}",
            index_revision=IndexRevision(
                physical_index="kb-doc-v1", schema_version="search-schema-v1", corpus_version="v1"
            ),
            embedding_model_version="tap-embed-fixed-v1",
            acl_decision_id="decision-1",
            score=0.8,
        )

    new_evidence = tuple(new_evidence_item(index) for index in (5, 6, 7))
    new_bundle = replace(bundle, response=replace(bundle.response, evidence=new_evidence))

    answers = ToolPhaseAnswers(search_result=(new_bundle, new_evidence))
    service = _service(answers)
    call = AdmittedToolCall(
        call_id="call-1", tool_id="knowledge.search", arguments={"query": "更多信息"}
    )

    result = await service.run_conversation_tool(step, frozen, call, answer_plan=plan)

    assert result["status"] == "completed"
    assert result["step"]["kind"] == "prepared"
    assert result["added_labels"] == ["S5", "S6", "S7"]
    assert "+3" in result["summary"]
    assert [item["label"] for item in result["retrieval_hits"]["labels"]] == ["S5", "S6", "S7"]


@pytest.mark.asyncio
async def test_run_conversation_tool_relations_unavailable_fails_softly() -> None:
    """Task 6: when the `graph.relations` tool fails (no configured relation
    analysis port here), `run_conversation_tool` must not raise -- it reports
    `status="failed"` and leaves the step's `bundle` untouched."""
    from tap.modules.ai.domain.tool_calls import AdmittedToolCall
    from tap.modules.knowledge.application.evidence_bundle import encode_bundle
    from tap.modules.knowledge.application.retrieve import RelationAnalysisUnavailable

    frozen, plan = await _retrieve_route()
    bundle = _bundle(query=frozen.message)
    step = {
        "kind": "prepared",
        "bundle": encode_bundle(bundle),
        "retrieval_hits": {},
        "transcript": [],
    }

    answers = ToolPhaseAnswers(relations_error=RelationAnalysisUnavailable("not configured"))
    service = _service(answers)
    call = AdmittedToolCall(
        call_id="call-1", tool_id="graph.relations", arguments={"query": "关系"}
    )

    result = await service.run_conversation_tool(step, frozen, call, answer_plan=plan)

    assert result["status"] == "failed"
    assert result["step"]["bundle"] == step["bundle"]


@pytest.mark.asyncio
async def test_add_insights_records_dedupes_and_continues_label_sequence() -> None:
    """Task 8: `insights.query` results grow the evidence pool through the
    real `KnowledgeHttpService.add_insights_records` -- labels continue
    `I<n>` without ever relabeling an existing record, and a repeated
    `(queryId, metricId)` is deduped rather than appended again."""
    from tap.modules.knowledge.application.evidence_bundle import decode_bundle, encode_bundle

    frozen, _plan = await _retrieve_route()
    bundle = _bundle(query=frozen.message)
    bundle = replace(
        bundle,
        insights_records=(
            {
                "label": "I1",
                "queryId": "query-a",
                "metricVersion": "insights-metrics-v1",
                "metricId": "first_pass_rate",
                "value": 0.5,
                "asOf": "2026-09-25T08:00:00+00:00",
            },
        ),
    )
    step = {"kind": "prepared", "bundle": encode_bundle(bundle), "transcript": []}
    service = _service()

    new_step, added_labels = await service.add_insights_records(
        step,
        (
            {
                "queryId": "query-a",
                "metricVersion": "insights-metrics-v1",
                "metricId": "first_pass_rate",
                "value": 0.5,
                "asOf": "2026-09-25T08:00:00+00:00",
            },
            {
                "queryId": "query-a",
                "metricVersion": "insights-metrics-v1",
                "metricId": "p95_duration_seconds",
                "value": 3.2,
                "asOf": "2026-09-25T08:00:00+00:00",
            },
        ),
    )

    assert added_labels == ["I2"]
    new_bundle = decode_bundle(new_step["bundle"])
    assert [item["label"] for item in new_bundle.insights_records] == ["I1", "I2"]
    assert new_bundle.insights_records[0]["metricId"] == "first_pass_rate"
    assert new_bundle.insights_records[1]["metricId"] == "p95_duration_seconds"


class ToolCallPhaseAnswers(PhaseAnswers):
    """A `PhaseAnswers` double whose `generate_frozen` plays the model asking
    to call a tool (Task 7): accepts and records the `tools`/`transcript`
    kwargs `generate_conversation_answer` forwards, and returns an
    `AnswerGeneration` carrying `tool_calls` whenever `tools` is non-empty."""

    def __init__(self) -> None:
        super().__init__()
        self.generate_tools: list[tuple[str, ...]] = []
        self.generate_transcripts: list[tuple[object, ...]] = []

    async def generate_frozen(
        self,
        bundle,
        revisions,
        policy,
        *,
        governance,
        model_alias=None,
        answer_execution=None,
        authorize=None,
        persona=None,
        tools: tuple[str, ...] = (),
        transcript: tuple[object, ...] = (),
    ):
        self.generate_calls += 1
        self.generate_tools.append(tools)
        self.generate_transcripts.append(transcript)
        if authorize is not None:
            await authorize()
        if tools:
            return AnswerGeneration(
                "",
                (),
                "qwen-plus",
                "quick-hybrid-v1",
                "provider-request-a",
                tool_calls=(GeneratedToolCall("c1", "knowledge_search", '{"query":"q"}'),),
            )
        return AnswerGeneration(
            "The rule is grounded.",
            (GeneratedClaim("The rule is grounded.", ("S1",)),),
            "qwen-plus",
            "quick-hybrid-v1",
            "provider-request-a",
        )


@pytest.mark.asyncio
async def test_generate_with_tools_yields_tool_calls_step() -> None:
    """Task 7: when `generate_conversation_answer` is called with `tools` and
    the model answers with tool calls, it returns a `"tool_calls"` step
    carrying `pending_tool_calls` and the extended `transcript` -- not a
    `"generated"` step."""
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)

    answers = ToolCallPhaseAnswers()
    service = _service(answers)
    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    step = await service.generate_conversation_answer(
        step, frozen, answer_plan=plan, tools=("knowledge.search",)
    )

    assert step["kind"] == "tool_calls"
    assert step["pending_tool_calls"] == [
        {"callId": "c1", "name": "knowledge_search", "argumentsJson": '{"query":"q"}'}
    ]
    assert step["transcript"][-1]["role"] == "assistant"
    assert step["transcript"][-1]["toolCalls"] == step["pending_tool_calls"]
    assert answers.generate_tools[-1] == ("knowledge.search",)


class _RealChainEmbeddings:
    """A minimal embedding port for the real-chain C-1 test below -- only the
    model gateway (`answers`) and search port are meant to be fakes; this
    embedding double exists purely so `AuthorizedRetrieval.prepare_answer`'s
    real retrieval pipeline has something to embed the query with."""

    embedding_model_id = "tap-embedding-v1"
    embedding_dimension = 2

    async def embed(self, query: str) -> Embedding:
        return Embedding(
            vector=(0.1, 0.2), model_id=self.embedding_model_id, provider_request_id="embed-1"
        )


class _RealChainSearch:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self._hits = hits

    async def search(self, execution) -> tuple[SearchHit, ...]:
        return self._hits


class _RealChainToolAnswers:
    """Stands in for the model gateway at the bottom of the real
    `AnswerService` -> `KnowledgeAPI` -> `AuthorizedRetrieval` chain: records
    every `.answer()` kwarg it receives (so the test can assert `tools`/
    `transcript` actually arrived) and returns tool calls whenever `tools`
    is non-empty, exactly like a model asking to call a tool mid-turn."""

    def __init__(self) -> None:
        self.answer_kwargs: list[dict[str, object]] = []

    async def answer(self, query, evidence, profile_id, **kwargs: object) -> AnswerGeneration:
        del query, evidence, profile_id
        self.answer_kwargs.append(kwargs)
        if kwargs.get("tools"):
            return AnswerGeneration(
                "",
                (),
                "qwen-plus",
                "quick-hybrid-v1",
                "provider-request-real-chain",
                tool_calls=(GeneratedToolCall("c1", "knowledge_search", '{"query":"q"}'),),
            )
        return AnswerGeneration(
            "The rule is grounded.",
            (GeneratedClaim("The rule is grounded.", ("S1",)),),
            "qwen-plus",
            "quick-hybrid-v1",
            "provider-request-real-chain",
        )


class _PassthroughRedactor:
    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(sanitized_text=text, redaction_version="redaction-v1")


class _CurrentPolicyVerifier:
    async def verify_current(self, expected):
        return expected


def _real_chain_search_hit(revision, *, variant: int = 1) -> SearchHit:
    content = (
        "错误码 E104 代表核保材料缺失。"
        if variant == 1
        else "错误码 E104 需补充体检报告后方可继续核保。"
    )
    return SearchHit(
        family=SourceFamily.DOC,
        chunk_id="h_" + str(variant) + "3" * 63,
        logical_chunk_id="h_" + str(variant) + "4" * 63,
        title="理赔手册.pdf",
        content=content,
        source=SourceRevisionRef(
            source_id=revision.source_id,
            source_type="document",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision.revision_id,
            source_content_hash=revision.source_content_hash,
            anchor=DocumentAnchor(heading_path=("理赔流程",)),
        ),
        # Must equal the real `sha256:`-prefixed hash of `content` --
        # `AuthorizedRetrieval._hit_has_compatible_provenance` checks this
        # before admitting the hit as evidence.
        chunk_content_hash="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index="kb-doc-v1",
            schema_version="search-schema-v1",
            corpus_version="tapper-demo-v1",
        ),
        embedding_model_version="tap-embedding-v1",
        score=0.9,
    )


@pytest.mark.asyncio
async def test_generate_conversation_answer_threads_tools_through_the_real_chain() -> None:
    """C-1 (Task 7 fix round 1): `KnowledgeHttpService.generate_conversation_answer`
    must thread `tools`/`transcript` all the way through the *real*
    `AnswerService` -> `KnowledgeAPI` -> `AuthorizedRetrieval` chain (only the
    model gateway and search port are fakes here) without either
    intermediate layer raising `TypeError` for an unexpected kwarg -- and the
    model gateway at the bottom must actually receive what was offered."""
    frozen, plan = await _retrieve_route()
    revision = ready()

    answers_port = _RealChainToolAnswers()
    knowledge_api = KnowledgeAPI(
        search=_RealChainSearch((_real_chain_search_hit(revision),)),
        embeddings=_RealChainEmbeddings(),
        answers=answers_port,
        policy_verifier=_CurrentPolicyVerifier(),
        redactor=_PassthroughRedactor(),
        id_factory=lambda: str(uuid4()),
    )
    repository = MemoryAnswerRepository((revision,))
    repository.scope = VALIDATION_SCOPE  # type: ignore[attr-defined]
    answer_service = AnswerService(repository=repository, knowledge=knowledge_api)
    service = _service(answer_service)

    request = RetrievalAnswerRequest(query=frozen.message)
    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert step["kind"] == "prepared"

    step = await service.generate_conversation_answer(
        step,
        frozen,
        answer_plan=plan,
        tools=("knowledge.search",),
    )

    assert step["kind"] == "tool_calls"
    assert step["pending_tool_calls"] == [
        {"callId": "c1", "name": "knowledge_search", "argumentsJson": '{"query":"q"}'}
    ]
    assert answers_port.answer_kwargs[-1]["tools"] == ("knowledge.search",)


class _RoundAwareRealChainSearch:
    """`SearchPort` double returning a distinct hit per call (I-2 fix round
    1): `seed_search`'s initial retrieval and the `knowledge.search` tool
    round each call `search()` once, and the test must prove the tool
    round actually grows the evidence pool through the real pipeline, not
    just a deduped repeat of the seed hit."""

    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self._hits = hits
        self.calls = 0

    async def search(self, execution) -> tuple[SearchHit, ...]:
        hit = self._hits[min(self.calls, len(self._hits) - 1)]
        self.calls += 1
        return (hit,)


class _RealChainToolThenTextAnswers:
    """Model gateway double at the bottom of the real `AnswerService` ->
    `KnowledgeAPI` -> `AuthorizedRetrieval` chain: the first `.answer()`
    call asks to call `knowledge.search` (regardless of what `tools` it
    was offered), the second returns a grounded text answer -- recording
    every kwarg (`tools`, `transcript`, ...) each call received so the
    test can assert the second call's `transcript` actually carries the
    first round's tool result."""

    def __init__(self) -> None:
        self.calls = 0
        self.answer_kwargs: list[dict[str, object]] = []

    async def answer(self, query, evidence, profile_id, **kwargs: object) -> AnswerGeneration:
        del query, evidence, profile_id
        self.calls += 1
        self.answer_kwargs.append(kwargs)
        if self.calls == 1:
            return AnswerGeneration(
                "",
                (),
                "qwen-plus",
                "quick-hybrid-v1",
                "provider-request-real-chain-loop-1",
                tool_calls=(GeneratedToolCall("c1", "knowledge_search", '{"query":"q"}'),),
            )
        return AnswerGeneration(
            "The rule is grounded.",
            (GeneratedClaim("The rule is grounded.", ("S1",)),),
            "qwen-plus",
            "quick-hybrid-v1",
            "provider-request-real-chain-loop-2",
        )


class _EmittingConversations:
    """Minimal `conversations` double for `ChatTurnNodes`: just enough to
    satisfy `_emit_committed`'s `conversations.emit` call (`seed_search`'s
    `retrieval.hits_ready` and `tools`' `task.step` events)."""

    scope = VALIDATION_SCOPE

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, object]]] = []

    async def emit(self, conversation_id, turn_id, event_type, payload, *, lease_token=None):
        self.emitted.append((event_type, dict(payload)))


@pytest.mark.asyncio
async def test_tool_loop_runs_through_the_real_chain_and_informs_the_second_model_call() -> None:
    """I-2 fix round 1: drive the bounded tool-calling loop through *real*
    `ChatTurnNodes` -> `KnowledgeHttpService` -> `AnswerService` ->
    `KnowledgeAPI` -> `AuthorizedRetrieval`, faking only the model gateway
    and the search port. `model` returns `tool_calls`, `tools` actually
    runs `knowledge.search` through the real retrieval pipeline and grows
    the evidence pool, and the second `model` call must see the first
    round's tool result message in the `transcript` it forwards to the
    model gateway."""
    frozen, _unused_plan = await _retrieve_route()
    revision = ready()

    answers_port = _RealChainToolThenTextAnswers()
    search_port = _RoundAwareRealChainSearch(
        (
            _real_chain_search_hit(revision, variant=1),
            _real_chain_search_hit(revision, variant=2),
        )
    )
    knowledge_api = KnowledgeAPI(
        search=search_port,
        embeddings=_RealChainEmbeddings(),
        answers=answers_port,
        policy_verifier=_CurrentPolicyVerifier(),
        redactor=_PassthroughRedactor(),
        id_factory=lambda: str(uuid4()),
    )
    repository = MemoryAnswerRepository((revision,))
    repository.scope = VALIDATION_SCOPE  # type: ignore[attr-defined]
    answer_service = AnswerService(repository=repository, knowledge=knowledge_api)
    service = _service(answer_service)

    async def require_authorized() -> None:
        return None

    turn = SimpleNamespace(
        turn_id="turn-real-chain-loop",
        lease_token="lease-real-chain-loop",
        attempt=1,
        input_snapshot=SimpleNamespace(snapshot_id="snapshot-real-chain-loop", value=frozen),
    )
    conversations = _EmittingConversations()
    nodes = ChatTurnNodes(
        conversation_id="conversation-real-chain-loop",
        turn=turn,
        conversations=conversations,
        steps=service,
        planner=None,
        knowledge=object(),
        insights_explanation=None,
        require_authorized=require_authorized,
    )

    state: dict[str, object] = {
        "tools": ["knowledge.search"],
        "budget": {"maxToolRounds": 6},
        "budget_used": {"modelCalls": 0, "costMicros": 0, "toolRounds": 0},
    }
    state.update(await nodes.seed_search(state))
    assert state["step"]["kind"] == "prepared"

    state.update(await nodes.model(state))
    assert state["step"]["kind"] == "tool_calls"
    assert state["step"]["pending_tool_calls"] == [
        {"callId": "c1", "name": "knowledge_search", "argumentsJson": '{"query":"q"}'}
    ]

    state.update(await nodes.tools(state))
    assert state["step"]["kind"] == "prepared"
    assert state["step"]["transcript"][-1]["role"] == "tool"
    tool_message = json.loads(state["step"]["transcript"][-1]["content"])
    assert tool_message["status"] == "completed"
    assert search_port.calls == 2

    # The graph's own `_run_budgeted` normally folds `tool_rounds_delta`
    # into `budget_used.toolRounds` between node calls; driving nodes
    # directly here, the test advances it itself before the second `model`
    # call so `tool_rounds_used < maxToolRounds` still holds.
    state["budget_used"] = {"modelCalls": 0, "costMicros": 0, "toolRounds": 1}
    state.update(await nodes.model(state))

    assert state["step"]["kind"] == "generated"
    assert answers_port.calls == 2
    second_call_transcript = answers_port.answer_kwargs[-1]["transcript"]
    assert [item["role"] for item in second_call_transcript] == ["assistant", "tool"]
    assert json.loads(second_call_transcript[-1]["content"])["status"] == "completed"

    task_step_statuses = [
        payload["status"]
        for event_type, payload in conversations.emitted
        if event_type == "task.step"
    ]
    assert task_step_statuses == ["running", "completed"]


# Final review M-2: an 18-character resident ID starting at offset 45 of the
# query straddles the 60-character summary cut -- truncating first would leave
# a 15-digit fragment the ID pattern no longer recognizes.
_STRADDLING_ID = "11010119900307123X"
_STRADDLING_QUERY = "a" * 44 + " " + _STRADDLING_ID + " tail"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_id", ["knowledge.search", "graph.relations"])
async def test_run_conversation_tool_summary_redacts_before_truncating(tool_id) -> None:
    from tap.modules.ai.domain.tool_calls import AdmittedToolCall
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.modules.knowledge.application.evidence_bundle import encode_bundle
    from tap.modules.knowledge.application.retrieve import RelationAnalysisUnavailable

    frozen, plan = await _retrieve_route()
    bundle = _bundle(query=frozen.message)
    step = {
        "kind": "prepared",
        "bundle": encode_bundle(bundle),
        "retrieval_hits": {},
        "transcript": [],
    }
    answers = ToolPhaseAnswers(
        search_result=(bundle, ()),
        relations_error=RelationAnalysisUnavailable("not configured"),
    )
    service = _service(
        answers, redactor=PatternEgressRedactor(max_chars=262144, personal_data=True)
    )
    call = AdmittedToolCall(
        call_id="call-1", tool_id=tool_id, arguments={"query": _STRADDLING_QUERY}
    )

    result = await service.run_conversation_tool(step, frozen, call, answer_plan=plan)

    assert "[REDACTED_ID]" in result["summary"]
    assert "1101011990" not in result["summary"]
    # The tool itself still runs on the original query.
    calls = answers.search_calls if tool_id == "knowledge.search" else answers.relations_calls
    assert calls[0]["query"] == _STRADDLING_QUERY


class _PacedSSE(httpx.AsyncByteStream):
    """httpx byte stream for a LiteLLM SSE response: each chunk arrives
    `delay` seconds after the previous one; `stall_after` chunks then hang."""

    def __init__(self, texts: tuple[str, ...], *, delay: float, stall_after: int | None = None):
        self._texts = texts
        self._delay = delay
        self._stall_after = stall_after

    async def __aiter__(self):
        import asyncio

        for index, text in enumerate(self._texts):
            if self._stall_after is not None and index == self._stall_after:
                await asyncio.Event().wait()
            await asyncio.sleep(self._delay)
            last = index == len(self._texts) - 1
            chunk = {
                "model": "dashscope/qwen-plus",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": text},
                        "finish_reason": "stop" if last else None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        return None


def _streaming_answer_models(stream: _PacedSSE, *, idle_seconds: float):
    """The real `KnowledgeModelGateway` over the real `LiteLLMModelGateway`,
    with only LiteLLM's HTTP responses faked."""
    from tap.modules.ai.adapters.litellm import LiteLLMModelGateway, LiteLLMModelGatewayConfig
    from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, ModelRoles
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    models = [
        {
            "model_name": name,
            "litellm_params": {"model": f"dashscope/{name}"},
            "model_info": {"id": f"deployment-{name}", "mode": mode, **extra},
        }
        for name, mode, extra in (
            ("qwen-plus", "chat", {"supports_response_schema": True}),
            ("text-embedding-v4", "embedding", {}),
        )
    ]

    def catalog_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": models})

    def chat_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def redact(text: str) -> str:
        return (await PatternEgressRedactor().redact(text)).sanitized_text

    async def passthrough(text: str) -> str:
        return text

    gateway = LiteLLMModelGateway(
        LiteLLMModelGatewayConfig(
            base_url="https://litellm.example",
            api_key="private-provider-key",
            roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
            embedding_dimension=2,
            stream_idle_seconds=idle_seconds,
        ),
        scope=VALIDATION_SCOPE,
        redact=redact,
        catalog=LiteLLMCatalog(
            base_url="https://litellm.example",
            api_key="private-provider-key",
            client=httpx.AsyncClient(transport=httpx.MockTransport(catalog_response)),
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(chat_response)),
    )
    return KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=passthrough,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=1,
    )


async def _planned_turn_nodes(stream: _PacedSSE, *, idle_seconds: float, plan_window: float):
    """Real `ChatTurnNodes` -> `KnowledgeHttpService` -> `AnswerService` ->
    `KnowledgeAPI` -> `KnowledgeModelGateway` -> `LiteLLMModelGateway` with an
    attached planner and an admitted plan whose own window is `plan_window`
    seconds -- shorter than the streamed answer -- inside a 30 s Turn budget."""
    frozen, plan = await _retrieve_route()
    revision = ready()
    knowledge_api = KnowledgeAPI(
        search=_RealChainSearch((_real_chain_search_hit(revision),)),
        embeddings=_RealChainEmbeddings(),
        answers=_streaming_answer_models(stream, idle_seconds=idle_seconds),
        policy_verifier=_CurrentPolicyVerifier(),
        redactor=_PassthroughRedactor(),
        id_factory=lambda: str(uuid4()),
    )
    repository = MemoryAnswerRepository((revision,))
    repository.scope = VALIDATION_SCOPE  # type: ignore[attr-defined]
    service = _service(AnswerService(repository=repository, knowledge=knowledge_api))

    async def require_authorized() -> None:
        return None

    turn = SimpleNamespace(
        turn_id="turn-a",
        lease_token="lease-a",
        attempt=1,
        input_snapshot=TurnInputSnapshot(
            "snapshot-a",
            VALIDATION_SCOPE.project_id,
            "turn-a",
            frozen,
            content_digest(
                frozen.material(project_id=VALIDATION_SCOPE.project_id, turn_id="turn-a")
            ),
            datetime.now(UTC),
        ),
    )
    nodes = ChatTurnNodes(
        conversation_id="conversation-a",
        turn=turn,
        conversations=_EmittingConversations(),
        steps=service,
        planner=object(),
        knowledge=object(),
        insights_explanation=None,
        require_authorized=require_authorized,
        attempt_started_at=time.time(),
    )
    state: dict[str, object] = {
        "answer_plan": replace(plan, deadline_at=time.time() + plan_window).to_dict(),
        "plan_attempt": 1,
        "tools": [],
        "budget": {
            "maxToolRounds": 6,
            "maxSeconds": 30.0,
            "maxModelCalls": 10,
            "maxCostMicros": 0,
            "startedAtMs": int(time.time() * 1000),
        },
        "budget_used": {"modelCalls": 0, "costMicros": 0, "toolRounds": 0},
    }
    state.update(await nodes.seed_search(state))
    assert state["step"]["kind"] == "prepared"
    return nodes, state


@pytest.mark.asyncio
async def test_planned_turn_streams_an_answer_longer_than_the_plan_window() -> None:
    """Capability-4 fix round 1 (D3 end to end): with a planner attached, the
    admitted plan's own window (30 s in production, 0.3 s here) must not cut
    a slow-but-steady streamed answer that is still inside the Turn's
    wall-clock budget; generation and verification complete."""
    pieces = ("错误码 E104 ", "代表", "核保", "材料", "缺失", "[S1]。")
    nodes, state = await _planned_turn_nodes(
        _PacedSSE(pieces, delay=0.12), idle_seconds=0.5, plan_window=0.3
    )
    started = time.monotonic()

    state.update(await nodes.model(state))
    state.update(await nodes.verify(state))

    assert time.monotonic() - started > 0.6
    assert state["step"]["kind"] == "verified"
    assert len(state["step"]["response"]["claims"]) == 1


@pytest.mark.asyncio
async def test_planned_turn_still_fails_a_stalled_stream_on_the_idle_timeout() -> None:
    from tap.modules.knowledge.ports.errors import AnswerUnavailable

    pieces = ("错误码 E104 ", "代表", "核保材料缺失[S1]。")
    nodes, state = await _planned_turn_nodes(
        _PacedSSE(pieces, delay=0.01, stall_after=1), idle_seconds=0.2, plan_window=10
    )
    started = time.monotonic()

    with pytest.raises(AnswerUnavailable):
        await nodes.model(state)

    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_plan_window_expiring_mid_phase_is_a_deterministic_budget_exhaustion() -> None:
    """Capability-4 fix round 1: the plan window now equals the Turn's
    wall-clock end, so it may expire in the same instant as the Turn budget.
    Expiring mid-phase must surface as `AnswerBudgetExhausted` (classified
    deterministic, `budget-exhausted`), never as a bare `TimeoutError` the
    worker would retry as transient."""
    import asyncio

    from tap.entrypoints.tapper_turn_failures import classify_turn_failure
    from tap.interfaces.http.knowledge_service import AnswerBudgetExhausted

    _frozen, plan = await _retrieve_route()
    short = replace(plan, deadline_at=time.time() + 0.05)

    with pytest.raises(AnswerBudgetExhausted) as raised:
        async with KnowledgeHttpService._plan_deadline(short):
            await asyncio.sleep(1)

    assert classify_turn_failure(raised.value) == "deterministic"
