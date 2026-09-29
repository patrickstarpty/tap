import asyncio
import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.application.review import (
    InMemoryKnowledgeReviewRepository,
    KnowledgeReviewApplication,
    ReviewComparisonTarget,
    ReviewStateConflict,
)
from tap.modules.knowledge.domain.documents import DocumentId, DocumentSource, MediaType, RevisionId
from tap.modules.knowledge.domain.flowcharts import normalize_flowchart
from tap.modules.knowledge.domain.review import (
    KnowledgeReviewRevision,
    ReviewStatus,
    canonical_digest,
)
from tap.modules.knowledge.ports.documents import ArtifactLocator

NOW = datetime(2026, 9, 27, tzinfo=UTC)
GRAPH = {
    "nodes": [
        {"id": "a", "label": "Start", "box": [0, 0, 30, 30], "lane": ""},
        {"id": "b", "label": "End", "box": [50, 50, 90, 90], "lane": ""},
    ],
    "edges": [{"source": "a", "target": "b", "condition": "", "certain": True}],
}


class Parser:
    async def parse(self, source):
        return ParserRegistry().parse(source)


class Artifacts:
    def __init__(self, source, normalized):
        self.source = source
        self.normalized = normalized
        self.written = []

    async def read_original(self, locator):
        return self.source.content

    async def read_normalized(self, locator):
        return self.normalized

    async def write_normalized(self, revision, artifact):
        self.written.append(artifact)
        return ArtifactLocator("corrected")


class Repository(InMemoryKnowledgeReviewRepository):
    async def comparison_target(self, review_id, item_id):
        return ReviewComparisonTarget(
            "image/png",
            "rev_1",
            self.source_hash,
            ArtifactLocator("original"),
            ArtifactLocator("normalized"),
            item_id,
            None,
            None,
        )

    async def save_flowchart_correction(
        self, revision, artifact, locator, *, expected_version, actor_id, occurred_at
    ):
        self.corrected = artifact
        return await self.save_review(
            revision,
            expected_version=expected_version,
            actor_id=actor_id,
            action="flowchart_corrected",
            occurred_at=occurred_at,
        )


def fixture(status=ReviewStatus.APPROVED):
    image = io.BytesIO()
    Image.new("RGB", (100, 100)).save(image, format="PNG")
    source = DocumentSource(
        "flow.png", MediaType.PNG, image.getvalue(), DocumentId("doc_1"), RevisionId("rev_1")
    )
    parsed = ParserRegistry().parse(source)
    normalized = normalize_flowchart(source, parsed, (100, 100), GRAPH)
    digest = canonical_digest([])
    review = KnowledgeReviewRevision(
        "review_1",
        "project_1",
        ("rev_1",),
        normalized.parse_inventory_digest,
        digest,
        digest,
        digest,
        ("author",),
        "reviewer",
        NOW + timedelta(days=30),
        status,
        3,
        (),
        tuple(i.item_id for i in normalized.parse_inventory),
    )
    repository = Repository()
    repository._reviews[review.review_id] = review
    repository.source_hash = normalized.source_hash
    artifacts = Artifacts(source, normalized)
    app = KnowledgeReviewApplication(repository, object(), artifacts, parser=Parser())
    return app, repository, artifacts, review


def test_correction_creates_new_graph_revision_and_invalidates_approval():
    async def scenario():
        app, repository, artifacts, review = fixture()
        changed = {
            "nodes": [
                dict(GRAPH["nodes"][0], label="Corrected", lane="Operations"),
                GRAPH["nodes"][1],
            ],
            "edges": [dict(GRAPH["edges"][0], source="b", target="a", condition="Retry")],
        }
        revision_id = await app.correct_flowchart(
            review.review_id, graph=changed, actor_id="editor", expected_version=3, now=NOW
        )
        assert revision_id != "rev_1"
        assert artifacts.normalized.flowchart_data != repository.corrected.flowchart_data
        assert "b → a" in repository.corrected.blocks[-1].text
        latest = await repository.get_review(review.review_id)
        assert latest.status is ReviewStatus.NEEDS_REVIEW
        assert latest.approved_item_ids == ()
        assert latest.reviewer_actor_id is None
        assert latest.version == 4
        assert "editor" in latest.editor_actor_ids

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [ReviewStatus.PUBLISHED, ReviewStatus.WITHDRAWN])
def test_published_or_withdrawn_review_cannot_be_corrected(status):
    async def scenario():
        app, _, artifacts, review = fixture(status)
        with pytest.raises(ReviewStateConflict):
            await app.correct_flowchart(
                review.review_id, graph=GRAPH, actor_id="editor", expected_version=3, now=NOW
            )
        assert artifacts.written == []

    asyncio.run(scenario())


@pytest.mark.parametrize("change", [{"box": [0, 0, 101, 30]}, {"box": [1, 2, 0, 4]}])
def test_out_of_bounds_correction_does_not_write(change):
    async def scenario():
        app, _, artifacts, review = fixture()
        graph = dict(GRAPH, nodes=[dict(GRAPH["nodes"][0], **change), GRAPH["nodes"][1]])
        with pytest.raises(ReviewStateConflict, match="invalid-flowchart"):
            await app.correct_flowchart(
                review.review_id, graph=graph, actor_id="editor", expected_version=3, now=NOW
            )
        assert artifacts.written == []

    asyncio.run(scenario())


def test_stale_correction_cannot_write_a_new_artifact():
    async def scenario():
        app, _, artifacts, review = fixture()
        with pytest.raises(ReviewStateConflict, match="revision-conflict"):
            await app.correct_flowchart(
                review.review_id, graph=GRAPH, actor_id="editor", expected_version=2, now=NOW
            )
        assert artifacts.written == []

    asyncio.run(scenario())


def test_dangling_edge_correction_does_not_write():
    async def scenario():
        app, _, artifacts, review = fixture()
        graph = dict(GRAPH, edges=[dict(GRAPH["edges"][0], target="missing")])
        with pytest.raises(ReviewStateConflict, match="invalid-flowchart"):
            await app.correct_flowchart(
                review.review_id, graph=graph, actor_id="editor", expected_version=3, now=NOW
            )
        assert artifacts.written == []

    asyncio.run(scenario())
