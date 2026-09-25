"""Isolated execution of admitted plans through the existing Knowledge and graph seams."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import Classification, ProjectPolicy, VerifiedSubjectFacts
from tap.modules.ai.application.interaction_graph import InteractionGraph
from tap.modules.chat.application.plan_answer import AnswerPlanner, PlanningInput
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    ResourceMode,
    ResourceRef,
    SourceFamily,
)
from tap.modules.knowledge.ports.errors import SearchUnavailable
from tests.contract.test_knowledge_api import (
    CurrentPolicyVerifier,
    FakeModelPort,
    FakeSearchPort,
    PassthroughRedactor,
    ResourceGrant,
    search_hit,
)


def value(question="比较章节 A 和 B"):
    return PlanningInput(
        "project-a",
        "turn-a",
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        question,
        "tapper-chat",
        ("repo:checkout:payment.py",),
    )


async def planned():
    async def model(_input, _timeout):
        return {
            "intent": "comparison",
            "route": "retrieve",
            "confidence": 0.9,
            "standalone_query": "比较章节 A 和 B",
            "missing": [],
            "queries": [
                {
                    "id": "q1",
                    "text": "章节 A",
                    "depends_on": [],
                    "evidence_goal": "A",
                    "source_ids": list(value().source_ids),
                },
                {
                    "id": "q2",
                    "text": "章节 B",
                    "depends_on": ["q1"],
                    "evidence_goal": "B",
                    "source_ids": list(value().source_ids),
                },
            ],
        }

    return await AnswerPlanner(model).plan(value())


def execution(plan):
    from tap.modules.chat.application import plan_answer

    assert hasattr(plan_answer, "authorized_execution"), (
        "public plan execution projection is missing"
    )
    return plan_answer.authorized_execution(plan)


class PlannedModels(FakeModelPort):
    async def answer(self, query, evidence, profile_id, **kwargs):
        self.query = query
        self.generation_input = kwargs.get("answer_input")
        return await super().answer(query, evidence, profile_id)


def knowledge(search, models):
    from uuid import uuid4

    return KnowledgeAPI(
        search=search,
        embeddings=models,
        answers=models,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=lambda: uuid4().hex,
    )


def policy(project_id="project-a"):
    hit = search_hit()
    grants = (
        ResourceGrant(
            family="code",
            source_id=hit.source.source_id,
            revision_kind="git_commit",
            revision=hit.source.revision,
            source_content_hash=hit.source.source_content_hash,
        ),
    )
    return build_retrieval_policy_context(
        VerifiedSubjectFacts(
            "tenant-a", "user-1", frozenset({"group-one"}), frozenset({"reader"}), True
        ),
        ProjectPolicy(
            tenant_id="tenant-a",
            project_id=project_id,
            permission_granted=True,
            allowed_group_ids=frozenset({"group-one"}),
            classification_ceiling=Classification.CONFIDENTIAL,
            allowed_environments=frozenset({"production"}),
            allowed_source_families=frozenset({"code"}),
            active_corpus_version="corpus-17",
            acl_digest=value().acl_digest,
            policy_version="policy-17",
            decision_id="decision-17",
            resource_grants=grants,
        ),
        requested_tenant_id="tenant-a",
        requested_project_id=project_id,
    )


def request():
    return AnswerRequest(
        query=value().original_question,
        resource_refs=(
            ResourceRef(
                family=SourceFamily.CODE,
                source_id=value().source_ids[0],
                mode=ResourceMode.SCOPE,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_subqueries_share_budget_keep_original_generation_question_and_plan_identity():
    search, models = FakeSearchPort((search_hit(),)), PlannedModels()
    plan = await planned()
    response = await knowledge(search, models).answer_frozen(
        request(),
        policy(),
        governance=None,
        answer_execution=execution(plan),
    )
    assert [item.plan.sanitized_query for item in search.executions] == ["章节 A", "章节 B"]
    assert sum(item.plan.candidate_limit for item in search.executions) <= 20
    assert len(models.answer_evidence[0]) <= 10
    assert len(models.answer_evidence) == 1
    assert models.query == "比较章节 A 和 B"
    assert models.generation_input.context["planId"] == plan.plan_id
    assert all(item.plan.answer_plan_id == plan.plan_id for item in search.executions)
    assert not response.abstained


@pytest.mark.asyncio
async def test_saturated_subqueries_share_evidence_capacity_without_starving_coverage():
    class Search(FakeSearchPort):
        async def search(self, item):
            self.executions.append(item)
            prefix = "a" if item.plan.sanitized_query == "章节 A" else "b"
            return tuple(
                replace(
                    search_hit(),
                    chunk_id="h_" + prefix + f"{index:063x}",
                    logical_chunk_id="h_" + prefix + f"{index:063x}",
                )
                for index in range(10)
            )

    models = PlannedModels()
    result = await knowledge(Search(), models).answer_frozen(
        request(), policy(), governance=None, answer_execution=execution(await planned())
    )
    evidence = models.answer_evidence[0]
    assert len(evidence) == 10
    assert {item.chunk_id[2] for item in evidence} == {"a", "b"}
    assert models.generation_input.context["missingEvidence"] == []
    assert not result.degraded_mode


@pytest.mark.asyncio
async def test_reordered_failed_subquestion_keeps_text_and_evidence_goal_in_generation():
    from tap.modules.chat.domain.answer_plan import PlannedQuery

    plan = await planned()
    plan = replace(
        plan,
        queries=(
            PlannedQuery("q2", "章节 B", (), "B 的限制条件", plan.source_ids),
            PlannedQuery("q1", "章节 A", (), "A 的许可条件", plan.source_ids),
        ),
    )

    class Search(FakeSearchPort):
        async def search(self, item):
            if item.plan.sanitized_query == "章节 B":
                raise SearchUnavailable("unavailable")
            return await super().search(item)

    models = PlannedModels()
    await knowledge(Search((search_hit(),)), models).answer_frozen(
        request(), policy(), governance=None, answer_execution=execution(plan)
    )
    assert models.generation_input.context.get("subquestions") == [
        {
            "id": "q2",
            "text": "章节 B",
            "evidenceGoal": "B 的限制条件",
            "evidenceLabels": [],
            "missingEvidence": True,
        },
        {
            "id": "q1",
            "text": "章节 A",
            "evidenceGoal": "A 的许可条件",
            "evidenceLabels": ["S1"],
            "missingEvidence": False,
        },
    ]


@pytest.mark.asyncio
async def test_partial_query_failure_marks_missing_evidence_without_inventing_it():
    class Search(FakeSearchPort):
        async def search(self, item):
            if item.plan.sanitized_query == "章节 B":
                raise SearchUnavailable("unavailable")
            return await super().search(item)

    models = PlannedModels()
    response = await knowledge(Search((search_hit(),)), models).answer_frozen(
        request(),
        policy(),
        governance=None,
        answer_execution=execution(await planned()),
    )
    assert response.degraded_mode
    assert "partial-evidence" in response.degradation_reasons
    assert models.generation_input.context["missingEvidence"] == ["q2"]


@pytest.mark.asyncio
async def test_changed_authority_blocks_search_before_model_call():
    search, models = FakeSearchPort((search_hit(),)), PlannedModels()
    from tap.modules.access.domain.policy import AuthorizationDenied

    with pytest.raises(AuthorizationDenied):
        await knowledge(search, models).answer_frozen(
            request(),
            policy("other"),
            governance=None,
            answer_execution=execution(await planned()),
        )
    assert search.executions == []
    assert models.embedding_queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("persistence", ["memory", "mysql"])
async def test_checkpoint_survives_restart_without_replanning_or_template_latest(
    persistence, request
):
    saver = InMemorySaver()
    engine = None
    if persistence == "mysql":
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from tap.modules.access.adapters.validation import VALIDATION_SCOPE
        from tap.modules.ai.adapters.mysql_checkpointer import MysqlGraphCheckpointer

        database = request.getfixturevalue("owned_project_mysql")
        engine = create_async_engine(database.url.replace("mysql+pymysql", "mysql+asyncmy"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        saver = MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE)
    plans = []
    attempts = []

    async def classify(_state):
        plan = await planned()
        plans.append(plan)
        return {"answer_plan": plan.to_dict()}

    async def admit(_state):
        return {"admitted": True}

    async def execute(state):
        assert "answer_plan" in state, "interaction graph discarded the durable answer plan"
        restored = AnswerPlan.from_dict(state["answer_plan"])
        restored.validate_binding(value())
        attempts.append(restored.plan_id)
        if len(attempts) == 1:
            raise RuntimeError("restart after planning")
        return {"result": {"planId": restored.plan_id}}

    async def authorize():
        return True

    def graph():
        return InteractionGraph(
            graph_version="fast-chat-v1",
            state_schema_version=1,
            checkpointer=saver,
            classify=classify,
            admit=admit,
            execute=execute,
            authorize=authorize,
        )

    with pytest.raises(RuntimeError, match="restart after planning"):
        await graph().start(run_id="turn-a", payload={}, execution_mode="inline")
    result = await graph().resume(run_id="turn-a")
    assert len(plans) == 1
    assert attempts == [plans[0].plan_id, plans[0].plan_id]
    assert result["result"] == {"planId": plans[0].plan_id}
    if engine is not None:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_execution_stops_before_search():
    async def canceled():
        raise asyncio.CancelledError

    search, models = FakeSearchPort((search_hit(),)), PlannedModels()
    with pytest.raises(asyncio.CancelledError):
        await knowledge(search, models).answer_frozen(
            request(),
            policy(),
            governance=None,
            answer_execution=execution(await planned()),
            authorize=canceled,
        )
    assert search.executions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,expected,calls",
    [
        ("你好", "Hello", 1),
        ("我们公司的报销政策是什么？", "请选择", 0),
        ("它和上一版有什么不同？", "请明确", 0),
        ("最近 30 天失败率", "尚不可用", 0),
        ("完整表格汇总", "尚不可用", 0),
    ],
)
async def test_worker_routes_in_graph_persists_plan_and_binds_completion(question, expected, calls):
    from tap.entrypoints.tapper_generation_worker import GenerationWorker
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.domain.conversations import TurnInput, TurnInputSnapshot, content_digest
    from tap.modules.knowledge.ports.models import AnswerGeneration

    frozen = TurnInput(
        question,
        VALIDATION_SCOPE.actor_id,
        "validation",
        "tapper-chat",
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
    )
    snapshot = TurnInputSnapshot.create(
        snapshot_id="snapshot-a",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn-a",
        value=frozen,
        now=datetime.now(timezone.utc),
    )

    class Models:
        count = 0

        async def chat(self, *args, **kwargs):
            self.count += 1
            return AnswerGeneration("Hello", (), "tapper-chat", "direct-chat-v1", None)

    class Repository:
        async def claim_queued(self, **kwargs):
            return (
                (
                    "chat-a",
                    SimpleNamespace(
                        turn_id="turn-a", lease_token="lease-a", input_snapshot=snapshot
                    ),
                ),
            )

    class Conversations:
        repository = Repository()
        evidence = []

        async def emit(self, *args, **kwargs):
            pass

        async def complete_evidence(self, chat, turn, evidence, **kwargs):
            self.evidence.append(evidence)

    class Scoped:
        scope = VALIDATION_SCOPE

    models = Models()
    knowledge_service = KnowledgeHttpService(
        documents=Scoped(), answers=Scoped(), citations=Scoped(), models=models
    )
    knowledge_service.answer_planner = AnswerPlanner()
    saver = InMemorySaver()
    conversations = Conversations()
    worker = GenerationWorker(conversations, knowledge_service, checkpointer=saver)
    await worker.run_once(limit=1)
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": "turn-a"}})
    values = checkpoint.checkpoint["channel_values"]
    assert "answer_plan" in values, "worker did not persist the graph-local answer plan"
    saved_plan = AnswerPlan.from_dict(values["answer_plan"])
    assert saved_plan.input_digest == snapshot.digest
    assert expected in conversations.evidence[0].answer
    assert models.count == calls
    assert f"answer-plan:{saved_plan.plan_id}" in conversations.evidence[0].diagnostics
    await worker.run_once(limit=1)
    assert models.count == calls


@pytest.mark.asyncio
async def test_worker_records_preplanning_publication_denial_as_failed_turn():
    from tap.entrypoints.tapper_generation_worker import GenerationWorker
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.access.domain.policy import AuthorizationDenied
    from tap.modules.chat.domain.conversations import (
        FrozenResource,
        TurnInput,
        TurnInputSnapshot,
    )

    frozen = TurnInput(
        "What does the selected document say?",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "tapper-chat",
        resolved_resources=(
            FrozenResource("source-a", "document-a", "revision-a", "sha256:" + "a" * 64),
        ),
    )
    snapshot = TurnInputSnapshot.create(
        snapshot_id="snapshot-denied",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn-denied",
        value=frozen,
        now=datetime.now(timezone.utc),
    )

    class Repository:
        async def claim_queued(self, **kwargs):
            return (
                (
                    "chat-denied",
                    SimpleNamespace(
                        turn_id="turn-denied", lease_token="lease-denied", input_snapshot=snapshot
                    ),
                ),
            )

    class Conversations:
        repository = Repository()
        completed = []

        async def complete_evidence(self, chat, turn, evidence, **kwargs):
            self.completed.append((chat, turn, evidence, kwargs["terminal_event"]))

    class Knowledge:
        answer_planner = object()

        async def authorize_planning(self, _snapshot):
            raise AuthorizationDenied("current knowledge publication is unavailable")

    conversations = Conversations()
    worker = GenerationWorker(conversations, Knowledge(), checkpointer=InMemorySaver())

    assert await worker.run_once(limit=1) == 1
    assert len(conversations.completed) == 1
    chat, turn, evidence, terminal = conversations.completed[0]
    assert (chat, turn, evidence.outcome, evidence.retrieval_summary.status) == (
        "chat-denied",
        "turn-denied",
        "failed",
        "failed",
    )
    assert terminal[0] == "turn.failed"


@pytest.mark.asyncio
async def test_http_selected_plan_returns_citations_from_existing_answer_boundary():
    from tap.contracts.http import RetrievalAnswerRequest
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.domain.conversations import FrozenResource, TurnInput, content_digest
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
    from tests.unit.knowledge.test_answer_service import answer_response, ready

    revision = ready()
    authority = build_demo_policy_context((revision,))
    frozen = TurnInput(
        "错误码 E104 如何处理？",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "tapper-chat",
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

    class Scoped:
        scope = VALIDATION_SCOPE

        async def answer_frozen(self, *args, **kwargs):
            assert kwargs["answer_execution"].plan_id == plan.plan_id
            return answer_response()

    service = KnowledgeHttpService(documents=Scoped(), answers=Scoped(), citations=Scoped())
    result = await service.answer_conversation(
        RetrievalAnswerRequest(query=frozen.message), frozen, answer_plan=plan
    )
    assert result is not None, "selected planned answer was not mapped back to HTTP"
    assert result.citations[0].citation_id == "citation-a"


@pytest.mark.asyncio
async def test_revocation_after_embedding_prevents_search():
    revoked = False

    class Models(PlannedModels):
        async def embed(self, query):
            nonlocal revoked
            result = await super().embed(query)
            revoked = True
            return result

    async def authorize():
        if revoked:
            raise PermissionError("revoked")

    search, models = FakeSearchPort((search_hit(),)), Models()
    with pytest.raises(PermissionError):
        await knowledge(search, models).answer_frozen(
            request(),
            policy(),
            governance=None,
            answer_execution=execution(await planned()),
            authorize=authorize,
        )
    assert search.executions == []


@pytest.mark.asyncio
async def test_persisted_plan_deadline_is_not_reset_on_resume():
    import time

    plan = await planned()
    assert hasattr(plan, "deadline_at"), "plan does not persist the shared deadline"
    expired = replace(plan, deadline_at=time.time() - 1)
    with pytest.raises(TimeoutError):
        execution(expired)


@pytest.mark.asyncio
async def test_independent_queries_run_together_and_dependents_wait():
    from tap.modules.chat.domain.answer_plan import PlannedQuery

    plan = await planned()
    plan = replace(
        plan,
        queries=(
            PlannedQuery("q1", "章节 A", (), "A", plan.source_ids),
            PlannedQuery("q2", "章节 B", (), "B", plan.source_ids),
            PlannedQuery("q3", "综合比较", ("q1", "q2"), "comparison", plan.source_ids),
        ),
    )
    started = set()
    both = asyncio.Event()

    class Search(FakeSearchPort):
        async def search(self, item):
            query = item.plan.sanitized_query
            if query == "综合比较":
                assert started == {"章节 A", "章节 B"}
            else:
                started.add(query)
                if len(started) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), timeout=1)
            return await super().search(item)

    search = Search((search_hit(),))
    result = await knowledge(search, PlannedModels()).answer_frozen(
        request(), policy(), governance=None, answer_execution=execution(plan)
    )
    assert not result.abstained
    assert [item.plan.sanitized_query for item in search.executions][-1] == "综合比较"
    assert sum(item.plan.candidate_limit for item in search.executions) == 20


@pytest.mark.asyncio
async def test_authorization_failure_cancels_parallel_sibling_queries():
    from tap.modules.chat.domain.answer_plan import PlannedQuery

    plan = await planned()
    plan = replace(
        plan,
        queries=(
            PlannedQuery("q1", "章节 A", (), "A", plan.source_ids),
            PlannedQuery("q2", "章节 B", (), "B", plan.source_ids),
        ),
    )
    started, release = asyncio.Event(), asyncio.Event()
    late_completions = []

    class Search(FakeSearchPort):
        async def search(self, item):
            if item.plan.sanitized_query == "章节 A":
                await started.wait()
                raise PermissionError("revoked")
            started.set()
            await release.wait()
            late_completions.append(item)
            return await super().search(item)

    with pytest.raises(PermissionError):
        await knowledge(Search((search_hit(),)), PlannedModels()).answer_frozen(
            request(), policy(), governance=None, answer_execution=execution(plan)
        )
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert late_completions == []


@pytest.mark.asyncio
async def test_direct_answer_respects_persisted_deadline_before_any_model_call():
    import time

    from tap.contracts.http import RetrievalAnswerRequest
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.domain.conversations import TurnInput, content_digest
    from tap.modules.knowledge.ports.models import AnswerGeneration

    frozen = TurnInput(
        "你好",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "tapper-chat",
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
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
            (),
        )
    )
    plan = replace(plan, deadline_at=time.time() - 1)

    class Scoped:
        scope = VALIDATION_SCOPE

    class Models:
        async def chat(self, *args, **kwargs):
            return AnswerGeneration("Hello", (), "tapper-chat", "direct-chat-v1", None)

    service = KnowledgeHttpService(
        documents=Scoped(), answers=Scoped(), citations=Scoped(), models=Models()
    )
    with pytest.raises(TimeoutError, match="budget"):
        await service.answer_conversation(
            RetrievalAnswerRequest(query="你好"), frozen, answer_plan=plan
        )


async def model_only_plan(question):
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.application.plan_answer import planning_input
    from tap.modules.chat.domain.conversations import TurnInput, TurnInputSnapshot, content_digest

    frozen = TurnInput(
        question,
        VALIDATION_SCOPE.actor_id,
        "validation",
        "tapper-chat",
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
    )
    snapshot = TurnInputSnapshot.create(
        snapshot_id="snapshot-a",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn-a",
        value=frozen,
        now=datetime.now(timezone.utc),
    )
    return frozen, await AnswerPlanner().plan(planning_input(snapshot))


def nonretrieval_service(models):
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE

    class Scoped:
        scope = VALIDATION_SCOPE

    return KnowledgeHttpService(
        documents=Scoped(), answers=Scoped(), citations=Scoped(), models=models
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", [{"template_digest": "sha256:" + "a" * 64}, {"template_version": "latest"}]
)
async def test_direct_plan_rejects_template_drift_before_generation(drift):
    from tap.contracts.http import RetrievalAnswerRequest
    from tap.modules.knowledge.ports.models import AnswerGeneration

    class Models:
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            return AnswerGeneration("Hello", (), "tapper-chat", "direct-chat-v1", None)

    frozen, plan = await model_only_plan("你好")
    models = Models()
    with pytest.raises(ValueError, match="template"):
        await nonretrieval_service(models).answer_conversation(
            RetrievalAnswerRequest(query=frozen.message), frozen, answer_plan=replace(plan, **drift)
        )
    assert models.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing,required", [("time-range", "时间范围"), ("comparison-conditions", "比较条件")]
)
async def test_clarification_asks_for_the_validated_missing_field(missing, required):
    from tap.contracts.http import RetrievalAnswerRequest

    frozen, plan = await model_only_plan("比较本次与目标结果")
    plan = replace(plan, missing=(missing,))
    result = await nonretrieval_service(object()).answer_conversation(
        RetrievalAnswerRequest(query=frozen.message), frozen, answer_plan=plan
    )
    assert required in result.answer
    assert "对象和版本" not in result.answer


@pytest.mark.asyncio
async def test_direct_plan_passes_pinned_assembly_to_generation():
    from tap.contracts.http import RetrievalAnswerRequest
    from tap.modules.knowledge.application.answer_templates import get_template
    from tap.modules.knowledge.ports.models import AnswerGeneration

    inputs = []

    class Models:
        async def chat(self, *args, **kwargs):
            inputs.append(kwargs.get("answer_input"))
            return AnswerGeneration("Hello", (), "tapper-chat", "direct-chat-v1", None)

    frozen, plan = await model_only_plan("你好")
    await nonretrieval_service(Models()).answer_conversation(
        RetrievalAnswerRequest(query=frozen.message), frozen, answer_plan=plan
    )
    assert inputs[0] is not None
    assert get_template("general", "1").instruction in inputs[0].platform_instruction
    assert inputs[0].context["originalQuestion"] == "你好"
