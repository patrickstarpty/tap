"""Pure, bounded admission rules for native child runs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DelegationRequest:
    agent_id: str
    objective: str
    input_refs: tuple[str, ...]
    expected_output: str
    depends_on: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.agent_id or not self.objective.strip() or not self.expected_output.strip():
            raise ValueError("delegation requires agent, objective, and expected output")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("delegation dependencies must be unique")


def can_dispatch(dependencies: tuple[str, ...], statuses: dict[str, str], slots: int) -> bool:
    return slots > 0 and all(statuses.get(node_id) == "succeeded" for node_id in dependencies)
