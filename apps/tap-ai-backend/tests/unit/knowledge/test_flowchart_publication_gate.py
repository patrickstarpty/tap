"""Direct chunk sources stay usable; flowchart images still need a published review."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tap.contracts.http import PublishedKnowledgeSource, PublishedKnowledgeSourcePage
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.application.answers import DocumentStateChanged
from tap.modules.knowledge.application.publication import (
    FlowchartPublicationGate,
    PublishedKnowledgeAuthority,
    is_flowchart_image,
)
from tap.modules.knowledge.domain.review import KnowledgePublication
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

HASH = "sha256:" + "a" * 64


def publication(*revisions: str, approved: tuple[str, ...] = ("pi_edge",)) -> KnowledgePublication:
    now = datetime.now(UTC)
    return KnowledgePublication(
        publication_id="kpb_flow",
        project_id=VALIDATION_SCOPE.project_id,
        review_id="krv_flow",
        review_version=3,
        approval_digest="sha256:" + "b" * 64,
        source_revision_ids=revisions,
        approved_item_ids=approved,
        generation="kb_doc_v2_tapper_demo",
        published_by="reviewer",
        published_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(days=1),
    )


class Publications:
    def __init__(self, *items: KnowledgePublication) -> None:
        self.items = items

    async def current_publications(self) -> tuple[KnowledgePublication, ...]:
        return self.items


def gate(*items: KnowledgePublication) -> FlowchartPublicationGate:
    return FlowchartPublicationGate(PublishedKnowledgeAuthority(Publications(*items)))


def ready(revision: str, filename: str) -> ReadyDocumentRevision:
    return ReadyDocumentRevision(
        document_id="doc_" + revision,
        revision_id=revision,
        source_content_hash=HASH,
        filename=filename,
    )


def test_flowchart_images_are_recognized_by_filename() -> None:
    assert is_flowchart_image("Approval Flow.PNG")
    assert is_flowchart_image("scan.jpeg")
    assert not is_flowchart_image("rates.xlsx")
    assert not is_flowchart_image(None)


@pytest.mark.asyncio
async def test_text_sources_need_no_publication_but_images_do() -> None:
    await gate().require_published(VALIDATION_SCOPE.project_id, (ready("rev_text", "guide.md"),))
    with pytest.raises(DocumentStateChanged):
        await gate().require_published(
            VALIDATION_SCOPE.project_id,
            (ready("rev_text", "guide.md"), ready("rev_flow", "flow.png")),
        )
    await gate(publication("rev_flow")).require_published(
        VALIDATION_SCOPE.project_id,
        (ready("rev_text", "guide.md"), ready("rev_flow", "flow.png")),
    )


@pytest.mark.asyncio
async def test_withdrawn_or_other_revision_publications_do_not_open_an_image() -> None:
    withdrawn = replace(
        publication("rev_flow"),
        status="withdrawn",
        withdrawn_by="reviewer",
        withdrawn_at=datetime.now(UTC),
    )
    with pytest.raises(DocumentStateChanged):
        await gate(withdrawn).require_published(
            VALIDATION_SCOPE.project_id, (ready("rev_flow", "flow.png"),)
        )
    with pytest.raises(DocumentStateChanged):
        await gate(publication("rev_older")).require_published(
            VALIDATION_SCOPE.project_id, (ready("rev_flow", "flow.png"),)
        )


@pytest.mark.asyncio
async def test_ready_source_list_hides_unpublished_images() -> None:
    def item(revision: str, filename: str) -> PublishedKnowledgeSource:
        return PublishedKnowledgeSource(
            source_id="src_" + revision[-1] * 32,
            document_id="doc_" + revision,
            revision_id=revision,
            source_name=filename,
            filename=filename,
            publication_id=None,
            expires_at=None,
            approved_item_count=0,
            inventory_item_count=1,
            partial=False,
        )

    page = PublishedKnowledgeSourcePage(
        items=[item("rev_a", "guide.md"), item("rev_b", "flow.png"), item("rev_c", "old.png")]
    )
    visible = await gate(publication("rev_b")).filter_sources(VALIDATION_SCOPE.project_id, page)

    assert [source.filename for source in visible.items] == ["guide.md", "flow.png"]
    assert visible.items[1].publication_id == "kpb_flow"
