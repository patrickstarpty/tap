from __future__ import annotations

from datetime import datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import ModelGatewayRejected, ModelGatewayUnavailable
from tap.modules.graph.application.jobs import InMemoryGraphJobStore
from tap.modules.graph.application.worker import GraphWorker
from tap.modules.graph.domain.jobs import TRANSIENT_GRAPH_FAILURES, GraphJobRequest
from tap.modules.knowledge.domain.documents import ChunkDraft


def test_transient_graph_failures_cover_model_unavailability_and_timeouts() -> None:
    assert TRANSIENT_GRAPH_FAILURES == frozenset({"model-unavailable", "model-timeout"})
    assert "graph-extraction-failed" not in TRANSIENT_GRAPH_FAILURES
    assert "attempts-exhausted" not in TRANSIENT_GRAPH_FAILURES


class Artifacts:
    async def read_chunks(self, locator):  # type: ignore[no-untyped-def]
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


class FailingExtractor:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def extract(self, request):  # type: ignore[no-untyped-def]
        raise self._error


class RecordingJobs(InMemoryGraphJobStore):
    def __init__(self) -> None:
        super().__init__()
        self.failure_codes: list[str] = []

    async def fail(self, scope, claim, *, failure_code, now):  # type: ignore[no-untyped-def]
        self.failure_codes.append(failure_code)
        return await super().fail(scope, claim, failure_code=failure_code, now=now)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ModelGatewayUnavailable(), "model-unavailable"),
        (TimeoutError(), "model-timeout"),
        (ModelGatewayRejected(), "graph-extraction-failed"),
        (ValueError("graph extraction output is malformed"), "graph-extraction-failed"),
    ],
)
async def test_worker_classifies_extraction_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    jobs = RecordingJobs()
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="qwen-plus",
    )
    await jobs.request(VALIDATION_SCOPE, request, now=datetime(2026, 11, 3, 9, 0, 0))
    worker = GraphWorker(
        jobs=jobs,
        artifacts=Artifacts(),  # type: ignore[arg-type]
        extractor=FailingExtractor(error),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
    )
    monkeypatch.setattr(worker, "_now", lambda: datetime(2026, 11, 3, 9, 0, 1))

    result = await worker.run_once(limit=1)

    assert result.failed == 1
    assert jobs.failure_codes == [expected]
