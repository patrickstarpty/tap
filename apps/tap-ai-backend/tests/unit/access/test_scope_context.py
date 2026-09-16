"""Trusted scope objects must reject malformed identities and client widening."""

from dataclasses import FrozenInstanceError

import pytest


def test_scope_identity_objects_are_discriminated_and_immutable() -> None:
    from tap.modules.access.domain.context import (
        AnonymousContext,
        IdentityMode,
        PlatformScopeContext,
        ProjectScopeContext,
    )

    anonymous = AnonymousContext(enterprise_id="local")
    platform = PlatformScopeContext(
        enterprise_id="local", actor_id="admin", identity_mode=IdentityMode.PRODUCT
    )
    project = ProjectScopeContext(
        enterprise_id="local",
        project_id="tapper-demo",
        actor_id="tapper-local-user",
        identity_mode=IdentityMode.VALIDATION,
    )
    assert (anonymous.scope_kind, platform.scope_kind, project.scope_kind) == (
        "ANONYMOUS",
        "PLATFORM",
        "PROJECT",
    )
    assert not hasattr(anonymous, "actor_id")
    assert not hasattr(platform, "project_id")
    with pytest.raises(FrozenInstanceError):
        project.project_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field,value",
    [
        ("enterprise_id", ""),
        ("project_id", " x"),
        ("actor_id", "a/../b"),
        ("identity_mode", "validation"),
    ],
)
def test_scope_rejects_invalid_identity_fields(field: str, value: str) -> None:
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext

    values = dict(
        enterprise_id="local",
        project_id="tapper-demo",
        actor_id="tapper-local-user",
        identity_mode=IdentityMode.VALIDATION,
    )
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        ProjectScopeContext(**values)


@pytest.mark.asyncio
async def test_scope_provider_uses_fixed_identity_and_rejects_path_override() -> None:
    from tap.modules.access.adapters.validation import ValidationScopeProvider
    from tap.modules.access.application.scope import RequestFacts
    from tap.modules.access.domain.policy import AuthorizationDenied

    provider = ValidationScopeProvider()
    scope = await provider.current(RequestFacts())
    assert (scope.enterprise_id, scope.project_id, scope.actor_id, scope.identity_mode.value) == (
        "local",
        "tapper-demo",
        "tapper-local-user",
        "validation",
    )
    assert await provider.current(RequestFacts(project_id="tapper-demo")) == scope
    with pytest.raises(AuthorizationDenied, match="scope-mismatch"):
        await provider.current(RequestFacts(project_id="other"))
    with pytest.raises(TypeError):
        RequestFacts(actor_id="client-admin")
