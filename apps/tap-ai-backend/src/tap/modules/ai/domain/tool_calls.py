"""Admission of model-requested tool calls into a bounded, authorized set.

Pure domain logic: given what the model asked to call this round and what is
actually visible/authorized for this turn, decide which calls are admitted
for execution and which are rejected (and why), without executing anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from tap.modules.ai.domain.tools import tool_for_wire_name

MAX_TOOL_CALLS_PER_ROUND = 3
# Final review M-8: rejected calls never count toward the 3-call execution cap
# above, so a model could otherwise request an unbounded number of (invalid)
# calls per round, each costing two `task.step` events. Every requested call
# past this total is rejected as `too-many-calls` whatever else is wrong with
# it, and `ChatTurnNodes.tools` emits `task.step` events only for the first
# `MAX_REQUESTED_TOOL_CALLS_PER_ROUND` calls -- the rest are still answered in
# the transcript (so every model call id gets its tool reply) but never run
# and never surface as steps.
MAX_REQUESTED_TOOL_CALLS_PER_ROUND = 10

RejectionReason = Literal[
    "unknown-tool",
    "tool-not-visible",
    "invalid-arguments",
    "source-out-of-scope",
    "too-many-calls",
    "duplicate-call-id",
]


@dataclass(frozen=True, slots=True)
class RequestedToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class AdmittedToolCall:
    call_id: str
    tool_id: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RejectedToolCall:
    call_id: str
    name: str
    reason: RejectionReason


def step_id(turn_id: str, round_number: int, index: int) -> str:
    return f"{turn_id}:r{round_number}:c{index}"


def _matches_schema(schema: Mapping[str, object], value: object) -> bool:
    """Minimal closed-schema check for the bounded tool input shapes this
    module admits. Supports only the keywords those schemas use: object with
    ``properties``/``required``/``additionalProperties: False``, array with
    ``items``/``maxItems``, string with ``minLength``/``maxLength``/``enum``.
    """

    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(value, dict):
            return False
        assert isinstance(properties, Mapping)
        assert isinstance(required, (list, tuple))
        if not set(required) <= set(value):
            return False
        if schema.get("additionalProperties") is False and not set(value) <= set(properties):
            return False
        for key, child_value in value.items():
            child_schema = properties.get(key)
            if not isinstance(child_schema, Mapping):
                return False
            if not _matches_schema(child_schema, child_value):
                return False
        return True
    if kind == "array":
        if not isinstance(value, list):
            return False
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        items_schema = schema.get("items")
        if isinstance(items_schema, Mapping):
            for item in value:
                if not _matches_schema(items_schema, item):
                    return False
        return True
    if kind == "string":
        if type(value) is not str:
            return False
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        maximum = schema.get("maxLength")
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            return False
        return True
    return False


def _source_ids_in_scope(
    arguments: Mapping[str, object], selected_source_ids: frozenset[str]
) -> bool:
    source_ids = arguments.get("sourceIds")
    if source_ids is None:
        return True
    if not isinstance(source_ids, list):
        return False
    return set(source_ids) <= selected_source_ids


def admit_tool_calls(
    calls: Sequence[RequestedToolCall],
    *,
    visible: tuple[str, ...],
    selected_source_ids: frozenset[str],
) -> tuple[AdmittedToolCall | RejectedToolCall, ...]:
    results: list[AdmittedToolCall | RejectedToolCall] = []
    admitted_count = 0
    seen_call_ids: set[str] = set()
    for position, call in enumerate(calls):
        if position >= MAX_REQUESTED_TOOL_CALLS_PER_ROUND:
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="too-many-calls")
            )
            continue
        # Final review M-9: a repeated call id within one round is ambiguous
        # (its tool reply could not be paired with one call) -- rejected,
        # never executed.
        if call.call_id in seen_call_ids:
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="duplicate-call-id")
            )
            continue
        seen_call_ids.add(call.call_id)
        if admitted_count >= MAX_TOOL_CALLS_PER_ROUND:
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="too-many-calls")
            )
            continue
        definition = tool_for_wire_name(call.name)
        if definition is None:
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="unknown-tool")
            )
            continue
        if definition.tool_id not in visible:
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="tool-not-visible")
            )
            continue
        try:
            arguments = json.loads(call.arguments_json)
        except (TypeError, ValueError):
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="invalid-arguments")
            )
            continue
        if not _matches_schema(definition.input_schema, arguments):
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="invalid-arguments")
            )
            continue
        assert isinstance(arguments, dict)
        if not _source_ids_in_scope(arguments, selected_source_ids):
            results.append(
                RejectedToolCall(call_id=call.call_id, name=call.name, reason="source-out-of-scope")
            )
            continue
        results.append(
            AdmittedToolCall(call_id=call.call_id, tool_id=definition.tool_id, arguments=arguments)
        )
        admitted_count += 1
    return tuple(results)
