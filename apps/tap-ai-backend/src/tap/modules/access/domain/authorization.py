"""Common authorization values, separate from revision-bound retrieval grants."""

from dataclasses import dataclass
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
