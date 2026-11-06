"""Input models for a frozen Copilot probe snapshot."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeAgent:
    agent_id: str
    revision_id: str
    description: str
    instruction: str
    tools: tuple[str, ...]
    enabled: bool
