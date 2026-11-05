"""Generic JSON codec for frozen, slotted dataclasses.

`to_json`/`from_json` round-trip the closed value shapes used across module
application boundaries (Knowledge, Graph, ...): `None`, `bool`/`int`/`float`/`str`,
`Enum` (by value), `Literal[...]` (plain value, membership-checked on decode),
`datetime` (ISO 8601), `tuple[X, ...]` (homogeneous or fixed-length), `list[X]`,
`dict[str, X]`, `Mapping[str, X]` (decoded as `MappingProxyType`), `X | None`, a
union of dataclasses (tagged by `__type__` on decode), and nested dataclasses.
`Any`/`object`-typed fields are decoded as plain JSON (dict/list/primitive)
with no further interpretation.

A dataclass is encoded as a JSON object carrying `"__type__": <class name>`
alongside its fields. A field declared with `init=False` is rejected with
`TypeError` on both encode and decode -- such fields are computed in
`__post_init__` and are not part of the value's reconstructable identity.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping as MappingABC
from datetime import datetime
from enum import Enum
from types import MappingProxyType, UnionType
from typing import Any, Literal, TypeVar, Union, get_args, get_origin, get_type_hints

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

T = TypeVar("T")

_TYPE_TAG = "__type__"


def to_json(value: object) -> JsonValue:
    """Encode `value` into a plain JSON-compatible structure."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _encode_dataclass(value)
    if isinstance(value, MappingABC):
        return {_encode_key(key): to_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json(item) for item in value]
    raise TypeError(f"unsupported type for JSON encoding: {type(value)!r}")


def from_json(tp: type[T], data: object) -> T:
    """Decode `data` into an instance of `tp`."""
    return _decode(tp, data)


def _encode_dataclass(value: Any) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {_TYPE_TAG: type(value).__name__}
    for item in dataclasses.fields(value):
        if not item.init:
            raise TypeError(
                f"{type(value).__name__}.{item.name} is init=False and cannot be JSON-encoded"
            )
        result[item.name] = to_json(getattr(value, item.name))
    return result


def _encode_key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError(f"unsupported mapping key type for JSON encoding: {type(key)!r}")
    return key


def _decode(tp: Any, data: object) -> Any:
    origin = get_origin(tp)

    if tp is type(None):
        if data is not None:
            raise TypeError("expected null for NoneType")
        return None

    if origin is Union or origin is UnionType:
        return _decode_union(tp, data)

    if origin is Literal:
        return _decode_literal(tp, data)

    if tp is Any or tp is object:
        return _decode_generic(data)

    if tp is bool:
        if not isinstance(data, bool):
            raise TypeError("expected a bool")
        return data

    if tp is int:
        if isinstance(data, bool) or not isinstance(data, int):
            raise TypeError("expected an int")
        return data

    if tp is float:
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            raise TypeError("expected a float")
        return float(data)

    if tp is str:
        if not isinstance(data, str):
            raise TypeError("expected a str")
        return data

    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(data)

    if tp is datetime:
        if not isinstance(data, str):
            raise TypeError("expected an ISO 8601 datetime string")
        return datetime.fromisoformat(data)

    if origin is tuple:
        return _decode_tuple(tp, data)

    if origin is list:
        return _decode_list(tp, data)

    if origin is dict:
        return _decode_dict(tp, data)

    if origin is not None and isinstance(origin, type) and issubclass(origin, MappingABC):
        return _decode_mapping(tp, data)

    if dataclasses.is_dataclass(tp) and isinstance(tp, type):
        return _decode_dataclass(tp, data)

    raise TypeError(f"unsupported type for JSON decoding: {tp!r}")


def _decode_union(tp: Any, data: object) -> Any:
    args = get_args(tp)
    non_none = [arg for arg in args if arg is not type(None)]
    if data is None:
        if type(None) in args:
            return None
        raise TypeError(f"unexpected null for non-optional union {tp!r}")
    if len(non_none) == 1:
        return _decode(non_none[0], data)
    # A plain numeric union (`float | int | None`, e.g. `InsightsFact.value`,
    # PR 5 Task 8/12): JSON has one number type, so `int` and `float` are
    # never ambiguous by a `__type__` tag the way two dataclass members
    # would be -- decode by the value's own runtime shape, matching how
    # `to_json` already encodes `int`/`float` as themselves with no tag.
    if non_none and all(candidate in (int, float) for candidate in non_none):
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            raise TypeError(f"expected a numeric value for union {tp!r}")
        if int in non_none and (float not in non_none or isinstance(data, int)):
            return int(data)
        return float(data)
    if isinstance(data, MappingABC) and _TYPE_TAG in data:
        tag = data[_TYPE_TAG]
        for candidate in non_none:
            if (
                isinstance(candidate, type)
                and dataclasses.is_dataclass(candidate)
                and candidate.__name__ == tag
            ):
                return _decode(candidate, data)
        raise TypeError(f"no member of union {tp!r} matches type tag {tag!r}")
    raise TypeError(f"ambiguous union {tp!r} without a {_TYPE_TAG} tag")


def _decode_literal(tp: Any, data: object) -> Any:
    allowed = get_args(tp)
    if data not in allowed:
        raise ValueError(f"expected one of {allowed!r} for {tp!r}, got {data!r}")
    return data


def _decode_tuple(tp: Any, data: object) -> tuple[Any, ...]:
    if not isinstance(data, list):
        raise TypeError("expected a JSON array for a tuple field")
    args = get_args(tp)
    if len(args) == 2 and args[1] is Ellipsis:
        return tuple(_decode(args[0], item) for item in data)
    if len(args) != len(data):
        raise TypeError(f"expected {len(args)} tuple elements, got {len(data)}")
    return tuple(_decode(item_type, item) for item_type, item in zip(args, data, strict=True))


def _decode_list(tp: Any, data: object) -> list[Any]:
    if not isinstance(data, list):
        raise TypeError("expected a JSON array for a list field")
    (item_type,) = get_args(tp)
    return [_decode(item_type, item) for item in data]


def _decode_dict(tp: Any, data: object) -> dict[str, Any]:
    if not isinstance(data, MappingABC):
        raise TypeError("expected a JSON object for a dict field")
    key_type, value_type = get_args(tp)
    if key_type is not str:
        raise TypeError("only str-keyed dict fields are supported")
    return {key: _decode(value_type, item) for key, item in data.items()}


def _decode_mapping(tp: Any, data: object) -> MappingProxyType[str, Any]:
    if not isinstance(data, MappingABC):
        raise TypeError("expected a JSON object for a Mapping field")
    key_type, value_type = get_args(tp)
    if key_type is not str:
        raise TypeError("only str-keyed Mapping fields are supported")
    return MappingProxyType({key: _decode(value_type, item) for key, item in data.items()})


def _decode_dataclass(tp: type, data: object) -> Any:
    if not isinstance(data, MappingABC):
        raise TypeError(f"expected a JSON object for {tp.__name__}")
    hints = get_type_hints(tp)
    kwargs: dict[str, Any] = {}
    for item in dataclasses.fields(tp):
        if not item.init:
            raise TypeError(f"{tp.__name__}.{item.name} is init=False and cannot be JSON-decoded")
        if item.name not in data:
            continue
        kwargs[item.name] = _decode(hints[item.name], data[item.name])
    return tp(**kwargs)


def _decode_generic(data: object) -> Any:
    if isinstance(data, MappingABC):
        return {key: _decode_generic(item) for key, item in data.items()}
    if isinstance(data, list):
        return [_decode_generic(item) for item in data]
    return data
