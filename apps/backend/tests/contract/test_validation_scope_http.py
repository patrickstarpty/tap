"""Trusted Project routing must reject caller authority before external I/O."""

from typing import cast

import pytest
from fastapi.testclient import TestClient

from tap.contracts.http import DocumentPage
from tap.entrypoints.tapper_runtime import create_project_audit
from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices, KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE, ValidationScopeProvider
from tap.modules.access.domain.authorization import AuthorizationDecision

ORIGIN = "http://127.0.0.1:15175"
PATH = "/api/v1/projects/tapper-demo/knowledge/documents"


class Authority:
    def __init__(self):
        self.calls = []

    async def current(self, facts):
        self.calls.append("scope")
        return await ValidationScopeProvider().current(facts)

    async def authorize(self, scope, action, resource):
        self.calls.append(action)
        return AuthorizationDecision(True, "validation-allowed")


class Knowledge:
    scope = VALIDATION_SCOPE

    def __init__(self):
        self.calls = []

    async def list_documents(self, cursor, limit):
        self.calls.append("list")
        return DocumentPage(items=[], next_cursor=None)

    async def delete_document(self, document_id):
        self.calls.append("delete")


def scoped_app(*, validation_mode=False):
    authority, knowledge = Authority(), Knowledge()
    app = create_app(
        HttpServices(
            knowledge=cast(KnowledgeHttpService, knowledge),
            scope_provider=authority,
            authorization_policy=authority,
            scope=VALIDATION_SCOPE,
        ),
        validation_mode=validation_mode,
        allowed_origins=frozenset({ORIGIN}),
    )
    return app, authority, knowledge


def test_project_route_and_runtime_report_server_authority():
    app, authority, knowledge = scoped_app()
    client = TestClient(app)
    assert client.get(PATH).status_code == 200
    assert authority.calls == ["scope", "knowledge.read"]
    assert knowledge.calls == ["list"]
    assert client.get("/api/v1/runtime-mode").json() == {
        "mode": "validation",
        "projectId": "tapper-demo",
        "actorId": "tapper-local-user",
        "identityMode": "validation",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"headers": {"X-Actor-Id": "fake"}},
        {"headers": {"X-Role": "admin"}},
        {"headers": {"X-Enterprise-Id": "other"}},
        {"headers": {"Cookie": "actorId=fake"}},
        {"params": {"project_id": "other"}},
    ],
)
def test_override_rejected_before_authority_or_service_io(kwargs):
    app, authority, knowledge = scoped_app()
    response = TestClient(app).get(PATH, **kwargs)
    assert response.status_code == 403
    assert response.json()["type"] == "https://tap.example/problems/authorization-denied"
    assert authority.calls == knowledge.calls == []


def test_wrong_project_rejected_before_authority_or_service_io():
    app, authority, knowledge = scoped_app()
    response = TestClient(app).get(PATH.replace("tapper-demo", "other"))
    assert response.status_code == 403
    assert response.json()["type"] == "https://tap.example/problems/scope-mismatch"
    assert authority.calls == knowledge.calls == []


def test_json_override_rejected_before_authority_io():
    app, authority, knowledge = scoped_app()
    response = TestClient(app).post(
        PATH.replace("documents", "answers"),
        headers={"Origin": ORIGIN},
        json={"query": "hello", "actorId": "fake"},
    )
    assert response.status_code == 403
    assert authority.calls == knowledge.calls == []


def test_aliases_exist_only_in_validation_profile_and_are_deprecated():
    app, _, _ = scoped_app()
    assert TestClient(app).get("/v1/knowledge/documents").status_code == 404
    app, authority, _ = scoped_app(validation_mode=True)
    assert TestClient(app).get("/v1/knowledge/documents").status_code == 200
    assert authority.calls == ["scope", "knowledge.read"]
    schema = app.openapi()
    assert schema["paths"]["/v1/knowledge/documents"]["get"]["deprecated"] is True
    assert not schema["paths"][PATH.replace("tapper-demo", "{project_id}")]["get"].get("deprecated")
    ids = [op["operationId"] for path in schema["paths"].values() for op in path.values()]
    assert len(ids) == len(set(ids))


def test_multipart_identity_override_rejected_before_authority_io():
    app, authority, knowledge = scoped_app()
    response = TestClient(app).post(
        PATH,
        headers={"Origin": ORIGIN},
        data={"actorId": "fake"},
        files={"upload": ("source.txt", b"evidence", "text/plain")},
    )
    assert response.status_code == 403
    assert authority.calls == knowledge.calls == []


