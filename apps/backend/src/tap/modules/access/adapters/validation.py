"""Loopback validation composition; fixed identity is not personal authentication."""

from tap.modules.access.application.ports import IdentityRegistry
from tap.modules.access.application.scope import RequestFacts
from tap.modules.access.domain.authorization import AuthorizationDecision, ResourceRef
from tap.modules.access.domain.context import IdentityContext, IdentityMode, ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable

VALIDATION_SCOPE = ProjectScopeContext(
    enterprise_id="local",
    project_id="tapper-demo",
    actor_id="tapper-local-user",
    identity_mode=IdentityMode.VALIDATION,
)

# Closed vocabulary for the existing Knowledge slice. Later VG capabilities add
# their own explicit pairs; platform/user/project administration is never implicit.
_VALIDATION_ACTIONS = frozenset(
    {
        ("knowledge.read", "knowledge"),
        ("knowledge.write", "knowledge"),
        ("knowledge.delete", "knowledge"),
        ("knowledge.search", "knowledge"),
        ("knowledge.answer", "knowledge"),
        ("knowledge.operate", "knowledge"),
    }
)


class ValidationScopeProvider:
    async def current(self, request: RequestFacts) -> ProjectScopeContext:
        if not isinstance(request, RequestFacts):
            raise TypeError("scope provider requires RequestFacts")
        if request.project_id is not None and request.project_id != VALIDATION_SCOPE.project_id:
            raise AuthorizationDenied("scope-mismatch")
        return VALIDATION_SCOPE


class ValidationAuthorizationPolicy:
    def __init__(self, registry: IdentityRegistry) -> None:
        self._registry = registry

    async def authorize(
        self, scope: IdentityContext, action: str, resource: ResourceRef
    ) -> AuthorizationDecision:
        if not isinstance(scope, ProjectScopeContext) or scope != VALIDATION_SCOPE:
            return AuthorizationDecision(False, "scope-mismatch")
        if not isinstance(resource, ResourceRef) or (
            resource.enterprise_id,
            resource.project_id,
        ) != (scope.enterprise_id, scope.project_id):
            return AuthorizationDecision(False, "scope-mismatch")
        if (action, resource.kind) not in _VALIDATION_ACTIONS:
            return AuthorizationDecision(False, "action-not-allowed")
        try:
            principal = await self._registry.get_principal(
                scope.enterprise_id, scope.project_id, scope.actor_id
            )
        except Exception as error:
            raise PolicyUnavailable("current identity registry is unavailable") from error
        if principal is None or (
            principal.enterprise_id,
            principal.actor_id,
            principal.principal_type,
        ) != (scope.enterprise_id, scope.actor_id, "VALIDATION"):
            return AuthorizationDecision(False, "principal-unavailable")
        if not principal.enabled:
            return AuthorizationDecision(False, "principal-disabled")
        return AuthorizationDecision(True, "validation-allowed")
