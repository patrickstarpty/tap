from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices, ModelCatalogHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor


class _Authority:
    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        from tap.modules.access.domain.authorization import AuthorizationDecision

        assert action == "ai.models.read"
        return AuthorizationDecision(True, "validation-allowed")


class _Catalog:
    scope = VALIDATION_SCOPE
    default_alias = "tapper-chat"

    async def list_models(self, scope):
        assert scope == VALIDATION_SCOPE
        return (
            ModelDescriptor(
                alias="tapper-chat",
                display_name="GPT-5.6 Sol",
                capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
                enabled=True,
            ),
        )


def test_model_catalog_is_project_scoped_and_omits_provider_details() -> None:
    authority = _Authority()
    app = create_app(
        HttpServices(
            model_catalog=cast(ModelCatalogHttpService, _Catalog()),
            scope_provider=authority,
            authorization_policy=authority,
            scope=VALIDATION_SCOPE,
        )
    )

    response = TestClient(app).get("/api/v1/projects/tapper-demo/ai/models")

    assert response.status_code == 200
    assert response.json() == {
        "defaultAlias": "tapper-chat",
        "items": [
            {
                "alias": "tapper-chat",
                "displayName": "GPT-5.6 Sol",
                "capabilities": ["chat", "structured"],
            }
        ],
    }


def test_catalog_cross_project_and_unavailability_use_safe_problem_details():
    from tap.modules.ai.domain.models import ModelGatewayUnavailable

    authority = _Authority()

    class Unavailable(_Catalog):
        async def list_models(self, scope):
            raise ModelGatewayUnavailable()

    app = create_app(
        HttpServices(
            model_catalog=Unavailable(),
            scope_provider=authority,
            authorization_policy=authority,
            scope=VALIDATION_SCOPE,
        )
    )
    client = TestClient(app)
    denied = client.get("/api/v1/projects/other-project/ai/models")
    assert denied.status_code == 403
    assert denied.headers["content-type"] == "application/problem+json"
    unavailable = client.get("/api/v1/projects/tapper-demo/ai/models")
    assert unavailable.status_code == 503
    assert unavailable.headers["content-type"] == "application/problem+json"
    assert unavailable.json()["type"].endswith("/model-unavailable")
    assert "provider" not in unavailable.text
