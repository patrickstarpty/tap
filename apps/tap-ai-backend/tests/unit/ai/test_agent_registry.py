from __future__ import annotations

import dataclasses

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.agents import AgentContext, AgentRegistry


class _EchoAgent:
    name = "echo"
    input_schema = str
    output_schema = str

    async def run(self, context: AgentContext, value: str) -> str:
        return value


class _OtherAgent:
    name = "echo"
    input_schema = str
    output_schema = str

    async def run(self, context: AgentContext, value: str) -> str:
        return value


def test_registry_registers_once_and_resolves_by_name() -> None:
    registry = AgentRegistry()
    first = _EchoAgent()
    second = _OtherAgent()

    registry.register(first)

    with pytest.raises(ValueError):
        registry.register(second)

    assert registry.get("echo") is first

    with pytest.raises(KeyError):
        registry.get("missing")

    assert registry.names() == ("echo",)


def test_registry_preserves_registration_order_in_names() -> None:
    registry = AgentRegistry()
    first = _EchoAgent()
    other = _OtherAgent()
    other.name = "other"

    registry.register(first)
    registry.register(other)

    assert registry.names() == ("echo", "other")


def test_agent_context_is_frozen_and_scopes_sources() -> None:
    context = AgentContext(
        scope=VALIDATION_SCOPE,
        source_revision_ids=frozenset({"r1"}),
        graph_version=None,
    )

    assert context.source_revision_ids == frozenset({"r1"})
    assert context.graph_version is None
    assert context.turn_id is None
    assert context.parent_context is None

    with pytest.raises(dataclasses.FrozenInstanceError):
        context.graph_version = "g1"  # type: ignore[misc]
