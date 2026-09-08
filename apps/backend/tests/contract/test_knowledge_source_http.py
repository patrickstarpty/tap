"""Canonical Source routes are bounded and require explicit mutation intent."""

import pytest
from conftest import validation_http_services
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app

BASE = "/api/v1/projects/tapper-demo/knowledge/sources"
SOURCE = "src_" + "a" * 32
ORIGIN = "http://127.0.0.1:15175"


class SourceHttpSpy:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE as scope

    def __init__(self):
        self.calls = []

    async def list_sources(self, cursor, limit):
        self.calls.append(("list", cursor, limit))
        return {"items": [], "nextCursor": None}

    async def get_source(self, source_id, cursor, limit):
        from tap.modules.knowledge.domain.sources import SourceUnavailable

        self.calls.append(("detail", source_id, cursor, limit))
        raise SourceUnavailable("source-not-found")

    async def delete_source(self, source_id, key, correlation):
        self.calls.append(("delete", source_id, key, correlation))

    async def retry_source(self, source_id, request, key, correlation):
        from tap.modules.knowledge.domain.sources import SourceCommandConflict

        self.calls.append(("retry", source_id, request, key, correlation))
        raise SourceCommandConflict("changed intent")


def source_client():
    spy = SourceHttpSpy()
    return TestClient(
        create_app(
            services=validation_http_services(knowledge=spy), allowed_origins=frozenset({ORIGIN})
        )
    ), spy


def test_source_http_preserves_targeted_retry_key_and_safe_conflict():
    client, spy = source_client()
    body = {"documentId": "doc_" + "b" * 32, "revisionId": "rev_" + "c" * 64, "expectedAttempt": 2}
    response = client.post(
        BASE + "/" + SOURCE + "/retry",
        headers={
            "Origin": ORIGIN,
            "Idempotency-Key": "opaque-Intent",
            "X-Correlation-ID": "retry-correlation",
        },
        json=body,
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert spy.calls[0][1] == SOURCE and spy.calls[0][3] == "opaque-Intent"
    assert spy.calls[0][2].model_dump(by_alias=True) == body
    assert "changed intent" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Idempotency-Key", " ")],
        [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
        [("Idempotency-Key", "x" * 129)],
    ],
)
def test_source_http_rejects_missing_blank_duplicate_or_unbounded_mutation_key(headers):
    client, spy = source_client()
    response = client.delete(BASE + "/" + SOURCE, headers=[("Origin", ORIGIN), *headers])
    assert response.status_code == 422
    assert spy.calls == []


def test_source_http_project_bounds_and_delete_success_are_not_broadly_coerced():
    client, spy = source_client()
    assert client.get(BASE + "?limit=51").status_code == 422
    assert client.get(BASE.replace("tapper-demo", "foreign")).status_code == 403
    assert spy.calls == []
    assert client.get(BASE + "?limit=50").json() == {"items": [], "nextCursor": None}
    assert client.get(BASE + "/" + SOURCE).status_code == 404
    response = client.delete(
        BASE + "/" + SOURCE, headers={"Origin": ORIGIN, "Idempotency-Key": "delete-intent"}
    )
    assert response.status_code == 204 and response.content == b""
    assert spy.calls[-1][0:3] == ("delete", SOURCE, "delete-intent")


def test_source_routes_expose_project_scoped_operations():
    paths = create_app().openapi()["paths"]
    base = "/api/v1/projects/{project_id}/knowledge/sources"
    assert base in paths
    assert set(paths[base]) >= {"get", "post"}
    assert set(paths[base + "/{source_id}"]) >= {"get", "delete"}
    assert "post" in paths[base + "/{source_id}/retry"]


def test_source_upload_refuses_missing_idempotency_key_before_execution():
    app = create_app(
        services=validation_http_services(), allowed_origins=frozenset({"http://127.0.0.1:15175"})
    )
    response = TestClient(app).post(
        BASE,
        headers={"Origin": "http://127.0.0.1:15175"},
        files={"upload": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_source_command_ledger_preserves_keys_separately_from_content_dedupe():
    from tap.platform.db.registry import load_authoritative_metadata

    tables = load_authoritative_metadata().tables
    assert "knowledge_source_command" in tables
    ledger = tables["knowledge_source_command"]
    assert {"idempotency_key", "request_digest", "result", "document_id", "operation"} <= set(
        ledger.c.keys()
    )
    from sqlalchemy import UniqueConstraint

    assert any(
        tuple(c.name for c in constraint.columns)
        == ("enterprise_id", "project_id", "idempotency_key")
        for constraint in ledger.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert not any(fk.ondelete == "CASCADE" for fk in ledger.foreign_key_constraints)
