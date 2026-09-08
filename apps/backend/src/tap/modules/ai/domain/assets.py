"""Approved, immutable, non-executable AI catalog revisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from tap.modules.access.domain.context import ProjectScopeContext, require_identifier

_DOMAIN_TOOLS = frozenset({"knowledge.search", "knowledge.answer"})
_TASKS = frozenset({"knowledge.answer", "test-plan.generate", "automation.generate"})


class AssetRevisionStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class AssetRevisionRejected(ValueError):
    """An unapproved, unavailable, or cross-scope revision was requested."""

    def __init__(self) -> None:
        super().__init__("asset-revision-rejected")


def text_digest(value: str) -> str:
    if not isinstance(value, str):
        raise AssetRevisionRejected()
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: str) -> None:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise AssetRevisionRejected()
    if any(character not in "0123456789abcdef" for character in value[7:]):
        raise AssetRevisionRejected()


def _identity(value: str, name: str) -> None:
    try:
        require_identifier(name, value)
    except (TypeError, ValueError) as error:
        raise AssetRevisionRejected() from error


@dataclass(frozen=True, slots=True, kw_only=True)
class AiAgentRevision:
    revision_id: str
    asset_id: str
    display_name: str
    scope: ProjectScopeContext
    content_digest: str
    system_instruction_digest: str
    tool_allowlist: frozenset[str]
    output_schema_digest: str
    status: AssetRevisionStatus = AssetRevisionStatus.ENABLED
    adopted_from_revision_id: str | None = None

    def __post_init__(self) -> None:
        _identity(self.revision_id, "revision_id")
        _identity(self.asset_id, "asset_id")
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 128
        ):
            raise AssetRevisionRejected()
        if type(self.scope) is not ProjectScopeContext or not isinstance(
            self.status, AssetRevisionStatus
        ):
            raise AssetRevisionRejected()
        for value in (
            self.content_digest,
            self.system_instruction_digest,
            self.output_schema_digest,
        ):
            _digest(value)
        if not self.tool_allowlist <= _DOMAIN_TOOLS:
            raise AssetRevisionRejected()
        if self.adopted_from_revision_id is not None:
            _identity(self.adopted_from_revision_id, "adopted_from_revision_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillRevision:
    revision_id: str
    asset_id: str
    display_name: str
    scope: ProjectScopeContext
    content_digest: str
    instruction_template_digest: str
    applicable_tasks: frozenset[str]
    status: AssetRevisionStatus = AssetRevisionStatus.ENABLED
    adopted_from_revision_id: str | None = None

    def __post_init__(self) -> None:
        _identity(self.revision_id, "revision_id")
        _identity(self.asset_id, "asset_id")
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 128
        ):
            raise AssetRevisionRejected()
        if type(self.scope) is not ProjectScopeContext or not isinstance(
            self.status, AssetRevisionStatus
        ):
            raise AssetRevisionRejected()
        _digest(self.content_digest)
        _digest(self.instruction_template_digest)
        if not self.applicable_tasks or not self.applicable_tasks <= _TASKS:
            raise AssetRevisionRejected()
        if self.adopted_from_revision_id is not None:
            _identity(self.adopted_from_revision_id, "adopted_from_revision_id")
