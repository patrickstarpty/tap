"""Explicit trusted Project values and SQL predicates; no implicit default scope."""

from sqlalchemy.sql import ColumnElement, FromClause

from tap.modules.access.domain.context import ProjectScopeContext


def require_project_scope(scope: object) -> ProjectScopeContext:
    if type(scope) is not ProjectScopeContext:
        raise TypeError("repository requires a trusted ProjectScopeContext")
    return scope


def scope_values(scope: ProjectScopeContext) -> dict[str, str]:
    scope = require_project_scope(scope)
    return {
        "enterprise_id": scope.enterprise_id,
        "project_id": scope.project_id,
        "actor_id": scope.actor_id,
        "identity_mode": scope.identity_mode.value,
        "identity_origin": scope.identity_mode.value.upper(),
    }


def scope_predicates(
    table: FromClause, scope: ProjectScopeContext
) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    scope = require_project_scope(scope)
    return table.c.enterprise_id == scope.enterprise_id, table.c.project_id == scope.project_id
