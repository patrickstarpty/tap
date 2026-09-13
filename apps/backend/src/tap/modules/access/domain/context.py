"""Immutable identity contexts produced at the trusted server boundary."""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


def require_identifier(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None
    ):
        raise ValueError(f"{name} must be a bounded identifier")


class IdentityMode(StrEnum):
    VALIDATION = "validation"
    PRODUCT = "product"


@dataclass(frozen=True, slots=True, kw_only=True)
class AnonymousContext:
    enterprise_id: str
    scope_kind: Literal["ANONYMOUS"] = field(default="ANONYMOUS", init=False)

    def __post_init__(self) -> None:
        require_identifier("enterprise_id", self.enterprise_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlatformScopeContext:
    enterprise_id: str
    actor_id: str
    identity_mode: IdentityMode
    scope_kind: Literal["PLATFORM"] = field(default="PLATFORM", init=False)

    def __post_init__(self) -> None:
        require_identifier("enterprise_id", self.enterprise_id)
        require_identifier("actor_id", self.actor_id)
        if not isinstance(self.identity_mode, IdentityMode):
            raise TypeError("identity_mode must be an IdentityMode")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectScopeContext:
    enterprise_id: str
    project_id: str
    actor_id: str
    identity_mode: IdentityMode
    scope_kind: Literal["PROJECT"] = field(default="PROJECT", init=False)

    def __post_init__(self) -> None:
        require_identifier("enterprise_id", self.enterprise_id)
        require_identifier("project_id", self.project_id)
        require_identifier("actor_id", self.actor_id)
        if not isinstance(self.identity_mode, IdentityMode):
            raise TypeError("identity_mode must be an IdentityMode")


IdentityContext = AnonymousContext | PlatformScopeContext | ProjectScopeContext
