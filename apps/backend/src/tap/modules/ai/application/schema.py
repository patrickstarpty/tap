"""Bounded closed JSON schema support; domain meaning remains with each consumer."""

from __future__ import annotations

import math


def _json_equal(left: object, right: object) -> bool:
    """JSON numbers compare numerically; booleans are never numbers."""
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(value, right[key]) for key, value in left.items()
        )
    return left == right


def check_schema(schema: object, depth: int = 0) -> None:
    if depth > 16 or not isinstance(schema, dict):
        raise ValueError("invalid locked schema")
    kind = schema.get("type")
    if kind not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ValueError("unsupported locked schema type")
    allowed = {"type", "description", "title", "enum"}
    if kind == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        properties = schema.get("properties")
        required = schema.get("required")
        if (
            not isinstance(properties, dict)
            or len(properties) > 128
            or not isinstance(required, list)
            or schema.get("additionalProperties") is not False
        ):
            raise ValueError("object schemas must be closed")
        if any(not isinstance(key, str) for key in required) or set(required) != set(properties):
            raise ValueError("object schemas must require every property")
        for child in properties.values():
            check_schema(child, depth + 1)
    elif kind == "array":
        allowed |= {"items"}
        check_schema(schema.get("items"), depth + 1)
    if set(schema) - allowed:
        raise ValueError("unsupported locked schema keyword")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("invalid locked schema enum")


def validate_output(schema: dict[str, object], value: object) -> None:
    kind = schema["type"]
    if kind == "object":
        properties = schema["properties"]
        assert isinstance(properties, dict)
        if not isinstance(value, dict) or set(value) != set(properties):
            raise ValueError("structured output does not match locked properties")
        for key, child in properties.items():
            validate_output(child, value[key])
    elif kind == "array":
        items = schema["items"]
        assert isinstance(items, dict)
        if not isinstance(value, list) or len(value) > 10000:
            raise ValueError("structured output is not a bounded array")
        for item in value:
            validate_output(items, item)
    elif not (
        (kind == "string" and type(value) is str)
        or (kind == "integer" and type(value) is int)
        or (
            kind == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
        or (kind == "boolean" and type(value) is bool)
        or (kind == "null" and value is None)
    ):
        raise ValueError("structured output does not match locked type")
    if "enum" in schema:
        choices = schema["enum"]
        assert isinstance(choices, list)
        if not any(_json_equal(value, choice) for choice in choices):
            raise ValueError("structured output does not match locked enum")
