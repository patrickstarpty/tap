from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Mapping

import pytest

from tap.platform.serialization.dataclass_json import from_json, to_json


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


@dataclass(frozen=True, slots=True)
class Inner:
    label: str
    weight: int


@dataclass(frozen=True, slots=True)
class Outer:
    color: Color
    inners: tuple[Inner, ...]
    counts: Mapping[str, int]
    created_at: datetime
    maybe_inner: Inner | None = None


def test_round_trips_nested_frozen_dataclasses_with_enums_tuples_and_mappings() -> None:
    value = Outer(
        color=Color.BLUE,
        inners=(Inner("a", 1), Inner("b", 2)),
        counts=MappingProxyType({"a": 1, "b": 2}),
        created_at=datetime(2026, 10, 14, 12, 30, tzinfo=UTC),
        maybe_inner=Inner("c", 3),
    )

    restored = from_json(Outer, json.loads(json.dumps(to_json(value))))

    assert restored == value
    assert isinstance(restored.counts, MappingProxyType)

    value_without_inner = Outer(
        color=Color.RED,
        inners=(),
        counts=MappingProxyType({}),
        created_at=datetime(2026, 10, 14, 12, 30, tzinfo=UTC),
        maybe_inner=None,
    )
    restored_without_inner = from_json(Outer, json.loads(json.dumps(to_json(value_without_inner))))
    assert restored_without_inner == value_without_inner
    assert isinstance(restored_without_inner.counts, MappingProxyType)


@dataclass(frozen=True, slots=True)
class A:
    value: str


@dataclass(frozen=True, slots=True)
class B:
    amount: int


@dataclass(frozen=True, slots=True)
class HoldsUnion:
    item: A | B


def test_union_of_dataclasses_uses_type_tag() -> None:
    holds_a = HoldsUnion(item=A(value="x"))
    holds_b = HoldsUnion(item=B(amount=7))

    restored_a = from_json(HoldsUnion, json.loads(json.dumps(to_json(holds_a))))
    restored_b = from_json(HoldsUnion, json.loads(json.dumps(to_json(holds_b))))

    assert restored_a == holds_a
    assert isinstance(restored_a.item, A)
    assert restored_b == holds_b
    assert isinstance(restored_b.item, B)


@dataclass(frozen=True, slots=True)
class WithComputed:
    value: int
    computed: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "computed", self.value * 2)


def test_init_false_field_is_rejected() -> None:
    instance = WithComputed(value=3)

    with pytest.raises(TypeError):
        to_json(instance)


@dataclass(frozen=True, slots=True)
class HoldsLiteral:
    status: Literal["published", "withdrawn"]


@dataclass(frozen=True, slots=True)
class HoldsNumericUnion:
    """Mirrors `InsightsFact.value: float | int | None` (LangGraph PR 5 Task
    8/12) -- two non-dataclass union members have no `__type__` tag to
    disambiguate by, unlike a union of dataclasses."""

    value: float | int | None


def test_numeric_union_round_trips_int_float_and_none() -> None:
    assert from_json(
        HoldsNumericUnion, json.loads(json.dumps(to_json(HoldsNumericUnion(0.9))))
    ) == (HoldsNumericUnion(0.9))
    restored_int = from_json(
        HoldsNumericUnion, json.loads(json.dumps(to_json(HoldsNumericUnion(5))))
    )
    assert restored_int == HoldsNumericUnion(5)
    assert type(restored_int.value) is int
    assert from_json(
        HoldsNumericUnion, json.loads(json.dumps(to_json(HoldsNumericUnion(None))))
    ) == (HoldsNumericUnion(None))


def test_numeric_union_rejects_bool() -> None:
    with pytest.raises(TypeError):
        from_json(HoldsNumericUnion, {"value": True})


def test_literal_round_trips_and_rejects_unknown_values() -> None:
    value = HoldsLiteral(status="withdrawn")

    restored = from_json(HoldsLiteral, json.loads(json.dumps(to_json(value))))

    assert restored == value

    with pytest.raises(ValueError):
        from_json(HoldsLiteral, {"status": "archived"})
