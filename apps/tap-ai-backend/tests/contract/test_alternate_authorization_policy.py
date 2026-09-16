"""Independent in-memory grant adapter proves the production policy is replaceable."""

from authorization_policy_conformance import AuthorizationPolicyConformance, IdentityRegistry

from tap.modules.access.application.ports import AuthorizationPolicy
from tap.modules.access.domain.authorization import AuthorizationDecision, ResourceRef
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext


class InMemoryAuthorizationPolicy:
    def __init__(self, registry: IdentityRegistry) -> None:
        self.registry = registry
        self.grants = {
            (
                "local",
                "tapper-demo",
                "tapper-local-user",
                IdentityMode.VALIDATION,
                "knowledge.read",
                "knowledge",
            )
        }
        self.grants.add(
            (
                "local",
                "tapper-demo",
                "tapper-local-user",
                IdentityMode.VALIDATION,
                "knowledge.operate",
                "knowledge",
            )
        )

    async def authorize(self, scope, action, resource):
        if not isinstance(scope, ProjectScopeContext) or not isinstance(resource, ResourceRef):
            return AuthorizationDecision(False, "scope-mismatch")
        grant = (
            scope.enterprise_id,
            scope.project_id,
            scope.actor_id,
            scope.identity_mode,
            action,
            resource.kind,
        )
        if grant not in self.grants or (scope.enterprise_id, scope.project_id) != (
            resource.enterprise_id,
            resource.project_id,
        ):
            return AuthorizationDecision(False, "grant-missing")
        principal = await self.registry.get_principal(
            scope.enterprise_id, scope.project_id, scope.actor_id
        )
        if principal is None or not principal.enabled:
            return AuthorizationDecision(False, "principal-disabled")
        return AuthorizationDecision(True, "grant-allowed")


class TestAlternateIdentityPolicy(AuthorizationPolicyConformance):
    def make_policy(self, registry: IdentityRegistry) -> AuthorizationPolicy:
        return InMemoryAuthorizationPolicy(registry)
