import pytest
from authorization_policy_conformance import (
    SCOPE,
    AuthorizationPolicyConformance,
    IdentityRegistry,
)

from tap.modules.access.adapters.validation import ValidationAuthorizationPolicy
from tap.modules.access.application.ports import AuthorizationPolicy
from tap.modules.access.domain.authorization import ResourceRef
from tap.modules.access.domain.policy import PolicyUnavailable


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


@pytest.mark.asyncio
async def test_request_policy_caches_only_its_principal_and_preserves_resource_denials() -> None:
    class CountingRegistry(IdentityRegistry):
        calls = 0

        async def get_principal(self, enterprise_id, project_id, actor_id):  # type: ignore[no-untyped-def]
            self.calls += 1
            return await super().get_principal(enterprise_id, project_id, actor_id)

    registry = CountingRegistry()
    policy = ValidationAuthorizationPolicy(registry)
    first_request = policy.for_request()
    assert (
        await first_request.authorize(
            SCOPE,
            "knowledge.review.edit",
            ResourceRef(
                enterprise_id=SCOPE.enterprise_id,
                project_id=SCOPE.project_id,
                kind="knowledge-review",
                resource_id="krv_one",
            ),
        )
    ).allowed
    assert (
        await first_request.authorize(
            SCOPE,
            "knowledge.review.edit",
            ResourceRef(
                enterprise_id=SCOPE.enterprise_id,
                project_id=SCOPE.project_id,
                kind="knowledge-review",
                resource_id="krv_two",
            ),
        )
    ).allowed
    assert registry.calls == 1

    wrong_resource = await policy.for_request().authorize(
        SCOPE,
        "knowledge.review.edit",
        ResourceRef(
            enterprise_id=SCOPE.enterprise_id,
            project_id="other",
            kind="knowledge-review",
            resource_id="krv_two",
        ),
    )
    assert not wrong_resource.allowed and wrong_resource.reason == "scope-mismatch"
    assert registry.calls == 1

    registry.enabled = False
    disabled = await policy.for_request().authorize(
        SCOPE,
        "knowledge.review.edit",
        ResourceRef(
            enterprise_id=SCOPE.enterprise_id,
            project_id=SCOPE.project_id,
            kind="knowledge-review",
            resource_id="krv_three",
        ),
    )
    assert not disabled.allowed and disabled.reason == "principal-disabled"
    assert registry.calls == 2

    registry.unavailable = True
    with pytest.raises(PolicyUnavailable):
        await policy.for_request().authorize(
            SCOPE,
            "knowledge.review.edit",
            ResourceRef(
                enterprise_id=SCOPE.enterprise_id,
                project_id=SCOPE.project_id,
                kind="knowledge-review",
                resource_id="krv_four",
            ),
        )
