"""Resolve trusted Project scope and authorize before any Knowledge service I/O."""

from collections.abc import Awaitable, Callable

from fastapi import Request

from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.application.policy import require_authorized
from tap.modules.access.application.scope import RequestFacts
from tap.modules.access.domain.authorization import ResourceRef
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

_IDENTITY_FIELDS = frozenset(
    {
        "actor",
        "actorid",
        "user",
        "userid",
        "role",
        "roles",
        "enterprise",
        "enterpriseid",
        "tenant",
        "tenantid",
        "project",
        "projectid",
        "identity",
        "identitymode",
        "scope",
        "scopekind",
        "groupids",
        "allowedgroupids",
        "permissions",
        "principal",
        "principalid",
    }
)


def _authority_key(key: str) -> bool:
    normalized = key.lower().replace("-", "").replace("_", "")
    return normalized in _IDENTITY_FIELDS or (
        normalized.startswith("x") and normalized[1:] in _IDENTITY_FIELDS
    )


def _has_authority(value: object) -> bool:
    if isinstance(value, dict):
        return any(_authority_key(str(key)) or _has_authority(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_authority(item) for item in value)
    return False


async def resolve_project_scope(request: Request) -> ProjectScopeContext:
    if any(
        _authority_key(key)
        for source in (request.headers, request.cookies, request.query_params)
        for key in source
    ):
        raise AuthorizationDenied("caller-authority-forbidden")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not content_type or content_type == "application/json" or content_type.endswith("+json"):
        try:
            value = await request.json()
        except ValueError:
            value = None
        if _has_authority(value):
            raise AuthorizationDenied("caller-authority-forbidden")
    if content_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        if any(_authority_key(key) for key in await request.form()):
            raise AuthorizationDenied("caller-authority-forbidden")
    services: HttpServices = request.app.state.http_services
    if services.scope is None or services.scope_provider is None:
        raise KnowledgeRuntimeUnavailable
    project_id = request.path_params.get("project_id")
    if project_id is not None and project_id != services.scope.project_id:
        raise AuthorizationDenied("scope-mismatch")
    if services.knowledge is not None and (
        getattr(services.knowledge, "scope", None) != services.scope
    ):
        raise AuthorizationDenied("scope-mismatch")
    scope = await services.scope_provider.current(RequestFacts(project_id=project_id))
    if not isinstance(scope, ProjectScopeContext) or scope != services.scope:
        raise AuthorizationDenied("scope-mismatch")
    if getattr(request.app.state, "validation_mode", False) and (
        scope.identity_mode is not IdentityMode.VALIDATION
    ):
        raise AuthorizationDenied("scope-mismatch")
    return scope


def project_authorization(action: str) -> Callable[[Request], Awaitable[None]]:
    async def authorize(request: Request) -> None:
        scope = await resolve_project_scope(request)
        services: HttpServices = request.app.state.http_services
        if services.authorization_policy is None:
            raise KnowledgeRuntimeUnavailable
        await require_authorized(
            services.authorization_policy,
            scope,
            action,
            ResourceRef(
                enterprise_id=scope.enterprise_id, project_id=scope.project_id, kind="knowledge"
            ),
        )
        request.state.project_scope = scope

    return authorize
