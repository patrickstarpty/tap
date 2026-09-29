"""Direct chunk retrieval keeps flowchart evidence behind its published review."""

from dataclasses import replace

import pytest
from test_published_retrieval import (
    DIGEST,
    NOW,
    CurrentPolicy,
    Models,
    PublicationRepository,
    Redactor,
    Search,
    _hit,
    _publication,
)

from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.application.publication import (
    FlowchartPublicationGate,
    PublishedKnowledgeAuthority,
)
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    DocumentAnchor,
    IndexRevision,
    ResourceMode,
    ResourceRef,
    SearchRequest,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

ROWS = (
    ReadyDocumentRevision("document-1", "revision-text", DIGEST, "src_" + "1" * 32, None, "a.md"),
    ReadyDocumentRevision("document-2", "revision-flow", DIGEST, "src_" + "2" * 32, None, "b.png"),
    ReadyDocumentRevision("document-3", "revision-other", DIGEST, "src_" + "3" * 32, None, "c.png"),
)


def _located(row: ReadyDocumentRevision, item: str, *, image: bool):
    hit = _hit(row.revision_id, item)
    return replace(
        hit,
        index_revision=IndexRevision("knowledge-project-1-v7", "v1", "tapper-demo-v1"),
        source=SourceRevisionRef(
            source_id=row.source_id or "",
            source_type="doc",
            revision_kind=hit.source.revision_kind,
            revision=row.revision_id,
            source_content_hash=DIGEST,
            anchor=DocumentAnchor(
                inventory_item_id=item,
                start_offset=0,
                end_offset=len(hit.content),
                bbox=(10, 10, 40, 40) if image else (),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_only_approved_items_of_a_published_flowchart_become_evidence() -> None:
    repository = PublicationRepository(
        _publication(
            project_id="tapper-demo",
            source_revision_ids=("revision-flow",),
            approved_item_ids=("edge-approved",),
        )
    )
    search = Search(
        (
            _located(ROWS[0], "text-item", image=False),
            _located(ROWS[1], "edge-approved", image=True),
            _located(ROWS[1], "edge-excluded", image=True),
            _located(ROWS[2], "edge-unpublished", image=True),
        )
    )
    ids = iter(f"id-{index}" for index in range(40))
    knowledge = KnowledgeAPI(
        search=search,
        embeddings=Models(repository),
        answers=Models(repository),
        policy_verifier=CurrentPolicy(),
        redactor=Redactor(),
        id_factory=lambda: next(ids),
        flowchart_gate=FlowchartPublicationGate(
            PublishedKnowledgeAuthority(repository, now=lambda: NOW)
        ),
    )
    request = SearchRequest(
        query="What happens next?",
        answer_mode=AnswerMode.QUICK,
        source_families=(SourceFamily.DOC,),
        resource_refs=tuple(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=row.source_id or "",
                mode=ResourceMode.SCOPE,
                requested_revision=row.revision_id,
            )
            for row in ROWS
        ),
    )

    response = await knowledge.search(request, build_demo_policy_context(ROWS))

    by_item = {item.source.anchor.inventory_item_id: item for item in response.evidence}
    assert set(by_item) == {"text-item", "edge-approved"}
    assert by_item["text-item"].publication_id is None
    assert by_item["edge-approved"].publication_id == "publication-1"
    assert by_item["edge-approved"].approved_item_id == "edge-approved"
    assert search.executions[0].approved_item_scope is None
