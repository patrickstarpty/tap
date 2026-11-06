"""Input models for a frozen Copilot probe snapshot."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ProbeAgent:
    agent_id: str
    revision_id: str
    description: str
    instruction: str
    tools: tuple[str, ...]
    enabled: bool


@dataclass(frozen=True)
class CapabilityResult:
    name: str
    status: Literal["supported", "unsupported", "unverified"]
    evidence_refs: tuple[str, ...]
    limitation: str | None
