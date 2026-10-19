"""Approved, immutable, non-executable AI catalog revisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Mapping

from tap.modules.access.domain.context import ProjectScopeContext, require_identifier
from tap.modules.ai.domain.tools import known_tool_ids

_TASKS = frozenset({"knowledge.answer", "test-plan.generate", "automation.generate"})

AssetSourceKind = Literal["imported", "authored", "built_in"]
SkillFileCategory = Literal["skill", "reference", "script", "asset", "other"]

_SOURCE_KINDS = frozenset({"imported", "authored", "built_in"})
_FILE_CATEGORIES = frozenset({"skill", "reference", "script", "asset", "other"})
_MAX_DESCRIPTION_LENGTH = 1024
_MAX_SYSTEM_INSTRUCTION_BYTES = 32 * 1024


class AssetRevisionStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class AssetRevisionRejected(ValueError):
    """An unapproved, unavailable, or cross-scope revision was requested."""

    def __init__(self) -> None:
        super().__init__("asset-revision-rejected")


class RevisionConflict(Exception):
    """A stale ``base_revision_id`` was supplied for an asset revision insert."""

    def __init__(self) -> None:
        super().__init__("revision-conflict")


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


def _description(value: str | None) -> None:
    if value is not None and (
        not isinstance(value, str) or not value.strip() or len(value) > _MAX_DESCRIPTION_LENGTH
    ):
        raise AssetRevisionRejected()


def _source_kind(value: object) -> None:
    if value not in _SOURCE_KINDS:
        raise AssetRevisionRejected()


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
    system_instruction: str | None = None
    output_schema_json: str | None = None
    description: str | None = None
    source_kind: AssetSourceKind = "built_in"
    skill_revision_ids: tuple[str, ...] = ()
    default_model_alias: str | None = None

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
        if (
            type(self.tool_allowlist) is not frozenset
            or not self.tool_allowlist <= known_tool_ids()
        ):
            raise AssetRevisionRejected()
        if self.adopted_from_revision_id == self.revision_id:
            raise AssetRevisionRejected()
        if self.adopted_from_revision_id is not None:
            _identity(self.adopted_from_revision_id, "adopted_from_revision_id")
        if self.system_instruction is not None:
            if len(self.system_instruction.encode("utf-8")) > _MAX_SYSTEM_INSTRUCTION_BYTES:
                raise AssetRevisionRejected()
            if text_digest(self.system_instruction) != self.system_instruction_digest:
                raise AssetRevisionRejected()
        if self.output_schema_json is not None:
            from tap.modules.ai.domain.models import schema_digest

            try:
                schema = json.loads(self.output_schema_json)
            except (TypeError, json.JSONDecodeError) as error:
                raise AssetRevisionRejected() from error
            if not isinstance(schema, dict) or schema_digest(schema) != self.output_schema_digest:
                raise AssetRevisionRejected()
        _description(self.description)
        _source_kind(self.source_kind)
        if type(self.skill_revision_ids) is not tuple:
            raise AssetRevisionRejected()
        for skill_revision_id in self.skill_revision_ids:
            _identity(skill_revision_id, "skill_revision_ids[]")
        if self.default_model_alias is not None and (
            not isinstance(self.default_model_alias, str)
            or not self.default_model_alias.strip()
            or len(self.default_model_alias) > 128
        ):
            raise AssetRevisionRejected()


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillFile:
    relative_path: str
    size_bytes: int
    sha256: str
    category: SkillFileCategory
    attached: bool

    def __post_init__(self) -> None:
        segments = self.relative_path.split("/") if isinstance(self.relative_path, str) else []
        if (
            not isinstance(self.relative_path, str)
            or not self.relative_path
            or len(self.relative_path) > 512
            or self.relative_path.startswith("/")
            or ".." in segments
            or "" in segments
        ):
            raise AssetRevisionRejected()
        if (
            type(self.size_bytes) is not int
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise AssetRevisionRejected()
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise AssetRevisionRejected()
        if self.category not in _FILE_CATEGORIES:
            raise AssetRevisionRejected()
        if type(self.attached) is not bool:
            raise AssetRevisionRejected()


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
    instruction_template: str | None = None
    description: str | None = None
    source_kind: AssetSourceKind = "built_in"
    attached_references: str | None = None
    frontmatter: Mapping[str, object] | None = None
    package_digest: str | None = None
    files: tuple[SkillFile, ...] = ()

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
        if (
            type(self.applicable_tasks) is not frozenset
            or not self.applicable_tasks
            or not self.applicable_tasks <= _TASKS
        ):
            raise AssetRevisionRejected()
        if self.adopted_from_revision_id == self.revision_id:
            raise AssetRevisionRejected()
        if self.adopted_from_revision_id is not None:
            _identity(self.adopted_from_revision_id, "adopted_from_revision_id")
        if self.instruction_template is not None:
            combined = self.instruction_template + (self.attached_references or "")
            if text_digest(combined) != self.instruction_template_digest:
                raise AssetRevisionRejected()
        _description(self.description)
        _source_kind(self.source_kind)
        if self.attached_references is not None and not isinstance(self.attached_references, str):
            raise AssetRevisionRejected()
        if self.frontmatter is not None and not isinstance(self.frontmatter, Mapping):
            raise AssetRevisionRejected()
        if self.package_digest is not None:
            _digest(self.package_digest)
        if type(self.files) is not tuple or any(type(file) is not SkillFile for file in self.files):
            raise AssetRevisionRejected()
