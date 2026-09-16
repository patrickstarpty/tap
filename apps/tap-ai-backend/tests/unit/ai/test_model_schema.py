from __future__ import annotations

import pytest

from tap.modules.ai.application.schema import check_schema, validate_output


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "boolean", "enum": [1]}, True),
        ({"type": "boolean", "enum": [0]}, False),
        ({"type": "number", "enum": [True]}, 1),
        ({"type": "array", "items": {"type": "boolean"}, "enum": [[1]]}, [True]),
        ({"type": "array", "items": {"type": "number"}, "enum": [[False]]}, [0]),
        (
            {
                "type": "object",
                "properties": {"nested": {"type": "array", "items": {"type": "boolean"}}},
                "required": ["nested"],
                "additionalProperties": False,
                "enum": [{"nested": [1]}],
            },
            {"nested": [True]},
        ),
    ],
)
def test_locked_enum_equality_distinguishes_booleans_from_numbers_recursively(schema, value):
    check_schema(schema)
    with pytest.raises(ValueError, match="enum"):
        validate_output(schema, value)


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "boolean", "enum": [True]}, True),
        ({"type": "boolean", "enum": [False]}, False),
        ({"type": "number", "enum": [1]}, 1.0),
        ({"type": "number", "enum": [1.0]}, 1),
        ({"type": "array", "items": {"type": "boolean"}, "enum": [[True]]}, [True]),
        (
            {
                "type": "object",
                "properties": {"nested": {"type": "array", "items": {"type": "boolean"}}},
                "required": ["nested"],
                "additionalProperties": False,
                "enum": [{"nested": [True]}],
            },
            {"nested": [True]},
        ),
    ],
)
def test_locked_enum_preserves_json_numeric_and_nested_equality(schema, value):
    check_schema(schema)
    validate_output(schema, value)


def test_unsupported_const_cannot_silently_bypass_the_locked_schema():
    with pytest.raises(ValueError, match="keyword"):
        check_schema({"type": "boolean", "const": 1})
