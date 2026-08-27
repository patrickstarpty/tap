"""Public HTTP contracts for the Athena local knowledge-demo slice."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tap.contracts.http import (
    CitationPreview,
    DocumentDetail,
    DocumentPage,
    HealthComponent,
    ReadyHealth,
    RetrievalAnswerRequest,
)
from tap.interfaces.http.app import create_app


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


def test_athena_routes_have_stable_provider_neutral_operation_ids() -> None:
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

    assert response.status_code == 422
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
        "citations": [],
    }

    from tap.contracts.http import RetrievalAnswerResponse

    assert RetrievalAnswerResponse.model_validate(payload).claims[1].answer_start == 27
    payload["claims"][1]["answerStart"] = 26
    with pytest.raises(ValidationError):
        RetrievalAnswerResponse.model_validate(payload)
