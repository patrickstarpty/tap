"""`KnowledgeHttpService.answer_conversation` split into resumable, JSON-serializable
prepare / generate / verify / finalize steps so a later LangGraph node can call each
phase on its own, checkpointing `ConversationAnswerStep` between them."""

from __future__ import annotations

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
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


def direct_answer(**values):
    """Build a complete application answer from the test's semantic fields."""
    from dataclasses import fields

    from tap.modules.knowledge.api import DirectAnswer

    payload = {
        "trace_id": "trace-1",
        "query_plan_id": "plan-1",
        "context_snapshot_id": "context-1",
        "corpus_version": "tapper-demo-v1",
        "retrieval_profile_id": "direct-chat-v1",
        "answer": "grounded",
        "abstained": False,
    }
    payload.update(
        {
            key: value
            for key, value in values.items()
            if key in {f.name for f in fields(DirectAnswer)}
        }
    )
    payload["citations"] = tuple(
        c.model_dump(mode="json", by_alias=True) if hasattr(c, "model_dump") else c
        for c in payload.get("citations", ())
    )
    graph = payload.get("graph_context")
    if graph is not None and hasattr(graph, "model_dump"):
        payload["graph_context"] = graph.model_dump(mode="json", by_alias=True)
    return DirectAnswer(**payload)
