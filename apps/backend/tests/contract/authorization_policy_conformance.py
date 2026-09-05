"""Adapter-independent authorization and real retrieval-bridge conformance."""

from dataclasses import dataclass, replace

import pytest

from tap.modules.access.adapters.validation import ValidationScopeProvider
from tap.modules.access.application.ports import AuthorizationPolicy
from tap.modules.access.domain.authorization import ActorPrincipal, ResourceRef
from tap.modules.access.domain.context import (
    AnonymousContext,
    IdentityMode,
    PlatformScopeContext,
    ProjectScopeContext,
)
from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable
from tap.modules.knowledge.api import KnowledgeAPI, SearchRequest
from tap.modules.knowledge.application.demo_policy import (
    DemoCurrentPolicyVerifier,
    build_demo_policy_context,
)
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision


@dataclass
class IdentityRegistry:
    enabled: bool = True
    unavailable: bool = False

    async def get_principal(
        self, enterprise_id: str, project_id: str, actor_id: str
    ) -> ActorPrincipal | None:
        if self.unavailable:
            raise RuntimeError("private database details")
        if (enterprise_id, project_id, actor_id) != ("local", "tapper-demo", "tapper-local-user"):
            return None
        return ActorPrincipal(
            enterprise_id=enterprise_id,
            actor_id=actor_id,
            principal_type="VALIDATION",
            enabled=self.enabled,
        )


SCOPE = ProjectScopeContext(
    enterprise_id="local",
    project_id="tapper-demo",
    actor_id="tapper-local-user",
    identity_mode=IdentityMode.VALIDATION,
)
RESOURCE = ResourceRef(
    enterprise_id="local", project_id="tapper-demo", kind="knowledge", resource_id="doc-one"
)


class AuthorizationPolicyConformance:
    def make_policy(self, registry: IdentityRegistry) -> AuthorizationPolicy:
        raise NotImplementedError

    @pytest.mark.asyncio
    async def test_scope_identity_allows_registered_actor_and_rechecks_disable(self) -> None:
        registry = IdentityRegistry()
        policy = self.make_policy(registry)
        assert (await policy.authorize(SCOPE, "knowledge.read", RESOURCE)).allowed
        registry.enabled = False
        denied = await policy.authorize(SCOPE, "knowledge.read", RESOURCE)
        assert not denied.allowed
        assert denied.reason == "principal-disabled"

    @pytest.mark.asyncio
    async def test_operator_explicit_grant_rechecks_live_identity(self) -> None:
        registry = IdentityRegistry()
        policy = self.make_policy(registry)
        assert (await policy.authorize(SCOPE, "knowledge.operate", RESOURCE)).allowed
        assert not (
            await policy.authorize(SCOPE, "knowledge.operate", replace(RESOURCE, kind="actor"))
        ).allowed
        registry.enabled = False
        assert not (await policy.authorize(SCOPE, "knowledge.operate", RESOURCE)).allowed

    @pytest.mark.parametrize(
        "scope,action,resource",
        [
            (SCOPE, "unknown.action", RESOURCE),
            (SCOPE, "user.create", RESOURCE),
            (SCOPE, "project.create", RESOURCE),
            (SCOPE, "membership.write", RESOURCE),
            (SCOPE, "knowledge.read", replace(RESOURCE, kind="actor")),
            (SCOPE, "knowledge.read", replace(RESOURCE, project_id="other")),
            (SCOPE, "knowledge.read", replace(RESOURCE, enterprise_id="other")),
            (
                replace(SCOPE, project_id="other"),
                "knowledge.read",
                replace(RESOURCE, project_id="other"),
            ),
            (replace(SCOPE, actor_id="other"), "knowledge.read", RESOURCE),
            (replace(SCOPE, identity_mode=IdentityMode.PRODUCT), "knowledge.read", RESOURCE),
            (
                PlatformScopeContext(
                    enterprise_id="local",
                    actor_id="tapper-local-user",
                    identity_mode=IdentityMode.VALIDATION,
                ),
                "knowledge.read",
                RESOURCE,
            ),
            (AnonymousContext(enterprise_id="local"), "knowledge.read", RESOURCE),
            (None, "knowledge.read", RESOURCE),
            (SCOPE, "knowledge.read", None),
        ],
    )
    @pytest.mark.asyncio
    async def test_scope_identity_denies_invalid_authority(self, scope, action, resource) -> None:
        decision = await self.make_policy(IdentityRegistry()).authorize(scope, action, resource)
        assert not decision.allowed
        assert decision.reason

    @pytest.mark.parametrize("unavailable", [False, True])
    @pytest.mark.asyncio
    async def test_scope_identity_denial_precedes_retrieval_io(self, unavailable: bool) -> None:
        revision = ReadyDocumentRevision(
            document_id="doc-one", revision_id="rev-one", source_content_hash="sha256:" + "a" * 64
        )

        class Repository:
            calls = 0

            async def load_ready_revisions(self, document_ids):
                self.calls += 1
                return (revision,)

        registry = IdentityRegistry()
        repository = Repository()
        verifier = DemoCurrentPolicyVerifier(
            repository,
            authorization_policy=self.make_policy(registry),
            scope_provider=ValidationScopeProvider(),
        )
        expected = build_demo_policy_context((revision,))
        assert await verifier.verify_current(expected) == expected
        assert repository.calls == 1
        registry.enabled = False
        registry.unavailable = unavailable

        class ForbiddenProvider:
            async def search(self, *args, **kwargs):
                raise AssertionError("unauthorized search I/O")

            async def embed(self, *args, **kwargs):
                raise AssertionError("unauthorized embedding I/O")

            async def answer(self, *args, **kwargs):
                raise AssertionError("unauthorized model I/O")

            async def redact(self, *args, **kwargs):
                raise AssertionError("unauthorized redaction I/O")

        provider = ForbiddenProvider()
        knowledge = KnowledgeAPI(
            search=provider,
            embeddings=provider,
            answers=provider,
            redactor=provider,
            policy_verifier=verifier,
        )
        with pytest.raises(PolicyUnavailable if unavailable else AuthorizationDenied):
            await knowledge.search(SearchRequest(query="question"), expected)
        assert repository.calls == 1
