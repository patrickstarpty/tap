"""Task 12: the agent loop's tool-calling path (Task 9) through the real
worker/graph/runtime assembly, backed by MySQL: a real MySQL session and the
production `MysqlConversationRepository.graph_checkpointer` lease-tracked
checkpointer, a real `ChatGraph`/`GenerationWorker`, and a real
`KnowledgeHttpService`/`AnswerService`/`KnowledgeAPI` -- faking only the
retrieval port (`FakeSearchPort`), `RelationAnalysis`, `InsightsPort`, and the
model gateway via `DeterministicModelGateway` (Task 12's new tool-calling fake).

Requires `TAP_RUN_MYSQL_INTEGRATION=1`; uses the isolated `owned_project_mysql`
fixture (never the shared default MySQL service)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.domain.graph_runs import GraphCheckpointRetryable
from tap.modules.ai.ports.insights import (
    AuthorizedInsightsScope,
    FactWatermark,
    MetricFact,
    MetricQuery,
    MetricResult,
)
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import FrozenResource, TurnInput, content_digest
from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.application.answers import AnswerService
from tap.modules.knowledge.application.citations import CitationResolver
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.application.relation_analysis import (
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
    RelationSupport,
)
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.models import SearchHit
from tap.testing.deterministic_model_gateway import DeterministicModelGateway
from tests.contract.test_knowledge_api import CurrentPolicyVerifier, PassthroughRedactor
from tests.integration.test_knowledge_publication import (
    ITEM,
    NOW,
    ReadyProjection,
    approved_review,
    seed_authority,
)

pytestmark = pytest.mark.asyncio

SOURCE_ID = "src_" + "2" * 32
DOCUMENT_ID = "doc_mysql_publication"
REVISION_ID = "rev_mysql_001"
SOURCE_HASH = "sha256:" + "a" * 64
_FIRST_PASS_QUESTION = "第一遍通过率的证据来自哪里"
_SECOND_HIT_KEYWORD = "第二份证据"


_ANCHOR_JSON_BY_CHUNK_ID: dict[str, str] = {}


def _hit(seed: int, content: str, *, heading: str) -> SearchHit:
    """A `SearchHit` whose `chunk_id`/`logical_chunk_id` are the real
    deterministic projections `AnswerSnapshot.from_response`
    (`ports/answers.py`) recomputes and checks at `save_frozen` -- a
    hand-rolled id there raises "answer citation is outside the selected
    document revisions" once the turn's answer is finalized, even though
    every earlier retrieval-authorization check (`_hit_is_in_execution`)
    only requires a real `chunk_content_hash`."""
    import hashlib
    import json

    from tap.modules.knowledge.domain.documents import (
        DocumentId,
        RevisionId,
        chunk_id_for,
        logical_chunk_id_for,
    )

    chunk_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    anchor = DocumentAnchor(
        heading_path=(heading,),
        start_offset=0,
        end_offset=seed + 1,
        inventory_item_id=ITEM.item_id,
    )
    anchor_json = json.dumps(
        {
            "endOffset": anchor.end_offset,
            "headingPath": list(anchor.heading_path),
            "inventoryItemId": anchor.inventory_item_id,
            "startOffset": anchor.start_offset,
            "type": "document",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    chunk_id = str(chunk_id_for(RevisionId(REVISION_ID), anchor_json, chunk_hash))
    logical_chunk_id = "h_" + str(
        logical_chunk_id_for(DocumentId(DOCUMENT_ID), anchor_json)
    ).removeprefix("lc_")
    _ANCHOR_JSON_BY_CHUNK_ID[chunk_id] = anchor_json
    return SearchHit(
        family=SourceFamily.DOC,
        chunk_id=chunk_id,
        logical_chunk_id=logical_chunk_id,
        title="publication.md",
        content=content,
        source=SourceRevisionRef(
            source_id=SOURCE_ID,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=REVISION_ID,
            source_content_hash=SOURCE_HASH,
            anchor=anchor,
        ),
        chunk_content_hash=chunk_hash,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index="kb-doc-v1",
            schema_version="search-schema-v1",
            corpus_version="tapper-demo-v1",
        ),
        embedding_model_version="text-embedding-v4",
        score=0.9,
    )


_FIRST_HIT = _hit(1, "The first clause governs the first-pass review.", heading="Clause One")
_SECOND_HIT = _hit(2, f"{_SECOND_HIT_KEYWORD} covers the retry path.", heading="Clause Two")
_RELATION_QUESTION = "核保流程和健康告知是什么关系"
# `relation_support_mentions_both_endpoints` (`relation_claims.py`) checks the
# *actual chunk text* a relation's support points at, not just the label --
# so the seed hit for the relation question must itself name both endpoints.
_RELATION_HIT = _hit(3, "核保流程需要健康告知，方可完成核保审核。", heading="Clause Three")


class _RoutingSearchPort:
    """Routes the seed search by the exact query text: the relation
    question gets `_RELATION_HIT` (whose content names both relation
    endpoints), the `knowledge.search` tool call's `_SECOND_HIT_KEYWORD`
    argument gets `_SECOND_HIT` (growing the pool with a genuinely new
    S-labeled chunk), anything else gets `_FIRST_HIT`."""

    def __init__(self) -> None:
        self.executions: list[object] = []

    async def search(self, execution):
        self.executions.append(execution)
        query = execution.plan.sanitized_query
        if query == _SECOND_HIT_KEYWORD:
            return (_SECOND_HIT,)
        if query == _RELATION_QUESTION:
            return (_RELATION_HIT,)
        return (_FIRST_HIT,)


_RELATION = RelationContext(
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
                    chunk_id=_RELATION_HIT.chunk_id,
                    source_revision_id=REVISION_ID,
                    document_revision_id=REVISION_ID,
                    content_digest=_RELATION_HIT.chunk_content_hash,
                    anchor={},
                    evidence_label="S1",
                ),
            ),
            on_path=True,
        ),
    ),
)


class _FixedRelationAnalysis:
    """Task 12: the fake `RelationAnalysis` port, returning the same fixed
    `APPLIED` context every call -- `graph.relations` is now a tool call
    (`seed_search` never runs relation analysis inline, Task 9), so this is
    only ever reached from the `tools` node."""

    def __init__(self) -> None:
        self.calls = 0
        self.called_from_tools_node = False

    async def analyse(self, query, evidence, source_revision_ids, *, turn_id=None):
        self.calls += 1
        return _RELATION


class _FixedInsightsPort:
    """Task 12: the fake low-level `InsightsPort` -- `query_insights` returns
    one `complete` `first_pass_rate` fact bound to the Turn's frozen
    `insights_report_refs`."""

    def __init__(self, report_ref: str) -> None:
        self._report_ref = report_ref

    async def query_insights(
        self, scope: AuthorizedInsightsScope, query: MetricQuery
    ) -> MetricResult:
        return MetricResult(
            query_id="query-1",
            metric_version="metrics-v1",
            query=query,
            fact_watermark=FactWatermark("projection-v1", 1),
            metrics=tuple(
                MetricFact(
                    metric_id=metric_id,
                    numerator=90,
                    denominator=100,
                    value=0.9,
                    completeness="complete",
                    missing_reasons=(),
                    evidence_refs=(self._report_ref,),
                )
                for metric_id in query.metric_ids
            ),
            report_coverage=(),
        )

    async def get_insights(self, scope: AuthorizedInsightsScope, query_id: str) -> MetricResult:
        raise NotImplementedError


def _insights_explanation(report_ref: str):
    from tap.interfaces.http.insights_explanation_runtime import ConfiguredInsightsExplanation

    return ConfiguredInsightsExplanation(
        insights=_FixedInsightsPort(report_ref),
        gateway=None,
        model_alias="qwen-plus",
        project_id=VALIDATION_SCOPE.project_id,
        delegated_user_token="x" * 16,
        authorization_version="insights-v1",
        max_micros_per_token=1000,
    )


def _knowledge_model_gateway() -> KnowledgeModelGateway:
    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="x" * 20,
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )

    async def redact(text: str) -> str:
        return text

    gateway = DeterministicModelGateway(
        config, scope=VALIDATION_SCOPE, redact=redact, answer_delay_seconds=0
    )
    return KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=5,
    )


def _frozen(
    question: str,
    *,
    insights_report_refs: tuple[str, ...] = (),
) -> TurnInput:
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
    from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

    revision = ReadyDocumentRevision(DOCUMENT_ID, REVISION_ID, SOURCE_HASH, SOURCE_ID)
    policy = build_demo_policy_context((revision,))
    return TurnInput(
        question,
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        resolved_resources=(FrozenResource(SOURCE_ID, DOCUMENT_ID, REVISION_ID, SOURCE_HASH),),
        acl_digest=policy.acl_digest,
        retrieval_policy_digest=content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        ),
        insights_report_refs=insights_report_refs,
    )


class _Setup:
    def __init__(
        self,
        *,
        search,
        relation_analysis,
        insights_explanation,
        knowledge: KnowledgeHttpService,
        conversations: ConversationService,
        worker: GenerationWorker,
        sessions,
    ) -> None:
        self.search = search
        self.relation_analysis = relation_analysis
        self.insights_explanation = insights_explanation
        self.knowledge = knowledge
        self.conversations = conversations
        self.worker = worker
        self.sessions = sessions

    async def clear_retry_backoff(self, turn_id: str) -> None:
        """A transient failure defers the Turn to `next_attempt_at` (backoff);
        clear it so the test's very next `run_once` can reclaim it instead of
        waiting out real wall-clock time."""
        from sqlalchemy import update

        from tap.modules.chat.adapters.mysql_conversations import chat_turn

        async with self.sessions() as session, session.begin():
            await session.execute(
                update(chat_turn).where(chat_turn.c.turn_id == turn_id).values(next_attempt_at=None)
            )


async def _activate_revision(sessions) -> None:
    """`seed_authority` (`tests/integration/test_knowledge_publication.py`)
    leaves `knowledge_document.current_revision_id` unset and the
    revision's `chunks_blob_locator` null -- fine for publication-cutover
    tests, but `CitationResolver._validate_ledger_facts` requires both to
    treat a citation as current (`authorize_completed_answer` runs for
    every completed turn with citations, not only through a real ingestion
    pipeline)."""
    from sqlalchemy import text

    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE knowledge_document SET current_revision_id=:revision_id "
                "WHERE document_id=:document_id"
            ),
            {"revision_id": REVISION_ID, "document_id": DOCUMENT_ID},
        )
        await session.execute(
            text(
                "UPDATE knowledge_document_revision SET chunks_blob_locator=:locator "
                "WHERE revision_id=:revision_id"
            ),
            {
                "locator": f"tapper-artifacts/revisions/{REVISION_ID}/chunks-v1.jsonl.gz",
                "revision_id": REVISION_ID,
            },
        )


async def _seed_chunk_manifest(sessions, hit: SearchHit, *, ordinal: int) -> None:
    """`AnswerSnapshot.from_response`/`_save_answer_snapshot`
    (`ports/answers.py`, `adapters/mysql_documents.py`) both recheck a cited
    chunk against a durable `knowledge_chunk_manifest` row -- real ingestion
    writes one per chunk, which this fake-search-backed turn never runs, so
    the fake hits above need their own manifest row seeded directly."""
    from sqlalchemy import text

    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO knowledge_chunk_manifest (enterprise_id,project_id,actor_id,"
                "identity_mode,identity_origin,chunk_id,logical_chunk_id,revision_id,"
                "ordinal,root_id,parent_id,anchor_json,chunk_content_hash,"
                "embedding_model_version,index_version,created_at) VALUES "
                "('local','tapper-demo','tapper-local-user','validation','VALIDATION',"
                ":chunk_id,:logical_chunk_id,:revision_id,:ordinal,:document_id,NULL,"
                ":anchor_json,:chunk_hash,:embedding_model_version,'tapper-index-v1',"
                "UTC_TIMESTAMP(6))"
            ),
            {
                "chunk_id": hit.chunk_id,
                "logical_chunk_id": hit.logical_chunk_id,
                "revision_id": hit.source.revision,
                "ordinal": ordinal,
                "document_id": DOCUMENT_ID,
                "anchor_json": _ANCHOR_JSON_BY_CHUNK_ID[hit.chunk_id],
                "chunk_hash": hit.chunk_content_hash,
                "embedding_model_version": hit.embedding_model_version,
            },
        )


async def _setup(
    owned_project_mysql,
    *,
    search=None,
    relation_analysis=None,
    insights_report_ref: str | None = None,
) -> tuple[_Setup, object]:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await seed_authority(sessions)
    await _activate_revision(sessions)
    review = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
    await review.create_review(approved_review())
    await KnowledgeReviewApplication(review, ReadyProjection()).publish_review(
        "krv_mysql_001",
        generation="generation-001",
        idempotency_key="publish-agent-loop",
        actor_id="synthetic-reviewer-02",
        expected_version=4,
        now=NOW,
    )
    documents = MysqlDocumentRepository(
        sessions, scope=VALIDATION_SCOPE, audit_factory=lambda _: None
    )
    authority = PublishedKnowledgeAuthority(review)
    await _seed_chunk_manifest(sessions, _FIRST_HIT, ordinal=0)
    await _seed_chunk_manifest(sessions, _SECOND_HIT, ordinal=1)
    await _seed_chunk_manifest(sessions, _RELATION_HIT, ordinal=2)

    search_port = search if search is not None else _RoutingSearchPort()
    relation_port = relation_analysis if relation_analysis is not None else _FixedRelationAnalysis()
    models = _knowledge_model_gateway()

    knowledge_api = KnowledgeAPI(
        search=search_port,
        embeddings=models,
        answers=models,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        relation_analysis=relation_port,
    )
    answer_service = AnswerService(
        repository=documents, knowledge=knowledge_api, publication_authority=authority
    )
    citations = CitationResolver(
        repository=documents, artifacts=object(), publication_authority=authority
    )
    knowledge_http = KnowledgeHttpService(
        documents=documents,
        answers=answer_service,
        citations=citations,
        models=models,
        relations_available=True,
    )
    conversations = ConversationService(
        MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
    )
    insights_explanation = (
        _insights_explanation(insights_report_ref) if insights_report_ref is not None else None
    )
    # No explicit `checkpointer=` here: letting `GenerationWorker` fall
    # through to `conversations.repository.graph_checkpointer` (the real
    # production wiring) is what actually populates `ai_graph_run`'s own
    # `lease_token`/`lease_until` bookkeeping -- a bare `MysqlGraphCheckpointer`
    # passed in directly (as several *other* MySQL integration tests do,
    # where no transient-failure resume is exercised) never does, so
    # `claim_queued`'s reclaim-after-defer branch can never match and a
    # deferred Turn is stuck "running" forever (caught by Task 12's own
    # crash/resume test).
    worker = GenerationWorker(
        conversations,
        knowledge_http,
        insights_explanation=insights_explanation,
    )
    setup = _Setup(
        search=search_port,
        relation_analysis=relation_port,
        insights_explanation=insights_explanation,
        knowledge=knowledge_http,
        conversations=conversations,
        worker=worker,
        sessions=sessions,
    )
    return setup, engine


async def _run_until_completed(setup: _Setup, chat_id: str) -> object:
    for _ in range(6):
        if await setup.worker.run_once(limit=1) == 0:
            break
        loaded = await setup.conversations.load(chat_id)
        if loaded.turns[0].state in {"completed", "failed"}:
            return loaded
    return await setup.conversations.load(chat_id)


@pytest.mark.asyncio
async def test_multi_step_search_persists_steps_and_cites_tool_evidence(
    owned_project_mysql,
) -> None:
    setup, engine = await _setup(owned_project_mysql)
    try:
        question = f"{_FIRST_PASS_QUESTION} [e2e-tool:knowledge.search:{_SECOND_HIT_KEYWORD}]"
        await setup.conversations.create(
            "chat-multi-step", "turn-multi-step", "request-multi-step", _frozen(question)
        )
        loaded = await _run_until_completed(setup, "chat-multi-step")
        turn = loaded.turns[0]
        assert turn.state == "completed"

        event_types = [event.event_type for event in loaded.events]
        step_events = [event for event in loaded.events if event.event_type == "task.step"]
        statuses = [event.payload["status"] for event in step_events]
        assert "running" in statuses
        assert "completed" in statuses
        assert statuses.index("running") < statuses.index("completed")
        assert any(
            event.event_type == "retrieval.hits_ready" and event.payload.get("stepId") is not None
            for event in loaded.events
        ), "the tool call's retrieval.hits_ready must carry a stepId"
        assert "turn.completed" in event_types

        completed = next(event for event in loaded.events if event.event_type == "turn.completed")
        citations = completed.payload["answer"]["citations"]
        assert any(citation["evidenceLabel"] not in {"S1"} for citation in citations), (
            "the second search's new S label was never cited"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_relation_analysis_runs_only_as_a_tool(owned_project_mysql) -> None:
    relation = _FixedRelationAnalysis()
    setup, engine = await _setup(owned_project_mysql, relation_analysis=relation)
    try:
        await setup.conversations.create(
            "chat-relation", "turn-relation", "request-relation", _frozen(_RELATION_QUESTION)
        )
        loaded = await _run_until_completed(setup, "chat-relation")
        turn = loaded.turns[0]
        assert turn.state == "completed"
        assert relation.calls == 1

        tool_steps = [
            event
            for event in loaded.events
            if event.event_type == "task.step" and event.payload.get("tool") == "graph.relations"
        ]
        assert tool_steps, "graph.relations never ran as a tool call"

        completed = next(event for event in loaded.events if event.event_type == "turn.completed")
        citations = completed.payload["answer"]["citations"]
        assert any(citation["kind"] == "edge" for citation in citations)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_insights_joint_analysis_with_fake_port(owned_project_mysql) -> None:
    setup, engine = await _setup(owned_project_mysql, insights_report_ref="report-1")
    try:
        question = "本周的首次通过率如何 [e2e-tool:insights.query:ignored]"
        frozen = _frozen(question, insights_report_refs=("report-1",))
        await setup.conversations.create(
            "chat-insights", "turn-insights", "request-insights", frozen
        )
        loaded = await _run_until_completed(setup, "chat-insights")
        turn = loaded.turns[0]
        assert turn.state == "completed"

        tool_steps = [
            event
            for event in loaded.events
            if event.event_type == "task.step" and event.payload.get("tool") == "insights.query"
        ]
        assert tool_steps and tool_steps[-1].payload["status"] == "completed"

        completed = next(event for event in loaded.events if event.event_type == "turn.completed")
        facts = completed.payload["answer"].get("insightsFacts") or []
        assert facts[0]["metricId"] == "first_pass_rate"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_clarification_reply_completes_without_citations(owned_project_mysql) -> None:
    setup, engine = await _setup(owned_project_mysql)
    try:
        question = "这个产品怎么样 [e2e-clarify]"
        await setup.conversations.create(
            "chat-clarify", "turn-clarify", "request-clarify", _frozen(question)
        )
        loaded = await _run_until_completed(setup, "chat-clarify")
        turn = loaded.turns[0]
        assert turn.state == "completed"

        completed = next(event for event in loaded.events if event.event_type == "turn.completed")
        answer = completed.payload["answer"]
        assert answer["answerKind"] == "clarification"
        assert answer["citations"] == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_crash_after_tools_resumes_without_repeating_tool(
    owned_project_mysql,
) -> None:
    """Injects one retryable failure into `generate_conversation_answer`'s
    *second* call for this turn -- the `model` node's re-entry immediately
    after the `tools` node completes round 1 -- mirroring Task 9's own
    `test_resume_after_tools_node_does_not_repeat_tool_calls_or_steps`
    (`tests/unit/entrypoints/test_chat_turn_graph.py`), which monkeypatches
    the same bound method rather than a stage-based failure-injection
    module (that module only covers document ingestion stages, not the
    chat graph)."""

    setup, engine = await _setup(owned_project_mysql)
    try:
        question = f"{_FIRST_PASS_QUESTION} [e2e-tool:knowledge.search:{_SECOND_HIT_KEYWORD}]"
        await setup.conversations.create(
            "chat-crash", "turn-crash", "request-crash", _frozen(question)
        )

        calls_seen = {"n": 0}
        real_generate = setup.knowledge.generate_conversation_answer

        async def _flaky_generate(step, frozen_input, **kwargs):
            calls_seen["n"] += 1
            if calls_seen["n"] == 2:
                raise GraphCheckpointRetryable("connection lost after durable task write")
            return await real_generate(step, frozen_input, **kwargs)

        setup.knowledge.generate_conversation_answer = _flaky_generate  # type: ignore[method-assign]

        assert await setup.worker.run_once(limit=1) == 1

        setup.knowledge.generate_conversation_answer = real_generate  # type: ignore[method-assign]
        await setup.clear_retry_backoff("turn-crash")
        loaded = await _run_until_completed(setup, "chat-crash")
        turn = loaded.turns[0]
        assert turn.state == "completed"

        step_events = [
            event
            for event in loaded.events
            if event.event_type == "task.step" and event.payload["status"] == "completed"
        ]
        assert len(step_events) == 1
        assert len(setup.search.executions) == 2  # seed_search + the one tool call, never twice
    finally:
        await engine.dispose()
