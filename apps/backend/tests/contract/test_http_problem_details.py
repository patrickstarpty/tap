"""Public HTTP error behavior for the contract-only application factory."""

from __future__ import annotations

from fastapi import Request
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable


def test_turn_endpoint_returns_rfc_9457_problem_details_for_validation_and_placeholder() -> None:
    """Replacing problem responses with FastAPI's default JSON errors must fail this test."""
    client = TestClient(
        create_app(allowed_origins=frozenset({"http://127.0.0.1:15175"})),
        headers={"Origin": "http://127.0.0.1:15175"},
    )

    validation = client.post("/v1/chats/chat-1/turns", json={})
    assert validation.status_code == 422
    assert validation.headers["content-type"].startswith("application/problem+json")
    assert validation.json() == {
        "correlationId": validation.headers["x-correlation-id"],
        "retryable": False,
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
        "correlationId": placeholder.headers["x-correlation-id"],
        "retryable": False,
        "type": "https://tap.example/problems/turn-not-implemented",
        "title": "Turn workflow not implemented",
        "status": 501,
        "detail": "The durable chat turn workflow is not available yet.",
    }


def test_openapi_documents_problem_details_for_validation_and_placeholder_failures() -> None:
    """Removing the public problem schema from either failure response must fail this test."""
    openapi = create_app().openapi()
    responses = openapi["paths"]["/v1/chats/{chat_id}/turns"]["post"]["responses"]

    for status_code in ("422", "501"):
        content = responses[status_code]["content"]
        assert set(content) == {"application/problem+json"}
        assert content["application/problem+json"]["schema"] == {
            "$ref": "#/components/schemas/ProblemDetails"
        }
        schema = openapi["components"]["schemas"]["ProblemDetails"]
        assert set(schema["required"]) == {
            "type",
            "title",
            "status",
            "detail",
            "correlationId",
            "retryable",
        }
        assert schema["properties"]["type"]["pattern"] == "^https://"
        assert schema["properties"]["status"] == {
            "maximum": 599,
            "minimum": 400,
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
        "correlationId": response.headers["x-correlation-id"],
        "retryable": True,
        "failureStage": "search",
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
        "correlationId": response.headers["x-correlation-id"],
        "retryable": True,
        "failureStage": "search",
        "type": "https://tap.example/problems/search-execution-rejected",
        "title": "Search execution rejected",
        "status": 503,
        "detail": "The search execution exceeded a safety bound.",
    }


def test_correlation_is_per_request_and_captured_for_downstream_use() -> None:
    async def inspect(request: Request) -> dict[str, str]:
        return {"captured": request.state.correlation_id}

    app = create_app()
    app.add_api_route("/_test/correlation", inspect)
    client = TestClient(app)
    first = client.get("/_test/correlation", headers={"x-correlation-id": "a" * 1000})
    second = client.post("/v1/chats/chat-1/turns", json={})
    assert first.json()["captured"] == first.headers["x-correlation-id"]
    assert len(first.json()["captured"]) <= 128
    assert first.json()["captured"] != second.json()["correlationId"]


def test_authorization_and_stale_document_failures_are_distinct() -> None:
    from tap.modules.access.domain.policy import AuthorizationDenied
    from tap.modules.knowledge.application.answers import DocumentStateChanged

    async def denied() -> None:
        raise AuthorizationDenied("private-provider-secret")

    async def scope_denied() -> None:
        raise AuthorizationDenied("scope-mismatch")

    async def stale() -> None:
        raise DocumentStateChanged()

    app = create_app()
    for path, endpoint in (("denied", denied), ("scope", scope_denied), ("stale", stale)):
        app.add_api_route("/_test/" + path, endpoint)
    client = TestClient(app)
    for path, code, status in (
        ("denied", "authorization-denied", 403),
        ("scope", "scope-mismatch", 403),
        ("stale", "document-state-changed", 409),
    ):
        result = client.get("/_test/" + path)
        assert result.status_code == status
        assert result.json()["type"] == "https://tap.example/problems/" + code
        assert "private-provider-secret" not in result.text


def test_runtime_openapi_uses_one_resolvable_problem_component_for_every_response() -> None:
    from tap.contracts.http import ProblemDetails

    schema = create_app().openapi()
    expected = ProblemDetails.model_json_schema(by_alias=True)
    assert schema["components"]["schemas"]["ProblemDetails"] == expected
    count = 0
    for path in schema["paths"].values():
        for operation in path.values():
            for response in operation.get("responses", {}).values():
                content = response.get("content", {})
                if "application/problem+json" in content:
                    count += 1
                    assert content["application/problem+json"]["schema"] == {
                        "$ref": "#/components/schemas/ProblemDetails"
                    }
                    assert "application/json" not in content
    assert count > 1
