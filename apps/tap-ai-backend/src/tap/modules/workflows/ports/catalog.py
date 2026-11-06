"""Frozen workflow agent configuration, separate from live asset records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from tap.modules.access.domain.context import ProjectScopeContext


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    agent_id: str
    revision_id: str
    name: str
    description: str
    instruction: str
    tool_ids: tuple[str, ...]
    model_alias: str | None
    skill_revision_ids: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> AgentSnapshot:
        return cls(
            agent_id=value["agent_id"],
            revision_id=value["revision_id"],
            name=value["name"],
            description=value["description"],
            instruction=value["instruction"],
            tool_ids=tuple(value["tool_ids"]),
            model_alias=value["model_alias"],
            skill_revision_ids=tuple(value["skill_revision_ids"]),
        )


@dataclass(frozen=True, slots=True)
class FrozenAgentCatalog:
    root: AgentSnapshot
    candidates: tuple[AgentSnapshot, ...]
    requested_ids: tuple[str, ...]


class WorkflowAgentCatalog(Protocol):
    async def freeze_agents(
        self, scope: ProjectScopeContext, requested_ids: tuple[str, ...]
    ) -> FrozenAgentCatalog: ...
