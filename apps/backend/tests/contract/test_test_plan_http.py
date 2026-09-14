from dataclasses import replace

from apps.backend.tests.conftest import validation_http_services
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.test_management.domain.models import (
    GenerationJobStatus,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationJob as PlanGenerationJob,
)


class PlansService:
    scope = VALIDATION_SCOPE

    def __init__(self):
        self.requests = []

    async def request_generation(self, scope, request, *, now):
        assert scope == VALIDATION_SCOPE
        self.requests.append(request)
        return PlanGenerationJob(request, GenerationJobStatus.PENDING, now, now)

    async def list_revisions(self, scope):
        assert scope == VALIDATION_SCOPE
        return ()


def _client(service: PlansService) -> TestClient:
    services = replace(validation_http_services(), test_plans=service)
    origin = "http://127.0.0.1:15175"
    return TestClient(
        create_app(services, allowed_origins=frozenset({origin})),
        headers={"Origin": origin},
    )


def test_test_plan_routes_and_generation_contract_are_registered() -> None:
    app = create_app(validation_mode=True)
    paths = app.openapi()["paths"]
    assert "/api/v1/projects/{project_id}/test-plans" in paths
    assert "/api/v1/projects/{project_id}/test-plans/generations" in paths
    assert (
        "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/publish"
        in paths
    )
    edit = paths["/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}"][
        "patch"
    ]
    assert any(parameter["name"] == "If-Match" for parameter in edit["parameters"])
    operation = paths["/api/v1/projects/{project_id}/test-plans/generations"]["post"]
    assert operation["responses"]["202"]
    schema = app.openapi()["components"]["schemas"]["TestPlanGenerationRequestBody"]
    assert {"inputSnapshotDigest", "answerEvidenceSnapshotDigest"} <= set(schema["required"])


def test_generation_is_idempotent_project_scoped_and_returns_deep_link() -> None:
    service = PlansService()
    client = _client(service)
    body = {
        "conversationId": "conversation_checkout",
        "turnId": "turn_checkout",
        "inputSnapshotDigest": "sha256:" + "1" * 64,
        "answerEvidenceSnapshotDigest": "sha256:" + "2" * 64,
        "modelAlias": "tapper-chat",
        "agentRevisionId": "validation-test-design-agent-v1",
        "skillRevisionIds": ["validation-test-design-skill-v1"],
        "objective": "Design checkout tests",
    }
    response = client.post(
        "/api/v1/projects/tapper-demo/test-plans/generations",
        json=body,
        headers={"Idempotency-Key": "test-design-checkout"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "PENDING"
    assert response.json()["deepLink"].startswith("/test-management/tp_")
    assert service.requests[0].input_snapshot_digest == body["inputSnapshotDigest"]

    denied = client.post(
        "/api/v1/projects/another-project/test-plans/generations",
        json=body,
        headers={"Idempotency-Key": "test-design-checkout"},
    )
    assert denied.status_code == 403
