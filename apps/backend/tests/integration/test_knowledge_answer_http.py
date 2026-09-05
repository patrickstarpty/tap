from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable
from tap.modules.knowledge.application.answers import (
    AnswerSnapshotUnavailable,
    DocumentStateChanged,
)
from tap.modules.knowledge.application.citations import (
    CitationPreviewResult,
    CitationStale,
    CitationUnavailable,
)
from tap.modules.knowledge.application.documents import DocumentService
from tap.modules.knowledge.domain.documents import DocumentParseRejected
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerResponse,
    ModelCallProvenance,
    RetrievalProfileId,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactStore,
    DocumentCapacityExceeded,
    DocumentNotFound,
    DocumentRepository,
    InvalidDocumentCursor,
    ReservationState,
    RetryNotAllowed,
    StagedOriginal,
    UploadReservation,
)
from tap.modules.knowledge.ports.errors import (
    AnswerUnavailable,
    ArtifactUnavailable,
    ModelUnavailable,
    SearchUnavailable,
)


class Documents:
    def __init__(self) -> None:
        self.error: Exception | None = None

    def _fail(self) -> None:
        if self.error is not None:
            raise self.error

    async def upload(self, upload):  # type: ignore[no-untyped-def]
        self._fail()
        raise AssertionError(upload)

    async def list_documents(self, cursor, limit):  # type: ignore[no-untyped-def]
        self._fail()
        raise AssertionError((cursor, limit))

    async def get_document(self, document_id):  # type: ignore[no-untyped-def]
        self._fail()
        raise AssertionError(document_id)

    async def retry_document(self, document_id):  # type: ignore[no-untyped-def]
        self._fail()
        raise AssertionError(document_id)

    async def delete_document(self, document_id):  # type: ignore[no-untyped-def]
        self._fail()
        raise AssertionError(document_id)


class Answers:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.requests = []

    async def answer(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return AnswerResponse(
            trace_id="trace-a",
            query_plan_id="plan-a",
            context_snapshot_id="context-a",
            corpus_version="tapper-demo-v1",
            retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
            answer="",
            abstained=True,
            abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
            claims=(),
            citations=(),
            embedding_provenance=ModelCallProvenance("tapper-embedding", "embed-a"),
            answer_provenance=None,
        )


class Citations:
    def __init__(self) -> None:
        self.error: Exception | None = None

    async def resolve(self, citation_id: str) -> CitationPreviewResult:
        if self.error is not None:
            raise self.error
        from tap.modules.knowledge.domain.models import DocumentAnchor

        return CitationPreviewResult(
            citation_id=citation_id,
            document_id="doc-a",
            revision_id="rev-a",
            filename="policy.md",
            source_content_hash="sha256:" + "a" * 64,
            chunk_content_hash="sha256:" + "b" * 64,
            anchor=DocumentAnchor(heading_path=("Policy",), start_offset=0, end_offset=8),
            quote="Evidence",
        )


@dataclass
class Harness:
    client: TestClient
    documents: Documents
    answers: Answers
    citations: Citations


def harness() -> Harness:
    documents = Documents()
    answers = Answers()
    citations = Citations()
    service = KnowledgeHttpService(
        documents=documents,
        answers=answers,
        citations=citations,
    )
    return Harness(
        client=TestClient(
            create_app(HttpServices(knowledge=service)),
            raise_server_exceptions=False,
        ),
        documents=documents,
        answers=answers,
        citations=citations,
    )


def answer_payload(*document_ids: str) -> dict[str, object]:
    return {
        "query": "What is the rule?",
        "resourceRefs": [
            {"family": "doc", "sourceId": document_id, "mode": "scope"}
            for document_id in document_ids
        ],
    }


def test_http_normalizes_selection_and_maps_answer_and_citation_dtos() -> None:
    app = harness()

    answer = app.client.post("/v1/knowledge/answers", json=answer_payload("doc-a"))
    citation = app.client.get("/v1/citations/citation-a")

    assert answer.status_code == 200
    assert answer.json()["abstained"] is True
    assert app.answers.requests[0].resource_refs[0].source_id == "doc-a"
    assert citation.status_code == 200
    assert citation.json() == {
        "citationId": "citation-a",
        "documentId": "doc-a",
        "revisionId": "rev-a",
        "filename": "policy.md",
        "sourceContentHash": "sha256:" + "a" * 64,
        "chunkContentHash": "sha256:" + "b" * 64,
        "anchor": {
            "type": "document",
            "headingPath": ["Policy"],
            "page": None,
            "bbox": None,
            "startOffset": 0,
            "endOffset": 8,
        },
        "quote": "Evidence",
        "prefix": "",
        "suffix": "",
    }


def test_empty_duplicate_and_hidden_controls_are_stable_400_problems() -> None:
    app = harness()
    empty = app.client.post("/v1/knowledge/answers", json=answer_payload())
    duplicate = app.client.post("/v1/knowledge/answers", json=answer_payload("doc-a", "doc-a"))
    control = answer_payload("doc-a") | {"topK": 1}
    hidden = app.client.post("/v1/knowledge/answers", json=control)

    assert (empty.status_code, empty.json()["type"]) == (
        400,
        "https://tap.example/problems/source-selection-required",
    )
    assert (duplicate.status_code, duplicate.json()["type"]) == (
        400,
        "https://tap.example/problems/source-selection-required",
    )
    assert (hidden.status_code, hidden.json()["type"]) == (
        400,
        "https://tap.example/problems/unsupported-answer-control",
    )
    assert app.answers.requests == []


def test_twenty_one_browser_refs_remain_contract_validation_422() -> None:
    app = harness()
    response = app.client.post(
        "/v1/knowledge/answers",
        json=answer_payload(*(f"doc-{index}" for index in range(21))),
    )

    assert response.status_code == 422
    assert response.json()["type"] == "https://tap.example/problems/request-validation"
    assert app.answers.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        answer_payload("doc-a") | {"query": " \n\t "},
        answer_payload("doc-a") | {"sources": ["doc", "doc"]},
    ],
)
def test_domain_invalid_request_shape_is_contract_validation_422(
    payload: dict[str, object],
) -> None:
    app = harness()

    response = app.client.post("/v1/knowledge/answers", json=payload)

    assert response.status_code == 422
    assert response.json()["type"] == "https://tap.example/problems/request-validation"
    assert app.answers.requests == []


