"""Framework-free ports for authoritative access-policy refresh."""

from __future__ import annotations

from typing import Protocol

from tap.modules.access.application.scope import RequestFacts
from tap.modules.access.domain.authorization import (
    ActorPrincipal,
    AuthorizationDecision,
    ResourceRef,
)
from tap.modules.access.domain.context import IdentityContext
from tap.modules.access.domain.policy import RetrievalPolicyContext


class CurrentPolicyVerificationPort(Protocol):
    """Return the authoritative current context, or ``None`` when unavailable."""

    async def verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext | None: ...


class ScopeProvider(Protocol):
    async def current(self, request: RequestFacts) -> IdentityContext: ...


class AuthorizationPolicy(Protocol):
    async def authorize(
        self, scope: IdentityContext, action: str, resource: ResourceRef
    ) -> AuthorizationDecision: ...


class IdentityRegistry(Protocol):
    async def get_principal(
        self, enterprise_id: str, project_id: str, actor_id: str
    ) -> ActorPrincipal | None: ...
