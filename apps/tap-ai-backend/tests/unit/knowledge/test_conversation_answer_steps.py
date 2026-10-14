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

from tap.contracts.http import RetrievalAnswerRequest
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.chat.application.plan_answer import AnswerPlanner, PlanningInput, planning_input
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
    surface: records calls and lets a revocation between prepare and generate be
    simulated, the same shape the real `AnswerService`/`KnowledgeAPI` expose."""

    scope = VALIDATION_SCOPE

    def __init__(self, *, revoke_after_prepare: bool = False) -> None:
        self.revoke_after_prepare = revoke_after_prepare
        self.revoked = False
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
        if self.revoke_after_prepare:
            self.revoked = True
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
        if self.revoked:
            raise AuthorizationDenied("selected source authorization was revoked")
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

    async def answer_frozen(
        self,
        request,
        revisions,
        policy,
        *,
        governance,
        graph_context=(),
        model_alias=None,
        answer_execution=None,
        authorize=None,
    ):
        bundle = await self.prepare_frozen(
            request,
            revisions,
            policy,
            governance=governance,
            graph_context=graph_context,
            answer_execution=answer_execution,
            authorize=authorize,
        )
        generation = await self.generate_frozen(
            bundle,
            revisions,
            policy,
            governance=governance,
            model_alias=model_alias,
            answer_execution=answer_execution,
            authorize=authorize,
        )
        response = await self.verify_frozen(
            bundle,
            generation,
            revisions,
            policy,
            answer_execution=answer_execution,
            authorize=authorize,
        )
        await self.save_frozen(response, request.query, revisions)
        return response


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


def _service(answers: PhaseAnswers | None = None, *, models=None) -> KnowledgeHttpService:
    return KnowledgeHttpService(
        documents=Scoped(),
        answers=answers or PhaseAnswers(),
        citations=Scoped(),
        models=models,
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
    frozen, plan = await _retrieve_route()
    request = RetrievalAnswerRequest(query=frozen.message)

    revoking_answers = PhaseAnswers(revoke_after_prepare=True)
    service = _service(revoking_answers)
    step = await service.prepare_conversation_answer(request, frozen, answer_plan=plan)
    assert step["kind"] == "prepared"

    with pytest.raises(Exception) as stepped_error:
        await service.generate_conversation_answer(step, frozen, answer_plan=plan)
    assert revoking_answers.generate_calls == 0

    composed_answers = PhaseAnswers(revoke_after_prepare=True)
    composed_service = _service(composed_answers)
    with pytest.raises(Exception) as composed_error:
        await composed_service.answer_conversation(request, frozen, answer_plan=plan)

    assert type(stepped_error.value) is type(composed_error.value)


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
