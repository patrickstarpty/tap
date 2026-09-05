"""Public HTTP contracts for the Tapper local knowledge-demo slice."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tap.contracts.http import (
    CitationPreview,
    DocumentAccepted,
    DocumentDetail,
    DocumentPage,
    DocumentSummary,
    HealthComponent,
    ReadyHealth,
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
)
from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices, UploadInput
from tap.interfaces.http.routes.knowledge_documents import MAX_DOCUMENT_BYTES


def test_document_contract_is_closed_and_bounded() -> None:
    """Adding provider facts or widening status/stage values must fail this boundary."""
    schema = DocumentDetail.model_json_schema(by_alias=True)

    assert schema["additionalProperties"] is False
    assert set(schema["$defs"]["DocumentStatus"]["enum"]) == {
        "queued",
        "processing",
        "ready",
        "failed",
        "deleting",
    }
    assert set(schema["$defs"]["IngestionStage"]["enum"]) == {
        "stored",
        "parsing",
        "chunking",
        "embedding",
        "publishing",
        "ready",
    }
    serialized = json.dumps(schema)
    assert all(
        forbidden not in serialized
        for forbidden in ("blobLocator", "physicalCollection", "providerModel")
    )


def test_document_page_limit_is_closed_to_the_public_list_bound() -> None:
    """Allowing more than fifty documents in a response would bypass the UI limit."""
    schema = DocumentPage.model_json_schema(by_alias=True)
    assert schema["properties"]["items"]["maxItems"] == 50


def test_answer_request_has_no_authoritative_policy_field() -> None:
    """Letting browser requests contain policy data would defeat server-owned scope."""
    fields = RetrievalAnswerRequest.model_json_schema(by_alias=True)["properties"]
    assert not {
        "tenantId",
        "projectId",
        "allowedGroupIds",
        "classification",
        "environment",
        "corpus",
        "providerFilter",
    } & set(fields)


def test_tapper_routes_have_stable_provider_neutral_operation_ids() -> None:
    """Renaming a public operation would break generated clients and integrations."""
    paths = create_app().openapi()["paths"]

    assert paths["/v1/knowledge/documents"]["post"]["operationId"] == "knowledge_upload_document"
    assert paths["/v1/knowledge/documents"]["get"]["operationId"] == "knowledge_list_documents"
    assert (
        paths["/v1/knowledge/documents/{document_id}"]["get"]["operationId"]
        == "knowledge_get_document"
    )
    assert (
        paths["/v1/knowledge/documents/{document_id}/retry"]["post"]["operationId"]
        == "knowledge_retry_document"
    )
    assert (
        paths["/v1/knowledge/documents/{document_id}"]["delete"]["operationId"]
        == "knowledge_delete_document"
    )
    assert paths["/v1/knowledge/answers"]["post"]["operationId"] == "knowledge_create_answer"
    assert paths["/v1/citations/{citation_id}"]["get"]["operationId"] == "citation_get_preview"
    assert paths["/health/live"]["get"]["operationId"] == "health_get_live"
    assert paths["/health/ready"]["get"]["operationId"] == "health_get_ready"
    assert paths["/v1/knowledge/documents"]["post"]["responses"]["202"]


def test_public_routes_publish_problem_details_media_type() -> None:
    """Default FastAPI JSON errors would violate the public error contract."""
    app = create_app()
    response = TestClient(app).get("/v1/knowledge/documents")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == "https://tap.example/problems/knowledge-runtime-unavailable"


def test_knowledge_route_error_schemas_are_all_problem_details() -> None:
    """A default JSON error schema would make generated clients mishandle public failures."""
    paths = create_app().openapi()["paths"]
    for path, method in (
        ("/v1/knowledge/documents", "get"),
        ("/v1/knowledge/documents", "post"),
        ("/v1/knowledge/documents/{document_id}", "get"),
        ("/v1/knowledge/documents/{document_id}", "delete"),
        ("/v1/knowledge/documents/{document_id}/retry", "post"),
        ("/v1/knowledge/answers", "post"),
        ("/v1/citations/{citation_id}", "get"),
    ):
        for status_code, response in paths[path][method]["responses"].items():
            if status_code.startswith("4") or status_code.startswith("5"):
                assert set(response["content"]) == {"application/problem+json"}


def test_knowledge_routes_publish_the_closed_task_six_error_statuses() -> None:
    paths = create_app().openapi()["paths"]
    expected = {
        ("/v1/knowledge/documents", "post"): {"400", "413", "422", "429", "503"},
        ("/v1/knowledge/documents", "get"): {"422", "503"},
        ("/v1/knowledge/documents/{document_id}", "get"): {"404", "422", "503"},
        ("/v1/knowledge/documents/{document_id}", "delete"): {
            "404",
            "409",
            "422",
            "503",
        },
        ("/v1/knowledge/documents/{document_id}/retry", "post"): {
            "404",
            "409",
            "422",
            "503",
        },
        ("/v1/knowledge/answers", "post"): {"400", "409", "422", "503"},
        ("/v1/citations/{citation_id}", "get"): {"404", "422", "503"},
    }

    for (path, method), statuses in expected.items():
        actual = {code for code in paths[path][method]["responses"] if code.startswith(("4", "5"))}
        assert actual == statuses


def test_document_list_limit_is_validated_at_the_route_boundary() -> None:
    """A route accepting zero or fifty-one items would exceed fixed server bounds."""
    client = TestClient(create_app())

    for limit in ("0", "51"):
        response = client.get(f"/v1/knowledge/documents?limit={limit}")
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")


def test_invalid_upload_metadata_is_a_public_problem_not_a_server_error() -> None:
    """Unsafe display filenames must not escape as a framework error or be accepted."""
    response = TestClient(create_app()).post(
        "/v1/knowledge/documents",
        files={"upload": ("../secret.txt", b"untrusted", "text/plain")},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == "https://tap.example/problems/unsupported-document"


def test_citation_preview_is_a_closed_public_dto() -> None:
    """A preview carrying a Blob or provider location would leak storage internals."""
    schema = CitationPreview.model_json_schema(by_alias=True)
    assert schema["additionalProperties"] is False
    assert "blobLocator" not in json.dumps(schema)


def test_ready_health_requires_each_fixed_dependency_once() -> None:
    """Duplicating one component would hide a missing runtime dependency from clients."""
    components = [HealthComponent(name="mysql", state="ok")] * 5
    with pytest.raises(ValidationError):
        ReadyHealth(status="unready", components=components)


def test_health_remediation_codes_are_closed_and_match_the_component() -> None:
    """Open-ended remediation text could leak provider endpoints or credentials."""
    schema = HealthComponent.model_json_schema(by_alias=True)
    assert set(schema["$defs"]["HealthRemediationCode"]["enum"]) == {
        "start-mysql",
        "start-redis",
        "start-blob",
        "start-milvus",
        "configure-models",
    }
    with pytest.raises(ValidationError):
        HealthComponent(name="mysql", state="failed", remediationCode="start-redis")


def test_answer_claim_spans_must_be_complete_non_overlapping_paragraphs() -> None:
    """Partial, overlapping, or mismatched claims would make citations misleading."""
    payload = {
        "traceId": "trace-1",
        "queryPlanId": "plan-1",
        "contextSnapshotId": "snapshot-1",
        "corpusVersion": "corpus-1",
        "retrievalProfileId": "quick-hybrid-v1",
        "degradedMode": False,
        "answer": "First grounded paragraph.\n\nSecond grounded paragraph.",
        "abstained": False,
        "claims": [
            {
                "claimId": "claim-1",
                "text": "First grounded paragraph.",
                "answerStart": 0,
                "answerEnd": 25,
                "citationIds": ["citation-1"],
            },
            {
                "claimId": "claim-2",
                "text": "Second grounded paragraph.",
                "answerStart": 27,
                "answerEnd": 53,
                "citationIds": ["citation-2"],
            },
        ],
        "citations": [_retrieval_citation("citation-1"), _retrieval_citation("citation-2")],
    }

    assert RetrievalAnswerResponse.model_validate(payload).claims[1].answer_start == 27
    payload["claims"][1]["answerStart"] = 26
    with pytest.raises(ValidationError):
        RetrievalAnswerResponse.model_validate(payload)


def test_non_abstained_claims_require_existing_bounded_citations() -> None:
    """Ungrounded or unbounded answer claims would make evidence verification impossible."""
    payload = {
        "traceId": "trace-1",
        "queryPlanId": "plan-1",
        "contextSnapshotId": "snapshot-1",
        "corpusVersion": "corpus-1",
        "retrievalProfileId": "quick-hybrid-v1",
        "degradedMode": False,
        "answer": "Grounded paragraph.",
        "abstained": False,
        "claims": [
            {
                "claimId": "claim-1",
                "text": "Grounded paragraph.",
                "answerStart": 0,
                "answerEnd": 19,
                "citationIds": ["citation-1"],
            }
        ],
        "citations": [_retrieval_citation("citation-1")],
    }

    RetrievalAnswerResponse.model_validate(payload)
    payload["claims"][0]["citationIds"] = []
    with pytest.raises(ValidationError):
        RetrievalAnswerResponse.model_validate(payload)
    payload["claims"][0]["citationIds"] = ["missing-citation"]
    with pytest.raises(ValidationError):
        RetrievalAnswerResponse.model_validate(payload)

    schema = RetrievalAnswerResponse.model_json_schema(by_alias=True)
    assert schema["properties"]["citations"]["maxItems"] == 20


def test_abstained_claims_cannot_reference_missing_citations() -> None:
    """Abstention must not open a hole in the public claim-to-citation graph."""
    payload = {
        "traceId": "trace-1",
        "queryPlanId": "plan-1",
        "contextSnapshotId": "snapshot-1",
        "corpusVersion": "corpus-1",
        "retrievalProfileId": "quick-hybrid-v1",
        "degradedMode": False,
        "answer": "Grounded paragraph.",
        "abstained": True,
        "claims": [
            {
                "claimId": "claim-1",
                "text": "Grounded paragraph.",
                "answerStart": 0,
                "answerEnd": 19,
                "citationIds": ["missing-citation"],
            }
        ],
        "citations": [],
    }

    with pytest.raises(ValidationError):
        RetrievalAnswerResponse.model_validate(payload)


def test_claim_text_must_be_exactly_one_unique_answer_paragraph() -> None:
    """Substring matches or embedded paragraph separators would make spans ambiguous."""
    payload = {
        "traceId": "trace-1",
        "queryPlanId": "plan-1",
        "contextSnapshotId": "snapshot-1",
        "corpusVersion": "corpus-1",
        "retrievalProfileId": "quick-hybrid-v1",
        "degradedMode": False,
        "answer": "Claim exact.\n\nAnother paragraph mentions Claim exact.",
        "abstained": False,
        "claims": [
            {
                "claimId": "claim-1",
                "text": "Claim exact.",
                "answerStart": 0,
                "answerEnd": 12,
                "citationIds": ["citation-1"],
            }
        ],
        "citations": [_retrieval_citation("citation-1")],
    }

    assert RetrievalAnswerResponse.model_validate(payload).claims[0].answer_start == 0

    payload["answer"] = "First.\n\nSecond."
    payload["claims"][0].update({"text": "First.\n\nSecond.", "answerStart": 0, "answerEnd": 15})
    with pytest.raises(ValidationError):
        RetrievalAnswerResponse.model_validate(payload)


def test_multipart_upload_uses_streamed_file_bytes_not_total_body_length() -> None:
    """Multipart framing must not reject a file exactly at the 25 MiB file limit."""
    service = _CountingUploadService()
    client = TestClient(create_app(HttpServices(knowledge=service)))

    exact = client.post(
        "/v1/knowledge/documents",
        files={"upload": ("exact.txt", b"x" * MAX_DOCUMENT_BYTES, "text/plain")},
    )
    assert exact.status_code == 202
    assert service.byte_count == MAX_DOCUMENT_BYTES

    extra_part = client.post(
        "/v1/knowledge/documents",
        data={"ignored": "x" * (20 * 1024)},
        files={"upload": ("exact.txt", b"x" * MAX_DOCUMENT_BYTES, "text/plain")},
    )
    assert extra_part.status_code == 400
    assert extra_part.headers["content-type"].startswith("application/problem+json")

    oversized = client.post(
        "/v1/knowledge/documents",
        files={"upload": ("oversized.txt", b"x" * (MAX_DOCUMENT_BYTES + 1), "text/plain")},
    )
    assert oversized.status_code == 413
    assert oversized.headers["content-type"].startswith("application/problem+json")

    response = create_app().openapi()["paths"]["/v1/knowledge/documents"]["post"]["responses"][
        "413"
    ]
    assert set(response["content"]) == {"application/problem+json"}


def _retrieval_citation(citation_id: str) -> dict[str, object]:
    return {
        "citationId": citation_id,
        "evidenceLabel": "S1",
        "chunkId": "chunk-1",
        "logicalChunkId": "logical-chunk-1",
        "source": {
            "sourceId": "repo:tap:contract.py",
            "sourceType": "code",
            "revisionKind": "git_commit",
            "revision": "a" * 40,
            "sourceContentHash": "sha256:" + "a" * 64,
            "anchor": {
                "type": "code",
                "repo": "tap",
                "path": "contract.py",
                "lineStart": 1,
                "lineEnd": 1,
            },
        },
        "chunkContentHash": "sha256:" + "b" * 64,
        "contentRole": "source",
    }


class _CountingUploadService:
    def __init__(self) -> None:
        self.byte_count = 0

    async def upload(self, upload: UploadInput) -> DocumentAccepted:
        async for chunk in upload.content:
            self.byte_count += len(chunk)
        return DocumentAccepted(
            document=DocumentSummary(
                document_id="document-1",
                filename="exact.txt",
                media_type="text/plain",
                status="queued",
                stage="stored",
                chunk_count=0,
                updated_at="2026-08-27T00:00:00Z",
            ),
            job_id="job-1",
            duplicate=False,
        )