@pytest.mark.parametrize(
    "method,suffix,kwargs,action",
    [
        ("get", "/documents", {}, "knowledge.read"),
        ("get", "/documents/doc", {}, "knowledge.read"),
        ("delete", "/documents/doc", {}, "knowledge.delete"),
        ("post", "/documents/doc/retry", {}, "knowledge.write"),
        (
            "post",
            "/documents",
            {"files": {"upload": ("a.txt", b"a", "text/plain")}},
            "knowledge.write",
        ),
        ("post", "/answers", {"json": {"query": "hello"}}, "knowledge.answer"),
        ("get", "/citations/citation", {}, "knowledge.read"),
    ],
)
def test_every_route_requires_its_closed_policy_action(method, suffix, kwargs, action):
    app, authority, knowledge = scoped_app()

    async def deny(scope, requested_action, resource):
        assert scope == VALIDATION_SCOPE
        assert resource.enterprise_id == "local"
        assert resource.project_id == "tapper-demo"
        assert resource.kind == "knowledge"
        authority.calls.append(requested_action)
        return AuthorizationDecision(False, "principal-disabled")

    authority.authorize = deny
    response = TestClient(app).request(
        method,
        "/api/v1/projects/tapper-demo/knowledge" + suffix,
        headers={"Origin": ORIGIN},
        **kwargs,
    )
    assert response.status_code == 403
    assert authority.calls == ["scope", action]
    assert knowledge.calls == []


def test_changed_provider_scope_never_reaches_policy_or_service():
    from dataclasses import replace

    app, authority, knowledge = scoped_app()

    async def changed(_facts):
        authority.calls.append("scope")
        return replace(VALIDATION_SCOPE, project_id="other")

    authority.current = changed
    response = TestClient(app).get(PATH)
    assert response.status_code == 403
    assert response.json()["type"] == "https://tap.example/problems/scope-mismatch"
    assert authority.calls == ["scope"]
    assert knowledge.calls == []


def test_policy_outage_fails_closed_without_service_io():
    app, authority, knowledge = scoped_app()

    async def unavailable(*_args):
        raise RuntimeError("private registry credentials")

    authority.authorize = unavailable
    response = TestClient(app).get(PATH)
    assert response.status_code == 503
    assert "private" not in response.text
    assert knowledge.calls == []


def test_historical_citation_alias_is_validation_only_and_guarded():
    app, _, _ = scoped_app()
    assert TestClient(app).get("/v1/citations/citation").status_code == 404
    app, authority, knowledge = scoped_app(validation_mode=True)
    response = TestClient(app).get("/v1/citations/citation", headers={"X-Actor-Id": "fake"})
    assert response.status_code == 403
    assert authority.calls == knowledge.calls == []
    assert app.openapi()["paths"]["/v1/citations/{citation_id}"]["get"]["deprecated"]


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "application/vnd.tap+json"}])
def test_json_override_without_standard_content_type_is_rejected_before_authority(headers):
    app, authority, knowledge = scoped_app()
    response = TestClient(app).post(
        PATH.replace("documents", "answers"),
        headers={"Origin": ORIGIN, **headers},
        content='{"query":"hello","actorId":"fake"}',
    )
    assert response.status_code == 403
    assert authority.calls == knowledge.calls == []


@pytest.mark.parametrize("misbound", ["documents", "answers", "citations", "all"])
def test_actual_repository_binding_overrules_misleading_http_metadata(misbound):
    from dataclasses import replace

    from tap.interfaces.http.knowledge_service import KnowledgeHttpService as HttpKnowledge
    from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
    from tap.modules.knowledge.application.answers import AnswerService
    from tap.modules.knowledge.application.citations import CitationResolver
    from tap.modules.knowledge.application.documents import DocumentService

    database_calls = []

    class Sessions:
        kw = {"bind": object()}

        def __call__(self):
            database_calls.append("opened")
            raise AssertionError("misbound repository must not open a session")

    def repository(name):
        scope = (
            replace(VALIDATION_SCOPE, project_id="other")
            if misbound in {name, "all"}
            else VALIDATION_SCOPE
        )
        return MysqlDocumentRepository(Sessions(), scope=scope, audit_factory=create_project_audit)

    actual_service = HttpKnowledge(
        documents=DocumentService(repository=repository("documents"), artifacts=object()),
        answers=AnswerService(repository=repository("answers"), knowledge=object()),
        citations=CitationResolver(repository=repository("citations"), artifacts=object()),
    )
    app, authority, _ = scoped_app()
    app.state.http_services = HttpServices(
        knowledge=actual_service,
        scope=VALIDATION_SCOPE,
        scope_provider=authority,
        authorization_policy=authority,
    )
    response = TestClient(app, raise_server_exceptions=False).get(PATH)
    assert response.status_code == 403
    assert response.json()["type"] == "https://tap.example/problems/scope-mismatch"
    assert database_calls == authority.calls == []