@pytest.mark.parametrize(
    ("error", "status", "problem_type"),
    [
        (
            DocumentParseRejected("unsupported-document"),
            400,
            "https://tap.example/problems/unsupported-document",
        ),
        (
            DocumentParseRejected("empty-document"),
            400,
            "https://tap.example/problems/empty-document",
        ),
        (
            DocumentParseRejected("document-too-large"),
            413,
            "https://tap.example/problems/document-too-large",
        ),
        (
            DocumentCapacityExceeded("secret"),
            429,
            "https://tap.example/problems/document-limit-reached",
        ),
    ],
)
def test_upload_failures_are_redacted_stable_problems(
    error: Exception, status: int, problem_type: str
) -> None:
    app = harness()
    app.documents.error = error

    response = app.client.post(
        "/v1/knowledge/documents",
        files={"upload": ("policy.md", b"# Policy", "text/markdown")},
    )

    assert response.status_code == status
    assert response.json()["type"] == problem_type
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("error", "method", "path", "status", "problem_type"),
    [
        (
            DocumentNotFound("secret"),
            "get",
            "/v1/knowledge/documents/missing",
            404,
            "https://tap.example/problems/document-not-found",
        ),
        (
            RetryNotAllowed("secret"),
            "post",
            "/v1/knowledge/documents/doc-a/retry",
            409,
            "https://tap.example/problems/document-not-retryable",
        ),
        (
            InvalidDocumentCursor("secret"),
            "get",
            "/v1/knowledge/documents?cursor=opaque",
            422,
            "https://tap.example/problems/request-validation",
        ),
    ],
)
def test_document_command_failures_are_redacted_stable_problems(
    error: Exception,
    method: str,
    path: str,
    status: int,
    problem_type: str,
) -> None:
    app = harness()
    app.documents.error = error

    response = app.client.request(method, path)

    assert response.status_code == status
    assert response.json()["type"] == problem_type
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "problem_type"),
    [
        (
            DocumentStateChanged("secret"),
            409,
            "https://tap.example/problems/document-state-changed",
        ),
        (
            AuthorizationDenied("secret"),
            409,
            "https://tap.example/problems/document-state-changed",
        ),
        (
            PolicyUnavailable("secret"),
            503,
            "https://tap.example/problems/search-unavailable",
        ),
        (
            ModelUnavailable("litellm key=secret"),
            503,
            "https://tap.example/problems/embedding-unavailable",
        ),
        (
            SearchUnavailable("milvus password=secret"),
            503,
            "https://tap.example/problems/search-unavailable",
        ),
        (
            AnswerSnapshotUnavailable("mysql password=secret"),
            503,
            "https://tap.example/problems/answer-snapshot-unavailable",
        ),
    ],
)
def test_answer_failures_are_redacted_stable_problems(
    error: Exception, status: int, problem_type: str
) -> None:
    app = harness()
    app.answers.error = error

    response = app.client.post("/v1/knowledge/answers", json=answer_payload("doc-a"))

    assert response.status_code == status
    assert response.json()["type"] == problem_type
    assert "secret" not in response.text


