"""Parse inert SKILL.md files into immutable Skill revisions; nothing is executed."""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

import yaml

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.tool_registry import ToolRegistry
from tap.modules.ai.domain.assets import (
    MAX_REQUESTED_TOOLS,
    MAX_SKILL_DESCRIPTION_LENGTH,
    MAX_SKILL_SOURCE_BYTES,
    AssetRevisionRejected,
    AssetRevisionStatus,
    SkillRevision,
)

MAX_SKILL_NAME_LENGTH = 64
_MAX_FRONT_MATTER_DEPTH = 16
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_FRONT_MATTER = re.compile(r"---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)
_BUILTIN_TOOL_IDS = frozenset({"knowledge.search", "knowledge.answer"})


class SkillImportRejected(ValueError):
    """A SKILL.md file violates the inert import contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedSkill:
    name: str
    description: str
    instructions: str
    requested_tools: tuple[str, ...]
    front_matter: Mapping[str, object]
    content_digest: str
    source_markdown: str
    warnings: tuple[str, ...]


class _InertLoader(yaml.SafeLoader):
    """Safe YAML without aliases, so a small file cannot expand into a large value."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise SkillImportRejected("skill-front-matter-invalid")
        event = self.peek_event()
        if getattr(event, "anchor", None) is not None:
            raise SkillImportRejected("skill-front-matter-invalid")
        return super().compose_node(parent, index)


def _plain(value: object, depth: int = 0) -> object:
    if depth > _MAX_FRONT_MATTER_DEPTH:
        raise SkillImportRejected("skill-front-matter-invalid")
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise SkillImportRejected("skill-front-matter-invalid")
        return value
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise SkillImportRejected("skill-front-matter-invalid")
        return {key: _plain(item, depth + 1) for key, item in value.items()}
    raise SkillImportRejected("skill-front-matter-invalid")


def _requested_tools(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: list[object] = [item for item in re.split(r"[,\s]+", value) if item]
    elif isinstance(value, list):
        items = value
    else:
        raise SkillImportRejected("skill-front-matter-invalid")
    tools: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 128:
            raise SkillImportRejected("skill-front-matter-invalid")
        if item.strip() not in tools:
            tools.append(item.strip())
    if len(tools) > MAX_REQUESTED_TOOLS:
        raise SkillImportRejected("skill-front-matter-invalid")
    return tuple(tools)


def _check_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_SKILL_NAME_LENGTH
        or _NAME.fullmatch(value) is None
    ):
        raise SkillImportRejected("skill-name-invalid")
    return value


def _check_description(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_SKILL_DESCRIPTION_LENGTH:
        raise SkillImportRejected("skill-description-invalid")
    return value


def parse_skill_markdown(
    raw: bytes, *, known_tools: Collection[str] = _BUILTIN_TOOL_IDS
) -> ParsedSkill:
    if not isinstance(raw, bytes) or len(raw) > MAX_SKILL_SOURCE_BYTES:
        raise SkillImportRejected("skill-too-large")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillImportRejected("skill-not-utf8") from error
    match = _FRONT_MATTER.fullmatch(source.removeprefix("\ufeff"))
    if match is None:
        raise SkillImportRejected("skill-front-matter-missing")
    header, body = match.group(1) or "", match.group(2)
    try:
        # _InertLoader is a SafeLoader subclass: no Python object construction.
        loaded = yaml.load(header, Loader=_InertLoader)
    except yaml.YAMLError as error:
        raise SkillImportRejected("skill-front-matter-invalid") from error
    if not isinstance(loaded, dict):
        raise SkillImportRejected("skill-front-matter-invalid")
    front_matter = _plain(loaded)
    if not isinstance(front_matter, dict):
        raise SkillImportRejected("skill-front-matter-invalid")
    name = _check_name(front_matter.get("name"))
    description = _check_description(front_matter.get("description"))
    if "metadata" in front_matter and not isinstance(front_matter["metadata"], dict):
        raise SkillImportRejected("skill-front-matter-invalid")
    requested_tools = _requested_tools(front_matter.get("allowed-tools"))
    instructions = body.strip()
    if not instructions:
        raise SkillImportRejected("skill-body-empty")
    return ParsedSkill(
        name=name,
        description=description,
        instructions=instructions,
        requested_tools=requested_tools,
        front_matter=MappingProxyType(front_matter),
        content_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        source_markdown=source,
        warnings=tuple(item for item in requested_tools if item not in known_tools),
    )


def render_skill_markdown(name: str, description: str, instructions: str) -> bytes:
    header = yaml.safe_dump(
        {"name": _check_name(name), "description": _check_description(description)},
        sort_keys=False,
        allow_unicode=True,
        width=1_000_000,
    )
    if not isinstance(instructions, str) or not instructions.strip():
        raise SkillImportRejected("skill-body-empty")
    raw = f"---\n{header}---\n\n{instructions.strip()}\n".encode()
    if len(raw) > MAX_SKILL_SOURCE_BYTES:
        raise SkillImportRejected("skill-too-large")
    return raw


class SkillImportCatalog(Protocol):
    async def import_skill(
        self, scope: ProjectScopeContext, parsed: ParsedSkill, *, origin: str
    ) -> tuple[SkillRevision, bool]: ...

    async def set_skill_status(
        self, scope: ProjectScopeContext, revision_id: str, status: AssetRevisionStatus
    ) -> SkillRevision: ...

    async def skill_source(self, scope: ProjectScopeContext, revision_id: str) -> bytes: ...


class SkillImportService:
    def __init__(self, catalog: SkillImportCatalog, tools: ToolRegistry) -> None:
        self._catalog = catalog
        self._tools = tools

    async def import_markdown(
        self, scope: ProjectScopeContext, raw: bytes
    ) -> tuple[SkillRevision, bool]:
        revision, created, _warnings = await self.import_with_warnings(scope, raw)
        return revision, created

    async def import_with_warnings(
        self, scope: ProjectScopeContext, raw: bytes, *, origin: str = "import"
    ) -> tuple[SkillRevision, bool, tuple[str, ...]]:
        known = {item.tool_id for item in await self._tools.list(scope)}
        parsed = parse_skill_markdown(raw, known_tools=known)
        revision, created = await self._catalog.import_skill(scope, parsed, origin=origin)
        return revision, created, parsed.warnings

    async def create(
        self, scope: ProjectScopeContext, *, name: str, description: str, instructions: str
    ) -> tuple[SkillRevision, bool]:
        revision, created, _warnings = await self.create_with_warnings(
            scope, name=name, description=description, instructions=instructions
        )
        return revision, created

    async def create_with_warnings(
        self, scope: ProjectScopeContext, *, name: str, description: str, instructions: str
    ) -> tuple[SkillRevision, bool, tuple[str, ...]]:
        raw = render_skill_markdown(name, description, instructions)
        return await self.import_with_warnings(scope, raw, origin="editor")

    async def set_status(
        self, scope: ProjectScopeContext, revision_id: str, status: AssetRevisionStatus
    ) -> SkillRevision:
        if not isinstance(status, AssetRevisionStatus):
            raise AssetRevisionRejected()
        return await self._catalog.set_skill_status(scope, revision_id, status)

    async def source(self, scope: ProjectScopeContext, revision_id: str) -> bytes:
        return await self._catalog.skill_source(scope, revision_id)
