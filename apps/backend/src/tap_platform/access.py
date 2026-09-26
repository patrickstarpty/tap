"""Framework-free project authorization for TAP Insights boundaries."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


def _require_identifier(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None
    ):
        raise ValueError(f"{name} must be a bounded identifier")


@dataclass(frozen=True, slots=True, kw_only=True)
class AccessPrincipal:
    project_id: str
    principal_id: str
    principal_type: Literal["user", "service"]
    audience: str
    expires_at: datetime
    actions: frozenset[str]
    enabled: bool

    def __post_init__(self) -> None:
        for name in ("project_id", "principal_id", "audience"):
            _require_identifier(name, getattr(self, name))
        if self.principal_type not in {"user", "service"}:
            raise ValueError("principal_type must be user or service")
        if (
            not isinstance(self.expires_at, datetime)
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must be timezone-aware")
        if (
            not isinstance(self.actions, frozenset)
            or len(self.actions) > 64
            or any(not isinstance(action, str) for action in self.actions)
        ):
            raise TypeError("actions must be a bounded immutable string set")
        for action in self.actions:
            _require_identifier("action", action)
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a boolean")


@dataclass(frozen=True, slots=True, kw_only=True)
class InsightsResource:
    project_id: str
    kind: str
    resource_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier("project_id", self.project_id)
        _require_identifier("kind", self.kind)
        if self.resource_id is not None:
            _require_identifier("resource_id", self.resource_id)


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str


_USER_ACTION_RESOURCE_KINDS = {
    "insights.evidence.read": "evidence",
    "insights.failures.read": "failure",
    "insights.metrics.read": "metric-query",
    "insights.reports.create": "report",
    "insights.reports.read": "report",
    "insights.runs.read": "run",
}


def authorize_insights_request(
    user: AccessPrincipal,
    service: AccessPrincipal,
    action: str,
    resource: InsightsResource,
    *,
    expected_user_audience: str,
    expected_service_audience: str,
    now: datetime,
) -> AccessDecision:
    """Require both the actual user scope and the TAP AI service scope."""
    if user.principal_type != "user":
        return AccessDecision(False, "user-type-mismatch")
    user_denial = _principal_denial(
        user,
        resource,
        expected_audience=expected_user_audience,
        now=now,
        prefix="user",
    )
    if user_denial is not None:
        return user_denial
    if action not in user.actions:
        return AccessDecision(False, "user-action-not-allowed")
    expected_kind = _USER_ACTION_RESOURCE_KINDS.get(action)
    if expected_kind is None:
        return AccessDecision(False, "user-action-not-allowed")
    if resource.kind != expected_kind:
        return AccessDecision(False, "resource-kind-mismatch")

    if service.principal_type != "service":
        return AccessDecision(False, "service-type-mismatch")
    service_denial = _principal_denial(
        service,
        resource,
        expected_audience=expected_service_audience,
        now=now,
        prefix="service",
    )
    if service_denial is not None:
        return service_denial
    if "insights.invoke" not in service.actions:
        return AccessDecision(False, "service-action-not-allowed")
    return AccessDecision(True, "insights-action-allowed")


def _principal_denial(
    principal: AccessPrincipal,
    resource: InsightsResource,
    *,
    expected_audience: str,
    now: datetime,
    prefix: str,
) -> AccessDecision | None:
    if not principal.enabled:
        return AccessDecision(False, f"{prefix}-disabled")
    if principal.expires_at <= now:
        return AccessDecision(False, f"{prefix}-expired")
    if principal.audience != expected_audience:
        return AccessDecision(False, f"{prefix}-audience-mismatch")
    if principal.project_id != resource.project_id:
        return AccessDecision(False, f"{prefix}-scope-mismatch")
    return None
