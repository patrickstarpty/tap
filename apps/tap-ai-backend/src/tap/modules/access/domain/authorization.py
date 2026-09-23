"""Common authorization values, separate from revision-bound retrieval grants."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tap.modules.access.domain.context import require_identifier


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceRef:
    enterprise_id: str
    project_id: str
    kind: str
    resource_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("enterprise_id", "project_id", "kind"):
            require_identifier(name, getattr(self, name))
        if self.resource_id is not None:
            require_identifier("resource_id", self.resource_id)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a boolean")
        require_identifier("reason", self.reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class ActorPrincipal:
    enterprise_id: str
    actor_id: str
    principal_type: Literal["VALIDATION", "USER"]
    enabled: bool

    def __post_init__(self) -> None:
        require_identifier("enterprise_id", self.enterprise_id)
        require_identifier("actor_id", self.actor_id)
        if self.principal_type not in {"VALIDATION", "USER"} or type(self.enabled) is not bool:
            raise ValueError("invalid principal state")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectPrincipal:
    enterprise_id: str
    project_id: str
    actor_id: str
    audience: str
    expires_at: datetime
    actions: frozenset[str]
    enabled: bool

    def __post_init__(self) -> None:
        for name in ("enterprise_id", "project_id", "actor_id", "audience"):
            require_identifier(name, getattr(self, name))
        if not isinstance(self.expires_at, datetime) or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if (
            not isinstance(self.actions, frozenset)
            or len(self.actions) > 64
            or any(not isinstance(action, str) for action in self.actions)
        ):
            raise TypeError("actions must be a bounded immutable string set")
        for action in self.actions:
            require_identifier("action", action)
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a boolean")
