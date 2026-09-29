"""Service journey with real artifact bytes; external storage/model/index use memory."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.knowledge.adapters import artifact_codecs as codecs
from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.application.ingestion import NEXT_STAGE, IngestionWorker
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.application.review import (
    InMemoryKnowledgeReviewRepository,
    KnowledgeReviewApplication,
    ReviewComparisonTarget,
    ReviewInventoryRecord,
)
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    canonical_sha256,
    revision_id_for,
)
from tap.modules.knowledge.domain.flowcharts import normalize_flowchart
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    AnswerRequest,
    ContentRole,
    DocumentAnchor,
    IndexRevision,
    ResourceMode,
    ResourceRef,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.domain.review import (
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
    canonical_digest,
)
from tap.modules.knowledge.domain.sources import projection_digest
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    ClaimedIngestionJob,
    EmbeddingArtifact,
    IndexReceipt,
    IngestionWork,
    JobKind,
    JobStage,
    JobState,
    initial_stage_results,
)
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    RedactionResult,
    SearchHit,
)

NOW = datetime(2026, 9, 27, tzinfo=UTC)
DOC = DocumentId("doc_" + "1" * 32)
SOURCE = "src_" + "2" * 32
GENERATION = "knowledge-flow-lifecycle-v1"
GRAPH = {
    "nodes": [
        {"id": name, "label": name, "box": [i * 20, 0, i * 20 + 15, 15], "lane": ""}
        for i, name in enumerate(("risk", "pay", "retry", "done"))
    ],
    "edges": [
        {"source": "risk", "target": "pay", "condition": "高风险", "certain": True},
        {"source": "pay", "target": "retry", "condition": "失败", "certain": True},
        {"source": "retry", "target": "pay", "condition": "允许重试", "certain": True},
        {"source": "pay", "target": "done", "condition": "成功", "certain": False},
    ],
}


class ByteArtifacts:
    """Only the byte transport is fake: every durable artifact uses production codecs."""

    def __init__(self, original):
        self.original = original
        self.values = {}
        self.read_kinds = []

    async def read_original(self, locator):
        return self.original

    async def write_normalized(self, revision, artifact):
        return self._write(
            revision, "normalized", codecs.encode_normalized_artifact(revision, artifact)
        )

    async def read_normalized(self, locator):
        revision, data = self._read(locator, "normalized")
        return codecs.decode_normalized_artifact(data, expected_revision=revision)

    async def write_chunks(self, revision, chunks):
        return self._write(revision, "chunks", codecs.encode_chunks_artifact(revision, chunks))

    async def read_chunks(self, locator):
        revision, data = self._read(locator, "chunks")
        return codecs.decode_chunks_artifact(data, expected_revision=revision)

    async def write_embeddings(self, revision, artifact, *, source_content_hash):
        return self._write(
            revision,
            "embeddings",
            codecs.encode_embeddings_artifact(revision, source_content_hash, artifact),
        )

    async def read_embeddings(self, locator):
        revision, data = self._read(locator, "embeddings")
        return codecs.decode_embeddings_artifact(data, expected_revision=revision)

    def _write(self, revision, kind, data):
        assert isinstance(data, bytes)
        locator = ArtifactLocator(f"{revision}/{kind}")
        self.values[locator] = (revision, kind, data)
        return locator

    def _read(self, locator, kind):
        revision, stored_kind, data = self.values[locator]
        assert stored_kind == kind
        self.read_kinds.append(kind)
        return revision, data


class Parser:
    async def parse(self, source):
        return ParserRegistry().parse(source)


class ReviewLedger(InMemoryKnowledgeReviewRepository):
    def __init__(self, normalized, locator):
        super().__init__()
        self.normalized = normalized
        self.locator = locator

    async def comparison_target(self, review_id, item_id):
        return ReviewComparisonTarget(
            "image/png",
            str(self.normalized.revision_id),
            self.normalized.source_hash,
            ArtifactLocator("original"),
            self.locator,
            item_id,
            None,
            None,
        )

    async def save_flowchart_correction(self, revision, artifact, locator, **kwargs):
        self.corrected = artifact
        self.corrected_locator = locator
        return await self.save_review(revision, action="flowchart_corrected", **kwargs)

    async def list_inventory(self, review_id):
        artifact = self.corrected if review_id == "corrected-review" else self.normalized
        return tuple(
            ReviewInventoryRecord(
                str(artifact.revision_id),
                item.item_id,
                1,
                item.kind.value,
                item.locator,
                item.status.value,
                item.artifact_digest,
                item.reason,
                None,
            )
            for item in artifact.parse_inventory
        )


class WorkerLedger:
    def __init__(self, artifact, locator):
        now = NOW.replace(tzinfo=None)
        self.job = ClaimedIngestionJob(
            "job-corrected",
            str(artifact.revision_id),
            JobKind.INGESTION,
            1,
            JobState.PROCESSING,
            JobStage.CHUNKING,
            initial_stage_results(now),
            "worker",
            "lease",
            now + timedelta(minutes=1),
        )
        self.work = IngestionWork(
            self.job.job_id,
            "lease",
            JobKind.INGESTION,
            JobStage.CHUNKING,
            str(DOC),
            str(artifact.revision_id),
            artifact.filename,
            artifact.media_type.value,
            artifact.source_hash,
            ArtifactLocator("original"),
            locator,
            None,
            None,
            PARSER_VERSION,
            "chunker-v1",
            "pipeline-v1",
            (),
        )
        self.commits = []

    async def claim_jobs(self, **kwargs):
        return (self.job,)

    async def load_ingestion_work(self, *args):
        return self.work

    async def commit_stage(self, commit):
        self.commits.append(commit.expected_stage)
        changes = {"stage": NEXT_STAGE.get(commit.expected_stage, JobStage.READY)}
        for name in (
            "chunks_locator",
            "embeddings_locator",
            "chunk_manifest_digest",
            "projection_digest",
        ):
            if getattr(commit, name) is not None:
                changes[name] = getattr(commit, name)
        if commit.manifest:
            changes["manifest"] = commit.manifest
        self.work = replace(self.work, **changes)

    async def renew_lease(self, *args, **kwargs):
        pass

    async def fail_job(self, failure):
        pytest.fail(f"worker failed: {failure}")


class Model:
    embedding_model_id = "embedding-v1"
    embedding_dimension = 2

    async def embed_documents(self, texts, *, model_alias, chunk_ids):
        return EmbeddingArtifact(model_alias, 2, tuple((0.1, 0.2) for _ in texts), chunk_ids)

    async def embed(self, query):
        return Embedding((0.1, 0.2), "embedding-v1", "embedding-request")

    async def answer(self, query, evidence, profile_id, **kwargs):
        self.context = kwargs["graph_context"][-1]
        path = next(
            p
            for p in self.context["paths"]
            if p["nodeIds"] == ["risk", "pay", "retry", "pay", "done"]
        )
        return AnswerGeneration(
            "高风险支付失败后，经允许重试回到支付，成功后完成。",
            (
                GeneratedClaim(
                    "高风险支付失败后，经允许重试回到支付，成功后完成。",
                    tuple(path["evidenceLabels"]),
                ),
            ),
            "answer-v1",
            profile_id,
            "request",
        )


class Index:
    async def upsert_revision(self, work, chunks, embeddings, *, index_version):
        self.chunks = chunks
        return IndexReceipt(
            work.revision_id,
            index_version,
            len(chunks),
            projection_digest=projection_digest(
                work.revision_id, "schema-v1", index_version, work.manifest
            ),
            schema_version="schema-v1",
        )

    async def verify(self, review, generation):
        return generation == GENERATION and bool(self.chunks)


class Policy:
    async def verify_current(self, expected):
        return expected


class Redactor:
    async def redact(self, text):
        return RedactionResult(text, "redaction-v1")


def review_for(artifact, identity):
    digest = canonical_digest([])
    return KnowledgeReviewRevision(
        identity,
        "tapper-demo",
        (str(artifact.revision_id),),
        artifact.parse_inventory_digest,
        digest,
        digest,
        digest,
        ("editor",),
        None,
        NOW + timedelta(days=30),
        ReviewStatus.CHECKING,
        1,
        tuple(item.item_id for item in artifact.parse_inventory),
        (),
    )


@pytest.mark.asyncio
async def test_corrected_image_roundtrips_resumes_publishes_answers_and_revokes():
    image = io.BytesIO()
    Image.new("RGB", (100, 100)).save(image, format="PNG")
    content = image.getvalue()
    revision = revision_id_for(DOC, canonical_sha256(content), PARSER_VERSION)
    source = DocumentSource("flow.png", MediaType.PNG, content, DOC, revision)
    original = normalize_flowchart(source, ParserRegistry().parse(source), (100, 100), GRAPH)
    artifacts = ByteArtifacts(content)
    locator = await artifacts.write_normalized(str(revision), original)
    ledger = ReviewLedger(await artifacts.read_normalized(locator), locator)
    initial_review = review_for(original, "original-review")
    await ledger.add(initial_review)
    model, index = Model(), Index()
    app = KnowledgeReviewApplication(ledger, index, artifacts, parser=Parser())
    corrected_graph = {
        **GRAPH,
        "edges": [*GRAPH["edges"][:-1], {**GRAPH["edges"][-1], "certain": True}],
    }
    corrected_id = await app.correct_flowchart(
        "original-review", graph=corrected_graph, actor_id="editor", expected_version=1, now=NOW
    )
    assert corrected_id != revision
    assert (await ledger.get_review("original-review")).status is ReviewStatus.NEEDS_REVIEW
    corrected = await artifacts.read_normalized(ledger.corrected_locator)
    assert corrected.revision_id == corrected_id
    worker_ledger = WorkerLedger(corrected, ledger.corrected_locator)
    worker = IngestionWorker(
        repository=worker_ledger,
        artifacts=artifacts,
        parser=Parser(),
        chunker=StructuralChunker(),
        embeddings=model,
        index=index,
        worker_id="worker",
        embedding_model_alias="embedding-v1",
        embedding_dimension=2,
        index_version=GENERATION,
    )
    result = await worker.run_once(1)
    assert result.ready == 1 and result.failed == 0
    assert worker_ledger.commits == [
        JobStage.CHUNKING,
        JobStage.EMBEDDING,
        JobStage.PUBLISHING,
        JobStage.READY,
    ]
    assert {"normalized", "chunks", "embeddings"} <= set(artifacts.read_kinds)

    review = review_for(corrected, "corrected-review")
    await ledger.add(review)
    for item in corrected.parse_inventory:
        review = await app.record_item_decision(
            review.review_id,
            item_id=item.item_id,
            check_kind=ReviewCheckKind.SCOPE,
            status=ReviewDecisionStatus.ACCEPTED,
            note="Compared against original image",
            actor_id="editor",
            expected_version=review.version,
            now=NOW,
        )
    review = await app.transition_review(
        review.review_id,
        target=ReviewStatus.REVIEWING,
        actor_id="reviewer",
        expected_version=review.version,
        now=NOW,
    )
    review = await app.approve_review(
        review.review_id, actor_id="reviewer", expected_version=review.version, now=NOW
    )
    publication = await app.publish_review(
        review.review_id,
        generation=GENERATION,
        idempotency_key="publish-corrected",
        actor_id="reviewer",
        expected_version=review.version,
        now=NOW,
    )
    authority = PublishedKnowledgeAuthority(ledger, now=lambda: NOW)
    hits = []
    for chunk in index.chunks:
        anchor = json.loads(chunk.anchor_json)
        hits.append(
            SearchHit(
                SourceFamily.DOC,
                str(chunk.chunk_id),
                str(chunk.logical_chunk_id),
                None,
                chunk.content,
                SourceRevisionRef(
                    SOURCE,
                    "doc",
                    RevisionKind.BLOB_VERSION,
                    corrected_id,
                    corrected.source_hash,
                    DocumentAnchor(
                        bbox=tuple(anchor.get("bbox", ())),
                        inventory_item_id=anchor["inventoryItemId"],
                        start_offset=anchor["startOffset"],
                        end_offset=anchor["endOffset"],
                    ),
                ),
                chunk.chunk_content_hash,
                ContentRole.SOURCE,
                IndexRevision(GENERATION, "v1", "tapper-demo-v1"),
                "embedding-v1",
                1.0,
            )
        )
    edges = tuple(hit for hit in hits if hit.content.startswith("流程图连线 "))
    assert len(edges) == 4

    class Search:
        async def search(self, execution):
            return (
                () if "business rules thresholds" in execution.plan.sanitized_query else (hits[0],)
            )

        async def flowchart_edges(self, execution):
            assert execution.approved_item_scope[0][0] == corrected_id
            return edges

    ids = iter(f"lifecycle-{i}" for i in range(100))
    knowledge = KnowledgeAPI(
        search=Search(),
        embeddings=model,
        answers=model,
        policy_verifier=Policy(),
        redactor=Redactor(),
        id_factory=lambda: next(ids),
        publication_authority=authority,
    )
    selected = (ReadyDocumentRevision(str(DOC), corrected_id, corrected.source_hash, SOURCE),)
    policy = build_demo_policy_context(selected)
    request = AnswerRequest(
        "高风险支付失败后重试成功经过哪些步骤？",
        AnswerMode.QUICK,
        (SourceFamily.DOC,),
        (
            ResourceRef(
                SourceFamily.DOC, SOURCE, ResourceMode.SCOPE, requested_revision=corrected_id
            ),
        ),
    )
    answer = await knowledge.answer(request, policy)
    assert not answer.abstained
    assert len(answer.citations) == 4
    assert all(c.publication_id == publication.publication_id for c in answer.citations)
    assert all(c.source.revision == corrected_id for c in answer.citations)
    assert model.context["coverage"] == "complete-published-approved-edge-set"
    assert model.context["businessRulesStatus"] == "unknown-from-image"
    await app.withdraw_publication(
        publication.publication_id,
        idempotency_key="withdraw-corrected",
        actor_id="reviewer",
        expected_version=publication.version,
        now=NOW,
    )
    with pytest.raises(AuthorizationDenied):
        await knowledge.answer(request, policy)
