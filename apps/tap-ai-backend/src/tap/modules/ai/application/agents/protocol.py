"""Contract shared by every agent subgraph in the interaction graph.

An ``AgentSubgraph`` is a self-contained unit of work (e.g. seeding,
expansion, ranking) that the interaction graph invokes by name through the
``AgentRegistry``. Two invariants hold for every implementation:

- ``run`` never raises: failures are reported through the returned
  ``OutputT`` carrying its own status/diagnostics, so callers can always
  await a result without wrapping every call in a try/except.
- Subgraphs never call one another directly. Composition only happens at
  the interaction graph layer; a subgraph that needs another subgraph's
  output receives it as part of its own input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from opentelemetry import context as otel_context

from tap.modules.access.domain.context import ProjectScopeContext

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Immutable per-invocation context passed to every agent subgraph."""

    scope: ProjectScopeContext
    source_revision_ids: frozenset[str]
    graph_version: str | None
    turn_id: str | None = None
    parent_context: otel_context.Context | None = field(default=None)


class AgentSubgraph(Protocol[InputT, OutputT]):
    """Protocol implemented by every registered agent subgraph."""

    name: str
    input_schema: type[InputT]
    output_schema: type[OutputT]

    async def run(self, context: AgentContext, value: InputT) -> OutputT: ...


__all__ = ["AgentContext", "AgentSubgraph", "InputT", "OutputT"]
