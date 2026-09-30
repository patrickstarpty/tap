from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.test_management.domain.models import (
    GenerationJobStatus,
    RequirementScopeItem,
    RequirementScopeSnapshot,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationJob as PlanGenerationJob,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationRequest as DomainGenerationRequest,
)
from tests.conftest import validation_http_services


class PlansService:
    scope = VALIDATION_SCOPE

    def __init__(self):
        self.requests = []
        self.generation_status = GenerationJobStatus.FAILED

    async def request_generation(self, scope, request, *, now):
        assert scope == VALIDATION_SCOPE
        self.requests.append(request)
        return PlanGenerationJob(request, GenerationJobStatus.PENDING, now, now)

    async def request_generation_from_turn(
        self,
        scope,
        *,
        conversation_id,
        turn_id,
        objective,
        idempotency_key,
        now,
    ):
        request = DomainGenerationRequest.create(
            project_id=scope.project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            input_snapshot_digest="sha256:" + "1" * 64,
            answer_evidence_snapshot_digest="sha256:" + "2" * 64,
            model_alias="qwen-plus",
            agent_revision_id="validation-test-design-agent-v1",
            skill_revision_ids=("validation-test-design-skill-v1",),
            objective=objective,
            idempotency_key=idempotency_key,
            requirement_scope=RequirementScopeSnapshot.create(
                scope_id="publication-checkout-v1",
                version=1,
                requirements=(
                    RequirementScopeItem(
                        "requirement_checkout",
                        "source_revision_checkout",
                        "section:checkout",
                    ),
                ),
            ),
            approved_knowledge_revision_ids=("source_revision_checkout",),
            model_revision_id="tmr_" + "a" * 64,
        )
        return await self.request_generation(scope, request, now=now)

    async def get_generation_job(self, scope, job_id):
        assert scope == VALIDATION_SCOPE
        request = self.requests[0]
        assert job_id == request.job_id
        now = request.created_at if hasattr(request, "created_at") else None
        from datetime import datetime, timezone

        instant = now or datetime.now(timezone.utc)
        return PlanGenerationJob(
            request,
            self.generation_status,
            instant,
            instant,
            failure_code="MODEL_GATEWAY_UNAVAILABLE",
        )

    async def cancel_generation(self, scope, job_id, *, expected_version, idempotency_key, now):
        assert scope == VALIDATION_SCOPE
        request = self.requests[0]
        assert job_id == request.job_id
        assert expected_version == 1
        assert idempotency_key
        self.generation_status = GenerationJobStatus.CANCELED
        return PlanGenerationJob(
            request,
            GenerationJobStatus.CANCELED,
            now,
            now,
            failure_code="canceled-by-user",
        )

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
    assert "/api/v1/projects/{project_id}/test-plans/generations/{job_id}/cancel" in paths
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
    assert set(schema["required"]) == {"conversationId", "turnId", "objective"}


def test_generation_is_idempotent_project_scoped_and_returns_deep_link() -> None:
    service = PlansService()
    client = _client(service)
    body = {
        "conversationId": "conversation_checkout",
        "turnId": "turn_checkout",
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
    assert service.requests[0].requirement_scope.scope_id == "publication-checkout-v1"
    assert service.requests[0].model_revision_id.startswith("tmr_")

    denied = client.post(
        "/api/v1/projects/another-project/test-plans/generations",
        json=body,
        headers={"Idempotency-Key": "test-design-checkout"},
    )
    assert denied.status_code == 403


def test_generation_status_exposes_failure_and_result_link() -> None:
    service = PlansService()
    client = _client(service)
    body = {
        "conversationId": "conversation_checkout",
        "turnId": "turn_checkout",
        "objective": "Design checkout tests",
    }
    accepted = client.post(
        "/api/v1/projects/tapper-demo/test-plans/generations",
        json=body,
        headers={"Idempotency-Key": "test-design-checkout"},
    ).json()

    status = client.get(f"/api/v1/projects/tapper-demo/test-plans/generations/{accepted['jobId']}")
    assert status.status_code == 200
    assert status.json()["failureCode"] == "MODEL_GATEWAY_UNAVAILABLE"
    assert status.json()["progress"] == "failed"

    assert status.json()["deepLink"] == accepted["deepLink"]


def test_generation_status_exposes_waiting_and_cancelled_lifecycle() -> None:
    service = PlansService()
    client = _client(service)
    body = {
        "conversationId": "conversation_checkout",
        "turnId": "turn_checkout",
        "objective": "Design checkout tests",
    }
    accepted = client.post(
        "/api/v1/projects/tapper-demo/test-plans/generations",
        json=body,
        headers={"Idempotency-Key": "test-design-waiting"},
    ).json()
    service.generation_status = GenerationJobStatus.WAITING

    waiting = client.get(f"/api/v1/projects/tapper-demo/test-plans/generations/{accepted['jobId']}")
    canceled = client.post(
        f"/api/v1/projects/tapper-demo/test-plans/generations/{accepted['jobId']}/cancel",
        headers={"If-Match": "1", "Idempotency-Key": "cancel-test-design"},
    )

    assert waiting.status_code == 200
    assert waiting.json()["status"] == "WAITING"
    assert waiting.json()["progress"] == "waiting"
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "CANCELED"
    assert canceled.json()["progress"] == "canceled"
