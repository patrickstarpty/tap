"""Project authorization and versioned chunk requests keep their wire shape."""

from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.knowledge.domain.managed_chunks import new_chunk
from tests.conftest import validation_http_services

BASE = "/api/v1/projects/tapper-demo/knowledge/documents/doc_test"
ORIGIN = "http://127.0.0.1:15175"


class Manager:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE as scope

    def __init__(self):
        self.calls = []

    async def list_chunks(self, document_id, **kwargs):
        self.calls.append((document_id, kwargs))
        return dict(items=[], total=0, page=kwargs["page"], pageSize=kwargs["page_size"])

    async def create(self, document_id, content, parent_id=None, key=None):
        self.calls.append((document_id, content, parent_id, key))
        return new_chunk(content, 1, edited=True)

    async def preview_upload(self, filename, media_type, content, settings):
        self.calls.append((filename, media_type, content, settings))
        return dict(items=[], total=0)


def client():
    manager = Manager()
    return TestClient(
        create_app(
            services=replace(validation_http_services(), chunk_manager=manager),
            allowed_origins=frozenset({ORIGIN}),
        )
    ), manager


def test_search_page_and_idempotency_are_forwarded():
    http, manager = client()
    response = http.get(BASE + "/chunks?q=needle&status=disabled&page=3&pageSize=10")
    assert response.status_code == 200
    assert manager.calls[-1] == (
        "doc_test",
        dict(q="needle", status="disabled", page=3, page_size=10),
    )
    response = http.post(
        BASE + "/chunks",
        json={"content": "New content"},
        headers={"Origin": ORIGIN, "Idempotency-Key": "retry-safe"},
    )
    assert response.status_code == 200
    assert manager.calls[-1] == ("doc_test", "New content", None, "retry-safe")
    assert http.get(BASE.replace("tapper-demo", "another-project") + "/chunks").status_code == 403


def test_preupload_preview_is_bounded_and_passes_actual_file_bytes():
    http, manager = client()
    response = http.post(
        "/api/v1/projects/tapper-demo/knowledge/chunks/preview",
        files={"upload": ("example.txt", b"actual content", "text/plain")},
        data={"settings": '{"maxLength":100,"overlap":0}'},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200
    assert manager.calls[-1][:3] == ("example.txt", "text/plain", b"actual content")
    assert manager.calls[-1][3].maxLength == 100
    assert (
        http.post(BASE + "/chunks", json={"content": ""}, headers={"Origin": ORIGIN}).status_code
        == 422
    )


def test_batch_delete_requires_delete_permission_in_addition_to_write():
    from tap.modules.access.domain.authorization import AuthorizationDecision

    class WriteOnlyPolicy:
        async def authorize(self, scope, action, resource):
            return AuthorizationDecision(action != "knowledge.delete", "action-not-allowed")

    spy = Manager()
    http = TestClient(
        create_app(
            services=replace(
                validation_http_services(),
                chunk_manager=spy,
                authorization_policy=WriteOnlyPolicy(),
            ),
            allowed_origins=frozenset({ORIGIN}),
        )
    )
    response = http.post(
        BASE + "/chunks/batch",
        json={"action": "delete", "items": [{"chunkId": "one", "version": 1}]},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 403
    assert spy.calls == []


def test_original_requires_explicit_original_permission():
    from tap.modules.access.domain.authorization import AuthorizationDecision

    class ReadOnlyPolicy:
        async def authorize(self, scope, action, resource):
            return AuthorizationDecision(action != "knowledge.original.read", "action-not-allowed")

    spy = Manager()
    http = TestClient(
        create_app(
            services=replace(
                validation_http_services(), chunk_manager=spy, authorization_policy=ReadOnlyPolicy()
            ),
            allowed_origins=frozenset({ORIGIN}),
        )
    )
    assert http.get(BASE + "/original").status_code == 403
    assert spy.calls == []
