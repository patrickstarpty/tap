"""In-memory registry resolving agent subgraphs by name."""

from __future__ import annotations

from typing import Any

from tap.modules.ai.application.agents.protocol import AgentSubgraph


class AgentRegistry:
    """Registers agent subgraphs once and resolves them by name."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSubgraph[Any, Any]] = {}

    def register(self, agent: AgentSubgraph[Any, Any]) -> None:
        if agent.name in self._agents:
            raise ValueError(f"agent {agent.name!r} is already registered")
        self._agents[agent.name] = agent

    def get(self, name: str) -> AgentSubgraph[Any, Any]:
        return self._agents[name]

    def names(self) -> tuple[str, ...]:
        return tuple(self._agents.keys())


__all__ = ["AgentRegistry"]
