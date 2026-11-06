"""Pure, bounded admission rules for native child runs."""

from __future__ import annotations

import hashlib
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


def child_result_summary(node_id: str, result: str) -> str:
    """Keep parent context bounded while retaining a stable private-result reference."""
    if len(result) <= 400:
        return result
    digest = hashlib.sha256(result.encode()).hexdigest()[:16]
    return f"{result[:400].rstrip()}… [child:{node_id}; sha256:{digest}]"
