"""`KnowledgeHttpService.answer_conversation` split into resumable, JSON-serializable
prepare / generate / verify / finalize steps so a later LangGraph node can call each
phase on its own, checkpointing `ConversationAnswerStep` between them."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_answer_service import answer_response, ready

from tap.contracts.http import AbstentionReason, RetrievalAnswerRequest
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.plan_answer import AnswerPlanner, PlanningInput, planning_input
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.chat.domain.conversations import (
    FrozenResource,
    TurnInput,
    TurnInputSnapshot,
    content_digest,
)
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.application.evidence_bundle import EvidenceBundle
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    AnswerRequest,
    Claim,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    ModelCallProvenance,
    QueryPlan,
    RetrievalProfileId,
    SearchResponse,
    SourceFamily,
)
from tap.modules.knowledge.ports.models import AnswerGeneration, GeneratedClaim


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
    ):
        self.prepare_calls += 1
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
    ):
        self.generate_calls += 1
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


async def _retrieve_route() -> tuple[TurnInput, object]:
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
async def test_output_redaction_hits_land_on_current_span(monkeypatch) -> None:
    """Only the answer-body hit counts land on `tap.guard.redaction.output_hits`
    -- a claim is always a verbatim paragraph of the answer, so counting its
    hits too (on top of the answer-level count that already covers the same
    match) would double-count."""
    import tap.interfaces.http.knowledge_service as knowledge_service_module
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
    from tap.platform.serialization.dataclass_json import to_json

    class _RecordingSpan:
        def __init__(self) -> None:
            self.attributes: dict[str, object] = {}

        def set_attribute(self, key: str, value: object) -> None:
            self.attributes[key] = value

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
async def test_final_route_redaction_result_is_validated_before_returning() -> None:
    """M5: `_redact_final_response` built the redacted response with
    `model_copy`, which skips validators. If per-claim redaction cannot
    locate its paragraph in the redacted answer (the `match_index is None`
    fallback keeps the claim's stale pre-redaction span), the response
    must fail validation here -- fail closed -- rather than reach the
    caller with a claim whose span no longer matches its text."""
    from pydantic import ValidationError

    from tap.modules.knowledge.api import answer_response_to_http

    class InconsistentRedactor:
        """Redacts the answer unchanged but always returns an unmatched
        value for a claim, forcing `_redact_final_response`'s defensive
        `match_index is None` fallback (a stale span) every time."""

        def __init__(self) -> None:
            self.calls = 0

        async def redact_with_report(self, text):
            self.calls += 1
            if self.calls == 1:
                return text, {}
            return "unmatched redacted text", {"phone": 1}

    response = answer_response_to_http(answer_response())
    service = _service(redactor=InconsistentRedactor())

    with pytest.raises(ValidationError):
        await service._redact_final_response(response)


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
