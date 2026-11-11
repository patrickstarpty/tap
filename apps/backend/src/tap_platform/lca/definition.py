"""Strict, bounded canonical debug-flow data. Validation never executes actions."""

import json
import math
from collections.abc import Mapping
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "lca-debug-v1"
MAX_STEPS = 100
ARIA_ROLES = frozenset(
    "alert alertdialog application article banner blockquote button caption cell checkbox code columnheader combobox complementary contentinfo definition deletion dialog directory document emphasis feed figure form generic grid gridcell group heading img insertion link list listbox listitem log main marquee math meter menu menubar menuitem menuitemcheckbox menuitemradio navigation none note option paragraph presentation progressbar radio radiogroup region row rowgroup rowheader scrollbar search searchbox separator slider spinbutton status strong subscript superscript switch tab table tablist tabpanel term textbox time timer toolbar tooltip tree treegrid treeitem".split()
)
Scalar = str | int | float | bool
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Text = Annotated[str, Field(max_length=8192)]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ParameterReference(StrictModel):
    parameter: Identifier


class Target(StrictModel):
    id: Identifier
    kind: Literal["test_id", "role", "css"]
    value: Annotated[str, Field(min_length=1, max_length=1024)]
    name: Text | None = None
    review_required: bool = False

    @model_validator(mode="after")
    def locator_shape(self) -> "Target":
        if self.kind == "role":
            if self.name is None or self.value not in ARIA_ROLES:
                raise ValueError("Role targets require a role and accessible name")
        elif self.name is not None:
            raise ValueError("Accessible name applies only to role targets")
        if self.kind == "css" and (not self.review_required or ">>" in self.value):
            raise ValueError("CSS fallback must be explicitly marked for review")
        return self


class Parameter(StrictModel):
    name: Identifier
    type: Literal["string", "integer", "number", "boolean"]
    required: bool
    default: Scalar | None = None

    @model_validator(mode="after")
    def default_type(self) -> "Parameter":
        if "default" in self.model_fields_set:
            check_scalar(self.type, self.default)
        return self


class Step(StrictModel):
    id: Identifier
    operation: Literal[
        "navigate", "click", "fill", "select", "assert_text", "assert_url"
    ]
    arguments: dict[str, Text | ParameterReference]
    target_id: Identifier | None = None
    timeout_ms: Annotated[int, Field(gt=0, le=30000)]
    provenance: dict[str, Annotated[str, Field(max_length=1024)]] = Field(
        default_factory=dict
    )


class DebugDefinition(StrictModel):
    schema_version: Literal["lca-debug-v1"]
    allowed_origins: Annotated[list[str], Field(min_length=1, max_length=8)]
    targets: Annotated[list[Target], Field(max_length=100)]
    parameters: Annotated[list[Parameter], Field(max_length=100)]
    steps: Annotated[list[Step], Field(min_length=1, max_length=MAX_STEPS)]

    @model_validator(mode="after")
    def references(self) -> "DebugDefinition":
        for ids in (
            [t.id for t in self.targets],
            [p.name for p in self.parameters],
            [s.id for s in self.steps],
            self.allowed_origins,
        ):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate canonical identifier or origin")
        for origin in self.allowed_origins:
            if normalized_origin(origin) != origin:
                raise ValueError(
                    "Allowed origins must be canonical origins without paths"
                )
        targets = {t.id for t in self.targets}
        parameters = {p.name: p for p in self.parameters}
        expected = {
            "navigate": "url",
            "fill": "value",
            "select": "value",
            "assert_text": "text",
            "assert_url": "url",
        }
        for step in self.steps:
            key = expected.get(step.operation)
            if set(step.arguments) != ({key} if key else set()):
                raise ValueError("Operation arguments do not match its schema")
            needs_target = step.operation not in {"navigate", "assert_url"}
            if needs_target and step.target_id not in targets:
                raise ValueError("Operation requires an existing target")
            if not needs_target and step.target_id is not None:
                raise ValueError("Page operation cannot have a target")
            if len(step.provenance) > 16:
                raise ValueError("Too many provenance fields")
            for argument in step.arguments.values():
                if isinstance(argument, ParameterReference):
                    parameter = parameters.get(argument.parameter)
                    if parameter is None or parameter.type != "string":
                        raise ValueError(
                            "Operation requires a declared string parameter"
                        )
                elif key == "url":
                    check_url(argument, self.allowed_origins)
        return self


def normalized_origin(value: str) -> str:
    if len(value) > 8192 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("Invalid URL")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("Only credential-free HTTP(S) URLs are supported")
    host = parts.hostname
    if not host.isascii() or "%" in host:
        raise ValueError("Use an ASCII canonical host")
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    suffix = (
        f":{port}"
        if port is not None
        and (parts.scheme, port) not in {("http", 80), ("https", 443)}
        else ""
    )
    return f"{parts.scheme}://{host}{suffix}"


def check_url(value: str, origins: list[str]) -> None:
    if normalized_origin(value) not in origins:
        raise ValueError("URL is outside allowed origins")


def check_scalar(kind: str, value: object) -> None:
    accepted = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
    }
    if type(value) not in accepted[kind]:
        raise ValueError("Parameter value has the wrong JSON type")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Parameter numbers must be finite")
    if isinstance(value, str) and len(value) > 8192:
        raise ValueError("Parameter string exceeds limit")


def validate_definition(value: object) -> DebugDefinition:
    if isinstance(value, DebugDefinition):
        value = value.model_dump(exclude_unset=True)
    return DebugDefinition.model_validate(value)


def canonical_bytes(definition: DebugDefinition) -> bytes:
    checked = validate_definition(definition)
    return json.dumps(
        checked.model_dump(exclude_unset=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


def resolve_parameters(
    definition: DebugDefinition, supplied: Mapping[str, object] | None = None
) -> dict[str, Scalar]:
    definition = validate_definition(definition)
    inputs = dict(supplied or {})
    declarations = {p.name: p for p in definition.parameters}
    if inputs.keys() - declarations.keys():
        raise ValueError("Undeclared parameter input")
    result: dict[str, Scalar] = {}
    for name, parameter in declarations.items():
        if name in inputs:
            value = inputs[name]
        elif "default" in parameter.model_fields_set:
            value = parameter.default
        elif parameter.required:
            raise ValueError(f"Missing required parameter: {name}")
        else:
            continue
        check_scalar(parameter.type, value)
        assert isinstance(value, (str, int, float, bool))
        result[name] = value
    for step in definition.steps:
        for key, argument in step.arguments.items():
            if isinstance(argument, ParameterReference):
                if argument.parameter not in result:
                    raise ValueError(
                        f"Missing referenced parameter: {argument.parameter}"
                    )
                value = result[argument.parameter]
            else:
                value = argument
            if key == "url":
                assert isinstance(value, str)
                check_url(value, definition.allowed_origins)
    return result
