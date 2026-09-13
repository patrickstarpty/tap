import pytest
from authorization_policy_conformance import (
    SCOPE,
    AuthorizationPolicyConformance,
    IdentityRegistry,
)

from tap.modules.access.adapters.validation import ValidationAuthorizationPolicy
from tap.modules.access.application.ports import AuthorizationPolicy
from tap.modules.access.domain.authorization import ResourceRef


class TestValidationIdentityPolicy(AuthorizationPolicyConformance):
    def make_policy(self, registry: IdentityRegistry) -> AuthorizationPolicy:
        return ValidationAuthorizationPolicy(registry)


@pytest.mark.asyncio
async def test_validation_identity_can_read_the_project_model_catalog() -> None:
    decision = await ValidationAuthorizationPolicy(IdentityRegistry()).authorize(
        SCOPE,
        "ai.models.read",
        ResourceRef(
            enterprise_id=SCOPE.enterprise_id,
            project_id=SCOPE.project_id,
            kind="ai",
            resource_id="project-model-catalog",
        ),
    )

    assert decision.allowed
