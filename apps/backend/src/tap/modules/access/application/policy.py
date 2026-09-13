"""Common application gate: all adapters share the same fail-closed decision seam."""

from tap.modules.access.application.ports import AuthorizationPolicy
from tap.modules.access.domain.authorization import ResourceRef
from tap.modules.access.domain.context import IdentityContext
from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable


async def require_authorized(
    policy: AuthorizationPolicy, scope: IdentityContext, action: str, resource: ResourceRef
) -> None:
    try:
        decision = await policy.authorize(scope, action, resource)
    except Exception as error:
        raise PolicyUnavailable("current authorization policy is unavailable") from error
    if not decision.allowed:
        raise AuthorizationDenied(decision.reason)
