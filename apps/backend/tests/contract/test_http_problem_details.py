"""Public HTTP error behavior for the contract-only application factory."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable


def test_turn_endpoint_returns_rfc_9457_problem_details_for_validation_and_placeholder() -> None:
    """Replacing problem responses with FastAPI's default JSON errors must fail this test."""
    client = TestClient(create_app())

    validation = client.post("/v1/chats/chat-1/turns", json={})
    assert validation.status_code == 422
    assert validation.headers["content-type"].startswith("application/problem+json")
    assert validation.json() == {
        "type": "https://tap.example/problems/request-validation",
        "title": "Request validation failed",
        "status": 422,
        "detail": "The request body does not match the public API contract.",
    }

    placeholder = client.post(
        "/v1/chats/chat-1/turns",
        json={"clientRequestId": "request-1", "message": "What changed?"},
    )
    assert placeholder.status_code == 501
    assert placeholder.headers["content-type"].startswith("application/problem+json")
    assert placeholder.json() == {
        "type": "https://tap.example/problems/turn-not-implemented",
        "title": "Turn workflow not implemented",
        "status": 501,
        "detail": "The durable chat turn workflow is not available yet.",
    }


def test_openapi_documents_problem_details_for_validation_and_placeholder_failures() -> None:
    """Removing the public problem schema from either failure response must fail this test."""
    responses = create_app().openapi()["paths"]["/v1/chats/{chat_id}/turns"]["post"]["responses"]

    for status_code in ("422", "501"):
        content = responses[status_code]["content"]
        assert set(content) == {"application/problem+json"}
        schema = content["application/problem+json"]["schema"]
        assert set(schema["required"]) == {"type", "title", "status", "detail"}
        assert schema["properties"]["type"]["pattern"] == "^https://"
        assert schema["properties"]["status"] == {
            "maximum": 599,
            "minimum": 100,
            "title": "Status",
            "type": "integer",
        }


def test_search_unavailable_is_redacted_as_a_service_unavailable_problem() -> None:
    """Returning a provider exception detail would expose credentials or filter internals."""

    async def fail_search() -> None:
        raise SearchUnavailable(
            "milvus://reader:secret@127.0.0.1 raw-filter "
            "query=where-is-policy-verified group_ids=group-one,group-two vector=[0.25,0.5]"
        )

    app = create_app()
    app.add_api_route("/_test/search-unavailable", fail_search, methods=["GET"])
    response = TestClient(app).get("/_test/search-unavailable")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://tap.example/problems/search-unavailable",
        "title": "Search unavailable",
        "status": 503,
        "detail": "The search provider is currently unavailable.",
    }


def test_search_bounds_error_is_redacted_as_a_service_unavailable_problem() -> None:
    """Exposing a rejected execution detail would reveal provider-specific execution data."""

    async def fail_search() -> None:
        raise SearchBoundsExceeded(
            "milvus://reader:secret@127.0.0.1 raw-filter "
            "query=where-is-policy-verified group_ids=group-one,group-two vector=[0.25,0.5]"
        )

    app = create_app()
    app.add_api_route("/_test/search-execution-rejected", fail_search, methods=["GET"])
    response = TestClient(app).get("/_test/search-execution-rejected")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://tap.example/problems/search-execution-rejected",
        "title": "Search execution rejected",
        "status": 503,
        "detail": "The search execution exceeded a safety bound.",
    }
