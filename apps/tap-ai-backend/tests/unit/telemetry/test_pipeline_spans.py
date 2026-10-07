"""Pipeline stage spans and background job root spans (observability Task 7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.insights_explanation import (
    ExplanationBudget,
    ExplanationRequest,
    InsightsExplanationService,
    ProposedExplanation,
)
from tap.modules.ai.ports.insights import FactWatermark
from tap.modules.chat.application.plan_answer import AnswerPlanner, PlanningInput
from tap.modules.graph.application.jobs import InMemoryGraphJobStore
from tap.modules.graph.application.worker import GraphWorker
from tap.modules.graph.domain.jobs import GraphJobRequest
from tap.modules.knowledge.application.graph_enrichment import GraphAnswerEnricher
from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    ResourceMode,
    ResourceRef,
    SourceFamily,
)
from tap.modules.knowledge.ports.documents import JobKind
from tap.modules.test_management.application.generation import (
    TestDesignWorker as DesignWorker,
)
from tests.contract.test_insights_tool import NOW, StubInsights, StubKnowledge, result, scope
from tests.contract.test_knowledge_api import (
    FakeModelPort,
    FakeSearchPort,
    search_hit,
)
from tests.integration.test_answer_plan_execution import PlannedModels
from tests.integration.test_answer_plan_execution import (
    execution as authorized_execution_of,
)
from tests.integration.test_answer_plan_execution import (
    knowledge as knowledge_api,
)
from tests.integration.test_answer_plan_execution import (
    policy as answer_plan_policy,
)
from tests.integration.test_answer_plan_execution import value as planning_value
from tests.unit.graph.test_graph_worker import Artifacts as GraphArtifacts
from tests.unit.graph.test_graph_worker import Extractor as GraphExtractor
from tests.unit.graph.test_graph_worker import _request_chunks as graph_request_chunks
from tests.unit.knowledge.test_ingestion_worker import (
    Chunker,
    FakeClock,
    Parser,
    StatefulArtifacts,
    StatefulRepository,
)
from tests.unit.knowledge.test_ingestion_worker import Embeddings as IngestionEmbeddings
from tests.unit.knowledge.test_ingestion_worker import Index as IngestionIndex
from tests.unit.test_design_graph_worker import ImmediateGenerator, RestartableJobs


def _planning_input(message: str) -> PlanningInput:
    return PlanningInput(
        project_id="project-a",
        turn_id="turn-a",
        input_digest="sha256:" + "a" * 64,
        acl_digest="sha256:" + "b" * 64,
        policy_digest="sha256:" + "c" * 64,
        original_question=message,
        model_alias="qwen-plus",
        source_ids=("source-a",),
    )


@pytest.mark.asyncio
async def test_planner_emits_chat_plan_span(span_recorder) -> None:
    plan = await AnswerPlanner().plan(_planning_input("错误码 E104 如何处理？"))

    spans = [item for item in span_recorder.get_finished_spans() if item.name == "chat.plan"]
    assert len(spans) == 1
    assert spans[0].attributes["tap.plan.kind"] == plan.route


@pytest.mark.asyncio
async def test_retrieve_emits_search_span_with_hits(span_recorder) -> None:
    hit_one = search_hit()
    hit_two = replace(
        hit_one,
        chunk_id="h_" + "3" * 64,
        logical_chunk_id="h_" + "4" * 64,
        score=0.5,
    )
    knowledge = knowledge_api(FakeSearchPort((hit_one, hit_two)), FakeModelPort())
    request = AnswerRequest(
        query="authorization",
        resource_refs=(
            ResourceRef(
                family=SourceFamily.CODE,
                source_id=hit_one.source.source_id,
                mode=ResourceMode.SCOPE,
            ),
        ),
    )

    await knowledge.answer(request, answer_plan_policy())

    spans = [item for item in span_recorder.get_finished_spans() if item.name == "retrieval.search"]
    assert len(spans) == 1
    attributes = spans[0].attributes
    assert attributes["tap.retrieval.hit_count"] == 2
    assert len(attributes["tap.retrieval.chunk_ids"]) == 2
    assert len(attributes["tap.retrieval.document_ids"]) == 2
    assert len(attributes["tap.retrieval.scores"]) == 2
    assert attributes["tap.retrieval.exact_flowchart"] is False


async def _planned_three_queries():
    async def model(_input, _timeout):
        source_ids = list(planning_value().source_ids)
        return {
            "intent": "comparison",
            "route": "retrieve",
            "confidence": 0.9,
            "standalone_query": "比较章节 A、B 和 C",
            "missing": [],
            "queries": [
                {
                    "id": "q1",
                    "text": "章节 A",
                    "depends_on": [],
                    "evidence_goal": "A",
                    "source_ids": source_ids,
                },
                {
                    "id": "q2",
                    "text": "章节 B",
                    "depends_on": [],
                    "evidence_goal": "B",
                    "source_ids": source_ids,
                },
                {
                    "id": "q3",
                    "text": "章节 C",
                    "depends_on": [],
                    "evidence_goal": "C",
                    "source_ids": source_ids,
                },
            ],
        }

    return await AnswerPlanner(model).plan(planning_value("比较章节 A、B 和 C"))


@pytest.mark.asyncio
async def test_planned_retrieval_emits_one_span_per_query(span_recorder) -> None:
    plan = await _planned_three_queries()
    knowledge = knowledge_api(FakeSearchPort((search_hit(),)), PlannedModels())
    request = AnswerRequest(
        query=plan.original_question,
        resource_refs=(
            ResourceRef(
                family=SourceFamily.CODE,
                source_id=planning_value().source_ids[0],
                mode=ResourceMode.SCOPE,
            ),
        ),
    )

    await knowledge.answer_frozen(
        request,
        answer_plan_policy(),
        governance=None,
        answer_execution=authorized_execution_of(plan),
    )

    spans = [item for item in span_recorder.get_finished_spans() if item.name == "retrieval.search"]
    assert len(spans) == 3


@pytest.mark.asyncio
async def test_graph_enricher_emits_counts(span_recorder) -> None:
    from tap.modules.graph.application.queries import InMemoryGraphStore
    from tap.modules.graph.domain.models import (
        Evidence,
        GraphEdge,
        GraphNode,
        GraphSnapshot,
        GraphSnapshotDraft,
        RelationOrigin,
    )

    evidence = Evidence(
        "evidence-1",
        "snapshot-1",
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 6},
        "sha256:" + "a" * 64,
    )
    draft = GraphSnapshotDraft(
        GraphSnapshot.create(
            snapshot_id="snapshot-1",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("source-revision-1",),
            document_revision_ids=("document-revision-1",),
        ),
        (
            GraphNode("node-1", "snapshot-1", "Policy", "ENTITY", "policy"),
            GraphNode("node-2", "snapshot-1", "Claim", "ENTITY", "claim"),
        ),
        (
            GraphEdge(
                "edge-1",
                "snapshot-1",
                "node-1",
                "node-2",
                "GOVERNS",
                RelationOrigin.EXTRACTED,
                1.0,
                ("evidence-1",),
            ),
        ),
        (evidence,),
        (),
    )
    store = InMemoryGraphStore()
    await store.publish(VALIDATION_SCOPE, draft)

    result_context = await GraphAnswerEnricher(store).enrich(
        VALIDATION_SCOPE, ("source-revision-1",), "*"
    )

    spans = [item for item in span_recorder.get_finished_spans() if item.name == "graph.enrich"]
    assert len(spans) == 1
    attributes = spans[0].attributes
    assert attributes["tap.graph.snapshot_id"] == result_context.snapshot_id
    assert attributes["tap.graph.node_count"] == 2
    assert attributes["tap.graph.edge_count"] == 1


@pytest.mark.asyncio
async def test_insights_query_span_has_row_count(span_recorder) -> None:
    insights = StubInsights(result())

    async def generate(_request, _metrics, _evidence, max_cost_micros):
        return ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 7),
            NOW,
            result().metrics,
            (),
            (),
            (),
            0,
        )

    await InsightsExplanationService(
        insights=insights,
        knowledge=StubKnowledge(()),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    ).explain(
        scope(),
        ExplanationRequest("conversation-a", "turn-a", "graph-a", "Explain", None, "query-a", ()),
        ExplanationBudget(1, 1, 30, 500),
    )

    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "tool.insights.query"
    ]
    assert len(spans) == 1
    attributes = spans[0].attributes
    assert attributes["tap.insights.query_id"] == "query-a"
    assert attributes["tap.insights.metric_version"] == "insights-metrics-v1"
    assert attributes["tap.insights.row_count"] == 2


@pytest.mark.asyncio
async def test_ingestion_job_root_span_binds_job(span_recorder) -> None:
    repository = StatefulRepository(kind=JobKind.INGESTION)
    artifacts = StatefulArtifacts()
    embeddings = IngestionEmbeddings(dimension=3)
    index = IngestionIndex()
    clock = FakeClock()

    class SpanEmittingStageHook:
        async def before_stage(self, stage):
            from tap.platform.telemetry import span as _span

            with _span("test.model_call"):
                return None

    worker = IngestionWorker(
        repository=repository,
        artifacts=artifacts,
        parser=Parser(),
        chunker=Chunker(),
        embeddings=embeddings,
        index=index,
        worker_id="worker-a",
        embedding_model_alias="text-embedding-v4",
        embedding_dimension=3,
        index_version="tapper-index-v1",
        clock=clock,
        stage_hook=SpanEmittingStageHook(),
        scope=VALIDATION_SCOPE,
    )

    result_run = await worker.run_once(limit=1)
    assert result_run.ready == 1

    spans = span_recorder.get_finished_spans()
    root_spans = [item for item in spans if item.name == "job.ingestion"]
    assert len(root_spans) == 1
    root = root_spans[0]
    assert root.parent is None
    assert root.attributes["tap.job_id"] == "job-1"
    assert root.attributes["tap.scope.project_id"] == VALIDATION_SCOPE.project_id

    child_spans = [item for item in spans if item.name == "test.model_call"]
    assert len(child_spans) >= 1
    for child in child_spans:
        assert child.attributes["tap.job_id"] == "job-1"
        assert child.attributes["tap.scope.project_id"] == VALIDATION_SCOPE.project_id


@pytest.mark.asyncio
async def test_graph_and_test_design_jobs_emit_root_spans(span_recorder) -> None:
    jobs = InMemoryGraphJobStore()
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="qwen-plus",
    )
    job = await jobs.request(VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 9, 0, 0))
    chunks = await GraphArtifacts().read_chunks("art1.chunks")
    from tap.modules.graph.adapters.fake_extraction import rule_based_draft

    request_chunks = graph_request_chunks(chunks, revision_id="revision-1")
    draft = rule_based_draft(job.snapshot, request_chunks, filename="revision-1")
    graph_worker = GraphWorker(
        jobs=jobs,
        artifacts=GraphArtifacts(),
        extractor=GraphExtractor(draft),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
    )

    graph_result = await graph_worker.run_once(limit=1)
    assert graph_result.ready == 1

    graph_spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "job.graph_extraction"
    ]
    assert len(graph_spans) == 1
    assert graph_spans[0].parent is None
    assert graph_spans[0].attributes["tap.job_id"] == job.job_id

    span_recorder.clear()

    design_jobs = RestartableJobs()
    generator = ImmediateGenerator()
    design_worker = DesignWorker(
        jobs=design_jobs,
        generator=generator,
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    design_result = await design_worker.run_once(limit=1)
    assert design_result.ready == 1

    design_spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "job.test_design"
    ]
    assert len(design_spans) == 1
    assert design_spans[0].parent is None
    assert design_spans[0].attributes["tap.job_id"] == design_jobs.claim.job.request.job_id


@pytest.mark.asyncio
async def test_ingestion_idle_run_once_does_not_flush_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty batch must skip the synchronous MySQL flush on the hot loop."""
    import tap.modules.knowledge.application.ingestion as ingestion_module

    calls = 0

    async def fake_flush_traces() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(ingestion_module, "flush_traces", fake_flush_traces)

    repository = StatefulRepository(kind=JobKind.INGESTION)
    repository.pending = False
    worker = IngestionWorker(
        repository=repository,
        artifacts=StatefulArtifacts(),
        parser=Parser(),
        chunker=Chunker(),
        embeddings=IngestionEmbeddings(dimension=3),
        index=IngestionIndex(),
        worker_id="worker-a",
        embedding_model_alias="text-embedding-v4",
        embedding_dimension=3,
        index_version="tapper-index-v1",
        clock=FakeClock(),
        scope=VALIDATION_SCOPE,
    )

    idle_run = await worker.run_once(limit=1)
    assert idle_run.claimed == 0
    assert calls == 0

    repository.pending = True
    processed_run = await worker.run_once(limit=1)
    assert processed_run.ready == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_graph_idle_run_once_does_not_flush_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty batch must skip the synchronous MySQL flush on the hot loop."""
    import tap.modules.graph.application.worker as graph_worker_module
    from tap.modules.graph.adapters.fake_extraction import rule_based_draft

    calls = 0

    async def fake_flush_traces() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(graph_worker_module, "flush_traces", fake_flush_traces)

    jobs = InMemoryGraphJobStore()
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="qwen-plus",
    )
    chunks = await GraphArtifacts().read_chunks("art1.chunks")
    request_chunks = graph_request_chunks(chunks, revision_id="revision-1")
    draft = rule_based_draft(request.snapshot, request_chunks, filename="revision-1")
    graph_worker = GraphWorker(
        jobs=jobs,
        artifacts=GraphArtifacts(),
        extractor=GraphExtractor(draft),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
    )

    idle_run = await graph_worker.run_once(limit=1)
    assert idle_run.claimed == 0
    assert calls == 0

    await jobs.request(VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 9, 0, 0))
    processed_run = await graph_worker.run_once(limit=1)
    assert processed_run.ready == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_test_design_idle_run_once_does_not_flush_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty batch must skip the synchronous MySQL flush on the hot loop."""
    import tap.modules.test_management.application.generation as generation_module

    calls = 0

    async def fake_flush_traces() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(generation_module, "flush_traces", fake_flush_traces)

    design_jobs = RestartableJobs()
    design_jobs.completed = ["placeholder"]
    generator = ImmediateGenerator()
    design_worker = DesignWorker(
        jobs=design_jobs,
        generator=generator,
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    idle_run = await design_worker.run_once(limit=1)
    assert idle_run.claimed == 0
    assert calls == 0

    design_jobs.completed = []
    processed_run = await design_worker.run_once(limit=1)
    assert processed_run.ready == 1
    assert calls == 1