def test_litellm_answer_failure_is_not_reported_as_embedding_failure() -> None:
    app = harness()
    app.answers.error = AnswerUnavailable("private provider detail")

    response = app.client.post("/v1/knowledge/answers", json=answer_payload("doc-a"))

    assert response.status_code == 503
    assert response.json() == {
        "type": "https://tap.example/problems/answer-unavailable",
        "title": "Answer unavailable",
        "status": 503,
        "detail": "The answer service is currently unavailable.",
    }
    assert "private provider detail" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "problem_type"),
    [
        (CitationStale("secret"), 404, "https://tap.example/problems/citation-stale"),
        (
            CitationUnavailable("secret"),
            503,
            "https://tap.example/problems/citation-unavailable",
        ),
    ],
)
def test_citation_failures_are_redacted_stable_problems(
    error: Exception, status: int, problem_type: str
) -> None:
    app = harness()
    app.citations.error = error

    response = app.client.get("/v1/citations/citation-a")

    assert response.status_code == status
    assert response.json()["type"] == problem_type
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/knowledge/documents"),
        ("get", "/v1/knowledge/documents"),
        ("get", "/v1/knowledge/documents/doc-a"),
        ("post", "/v1/knowledge/documents/doc-a/retry"),
        ("delete", "/v1/knowledge/documents/doc-a"),
    ],
)
def test_document_provider_outages_are_redacted_rfc9457_503(
    method: str,
    path: str,
) -> None:
    app = harness()
    app.documents.error = ArtifactUnavailable("azure credential=secret")

    kwargs = (
        {"files": {"upload": ("policy.md", b"# Policy", "text/markdown")}}
        if method == "post" and path == "/v1/knowledge/documents"
        else {}
    )
    response = app.client.request(method, path, **kwargs)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == ("https://tap.example/problems/knowledge-runtime-unavailable")
    assert "secret" not in response.text


def test_unexpected_rest_failure_is_a_redacted_rfc9457_fallback() -> None:
    app = harness()
    app.answers.error = RuntimeError("provider token=secret")

    response = app.client.post("/v1/knowledge/answers", json=answer_payload("doc-a"))

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == ("https://tap.example/problems/knowledge-runtime-unavailable")
    assert "secret" not in response.text


def test_unexpected_rest_fallback_does_not_consume_caller_cancellation() -> None:
    async def scenario() -> None:
        documents = Documents()
        answers = Answers()
        answers.error = asyncio.CancelledError("caller disconnected")
        service = KnowledgeHttpService(
            documents=documents,
            answers=answers,
            citations=Citations(),
        )
        transport = httpx.ASGITransport(app=create_app(HttpServices(knowledge=service)))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(asyncio.CancelledError, match="caller disconnected"):
                await client.post("/v1/knowledge/answers", json=answer_payload("doc-a"))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("phase", "message"),
    [
        ("reserve", "reserve caller disconnected"),
        ("duplicate-commit", "commit caller disconnected"),
    ],
)
def test_upload_cleanup_failure_does_not_replace_cancellation_through_asgi(
    phase: str,
    message: str,
) -> None:
    async def scenario() -> None:
        cancellation = asyncio.CancelledError(message)

        class Artifacts:
            async def stage_original(self, _upload: object, *, max_bytes: int) -> StagedOriginal:
                assert max_bytes > 0
                return StagedOriginal(
                    staging_key="staging-cancel",
                    filename="policy.md",
                    media_type="text/markdown",
                    size=1,
                    source_content_hash="sha256:" + "a" * 64,
                )

            async def commit_original(self, _staged: StagedOriginal, _revision_id: str) -> None:
                raise cancellation

            async def discard_staged(self, _staged: StagedOriginal) -> None:
                raise RuntimeError("cleanup credential=secret")

        class Repository:
            async def reserve_upload(self, _command: object) -> UploadReservation:
                if phase == "reserve":
                    raise cancellation
                return UploadReservation(
                    state=ReservationState.DUPLICATE_PENDING,
                    reservation_id="reservation-a",
                    owner_token="",
                    document_id="doc-a",
                    revision_id="rev-a",
                    dedupe_key="sha256:" + "b" * 64,
                    document=None,
                    staging_key="staging-owner",
                )

        documents = DocumentService(
            repository=cast(DocumentRepository, Repository()),
            artifacts=cast(ArtifactStore, Artifacts()),
        )
        service = KnowledgeHttpService(
            documents=documents,
            answers=cast(Any, Answers()),
            citations=cast(Any, Citations()),
        )
        transport = httpx.ASGITransport(app=create_app(HttpServices(knowledge=service)))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(asyncio.CancelledError) as caught:
                await client.post(
                    "/v1/knowledge/documents",
                    files={"upload": ("policy.md", b"# Policy", "text/markdown")},
                )

        assert caught.value is cancellation
        assert caught.value.args == (message,)

    asyncio.run(scenario())
