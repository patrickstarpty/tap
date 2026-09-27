"""A durable Insights explanation can cite only current frozen Knowledge evidence."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tap.interfaces.http.insights_explanation_runtime import ConfiguredInsightsExplanation
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.published_knowledge import PublishedKnowledgeEvidence
from tap.modules.ai.ports.insights import (
    FactWatermark,
    InsightsAuthorizationChanged,
    MetricFact,
    MetricQuery,
    MetricResult,
)
from tap.modules.chat.domain.conversations import FrozenResource
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.domain.review import KnowledgePublication
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

NOW = datetime(2026, 9, 27, tzinfo=UTC)
SOURCE = "src_" + "1" * 32
HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64
QUOTE = "Credential rotation invalidated the login token."
FROZEN = FrozenResource(SOURCE, "doc-1", "rev-1", HASH)


class Insights:
    async def get_insights(self, scope, query_id):
        assert scope.context == VALIDATION_SCOPE
        assert query_id == "query-a"
        return MetricResult(
            query_id="query-a",
            metric_version="metrics-v1",
            query=MetricQuery(
                metric_ids=("first_pass_rate",),
                source_ids=("ci-a",),
                run_ids=(),
                build_ids=(),
                branches=(),
                environments=(),
                configurations=(),
                from_date="2026-09-26",
                to_date="2026-09-27",
                timezone="UTC",
                as_of=NOW,
            ),
            fact_watermark=FactWatermark("insights-v1", 7),
            metrics=(MetricFact("first_pass_rate", 1, 2, 0.5, "complete", (), ("receipt-a",)),),
            report_coverage=(),
        )

    async def get_evidence(self, scope, receipt_id, *, max_bytes):
        assert receipt_id == "receipt-a" and max_bytes <= 20_000
        return b"<testsuite><failure>Login failed</failure></testsuite>"


class Searches:
    scope = VALIDATION_SCOPE

    async def resolve_conversation_selection(self, revisions):
        assert revisions == ("rev-1",)
        return (ReadyDocumentRevision("doc-1", "rev-1", HASH, SOURCE),), SimpleNamespace()

    async def search(self, request):
        assert request.query == "Why did login fail?"
        assert request.resource_refs[0].source_id == SOURCE
        return SimpleNamespace(
            evidence=(
                Evidence(
                    family=SourceFamily.DOC,
                    chunk_id="chunk-1",
                    logical_chunk_id="logical-1",
                    title="Runbook",
                    content=QUOTE,
                    source=SourceRevisionRef(
                        source_id=SOURCE,
                        source_type="doc",
                        revision_kind=RevisionKind.BLOB_VERSION,
                        revision="rev-1",
                        source_content_hash=HASH,
                        anchor=DocumentAnchor(page=2, inventory_item_id="item-1"),
                    ),
                    chunk_content_hash=CHUNK_HASH,
                    content_role=ContentRole.SOURCE,
                    citation_id="knowledge-citation-1",
                    evidence_label="S1",
                    index_revision=IndexRevision("generation-1", "v1", "tapper-demo-v2"),
                    embedding_model_version="embed-v1",
                    acl_decision_id="decision-1",
                    score=0.9,
                    publication_id="publication-1",
                    approval_digest=HASH,
                    approved_item_id="item-1",
                ),
            )
        )


class Publications:
    def __init__(self):
        self.current = KnowledgePublication(
            publication_id="publication-1",
            project_id="tapper-demo",
            review_id="review-1",
            review_version=1,
            approval_digest=HASH,
            source_revision_ids=("rev-1",),
            approved_item_ids=("item-1",),
            generation="generation-1",
            published_by="reviewer-1",
            published_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
        )

    async def current_publication(self):
        return self.current


@pytest.mark.asyncio
async def test_runtime_cites_frozen_published_knowledge_and_rejects_withdrawal():
    publications = Publications()
    authority = PublishedKnowledgeAuthority(publications, now=lambda: NOW)

    class Gateway:
        async def generate_structured(self, request):
            supplied = json.loads(request.context)
            assert {item["citationId"] for item in supplied["evidence"]} == {
                "receipt-a",
                "knowledge-citation-1",
            }
            assert (
                next(
                    item
                    for item in supplied["evidence"]
                    if item["citationId"] == "knowledge-citation-1"
                )["excerpt"]
                == QUOTE
            )
            return SimpleNamespace(
                output={
                    "hypotheses": [
                        {
                            "text": "A credential rotation may explain the login failure.",
                            "citationId": "knowledge-citation-1",
                            "evidenceQuote": QUOTE,
                        }
                    ],
                    "missingInformation": ["The token issue time is unknown."],
                }
            )

    runtime = ConfiguredInsightsExplanation(
        insights=Insights(),
        gateway=Gateway(),
        model_alias="tapper-chat",
        project_id="tapper-demo",
        delegated_user_token="delegated-user-token-0001",
        authorization_version="authz-1",
        max_micros_per_token=1,
        knowledge_evidence_factory=lambda selection: PublishedKnowledgeEvidence(
            searches=Searches(), publication_authority=authority, selection=selection
        ),
    )
    result = await runtime.explain(
        VALIDATION_SCOPE,
        "query-a",
        ("receipt-a",),
        "Why did login fail?",
        conversation_id="conversation-a",
        turn_id="turn-a",
        selected_knowledge=(FROZEN,),
    )

    assert len(result["hypotheses"]) == 1
    assert "A credential rotation may explain the login failure." in result["hypotheses"][0]
    assert result["hypotheses"][0].endswith("[knowledge-citation-1]")
    assert result["evidenceExcerpts"][0]["sourceId"] == SOURCE
    assert result["evidenceExcerpts"][0]["approvedItemId"] == "item-1"
    assert result["evidenceExcerpts"][0]["text"] == QUOTE
    assert result["missingInformation"] == ["The token issue time is unknown."]
    json.dumps(result)
    await runtime.reauthorize_result(VALIDATION_SCOPE, "query-a", ("receipt-a",), (FROZEN,), result)
    publications.current = replace(
        publications.current, status="withdrawn", withdrawn_by="reviewer-1", withdrawn_at=NOW
    )
    with pytest.raises(InsightsAuthorizationChanged):
        await runtime.reauthorize_result(
            VALIDATION_SCOPE, "query-a", ("receipt-a",), (FROZEN,), result
        )
