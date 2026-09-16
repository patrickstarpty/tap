from __future__ import annotations

from datetime import datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.jobs import InMemoryGraphJobStore
from tap.modules.graph.application.worker import GraphWorker
from tap.modules.graph.domain.jobs import GraphJobRequest, GraphJobStatus
from tap.modules.graph.domain.models import GraphSnapshotDraft
from tap.modules.knowledge.domain.documents import ChunkDraft


class Artifacts:
    async def read_chunks(self, locator):
        assert locator == "art1.chunks"
        return (
            ChunkDraft(
                chunk_id="chunk-1",
                logical_chunk_id="logical-1",
                root_id="document-1",
                parent_id=None,
                content="Claims require evidence.",
                anchor_json='{"endOffset":24,"headingPath":[],"startOffset":0,"type":"document"}',
                source_content_hash="sha256:" + "b" * 64,
                chunk_content_hash="sha256:" + "a" * 64,
            ),
        )


class Extractor:
    def __init__(self, draft: GraphSnapshotDraft) -> None:
        self._draft = draft

    async def extract(self, request):
        return self._draft


@pytest.mark.asyncio
async def test_worker_publishes_a_claimed_job_atomically(monkeypatch) -> None:
    from tap.modules.graph.adapters.fake_extraction import deterministic_draft

    jobs = InMemoryGraphJobStore()
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="tapper-chat",
    )
    job = await jobs.request(VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 9, 0, 0))
    chunks = await Artifacts().read_chunks("art1.chunks")
    draft = deterministic_draft(job.snapshot, chunks, filename="revision-1")
    worker = GraphWorker(
        jobs=jobs,
        artifacts=Artifacts(),
        extractor=Extractor(draft),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
    )
    monkeypatch.setattr(worker, "_now", lambda: datetime(2026, 9, 13, 9, 0, 1))

    result = await worker.run_once(limit=1)

    assert result.claimed == 1
    assert result.ready == 1
    assert result.failed == 0
    assert (await jobs.get_job(VALIDATION_SCOPE, job.job_id)).status is GraphJobStatus.READY
