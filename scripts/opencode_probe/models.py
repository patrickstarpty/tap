"""Frozen inputs for OpenCode capability probes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeAgent:
    agent_id: str
    revision_id: str
    description: str
    instruction: str
    tools: tuple[str, ...]
    enabled: bool


# Provider setup is task-local and separate from the profile exporter. This
# alias is the intended OpenCode model name, not a credential or a live call.
PROBE_MODEL = "litellm/qwen-plus"
