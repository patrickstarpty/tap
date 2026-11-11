"""Strict, bounded canonical debug-flow data. Validation never executes actions."""

import json
import math
import re
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
    frame_target_id: Identifier | None = None

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
        "navigate",
        "click",
        "fill",
        "select",
        "assert_text",
        "assert_text_exact",
        "assert_url",
        "scroll",
        "scroll_into_view",
        "press",
        "hover",
        "double_click",
        "right_click",
        "check",
        "uncheck",
        "drag_drop",
        "wait_for",
        "assert_visible",
        "assert_value",
        "assert_checked",
        "assert_read",
        "open_page",
        "switch_page",
        "close_page",
        "click_popup",
        "click_dialog",
        "upload_file",
        "download_file",
    ]
    arguments: dict[str, Text | int | float | bool | ParameterReference]
    target_id: Identifier | None = None
    page_id: Identifier | None = None
    timeout_ms: Annotated[int, Field(gt=0, le=30000)]
    provenance: dict[str, Annotated[str, Field(max_length=1024)]] = Field(
        default_factory=dict
    )


class BddStep(StrictModel):
    """Authoring metadata; the flat steps remain the execution source of truth."""

    id: Identifier
    keyword: Literal["Given", "When", "Then", "And", "But"]
    description: Annotated[str, Field(min_length=1, max_length=1024)]
    action_ids: Annotated[list[Identifier], Field(max_length=MAX_STEPS)]

    @model_validator(mode="after")
    def meaningful_description(self) -> "BddStep":
        if not self.description.strip():
            raise ValueError("BDD description must contain non-whitespace text")
        return self


class DebugDefinition(StrictModel):
    schema_version: Literal["lca-debug-v1"]
    allowed_origins: Annotated[list[str], Field(min_length=1, max_length=8)]
    targets: Annotated[list[Target], Field(max_length=100)]
    parameters: Annotated[list[Parameter], Field(max_length=100)]
    steps: Annotated[list[Step], Field(min_length=1, max_length=MAX_STEPS)]
    # Omission preserves legacy canonical bytes; explicit null/empty is invalid.
    bdd_steps: Annotated[list[BddStep], Field(min_length=1, max_length=MAX_STEPS)] = (
        Field(default_factory=list)
    )

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
        if "bdd_steps" in self.model_fields_set:
            group_ids = [group.id for group in self.bdd_steps]
            if len(group_ids) != len(set(group_ids)):
                raise ValueError("Duplicate BDD group identifier")
            action_ids = [
                action_id for group in self.bdd_steps for action_id in group.action_ids
            ]
            if action_ids != [step.id for step in self.steps]:
                raise ValueError("BDD action IDs must partition all steps in order")
        for origin in self.allowed_origins:
            if normalized_origin(origin) != origin:
                raise ValueError(
                    "Allowed origins must be canonical origins without paths"
                )
        targets = {t.id for t in self.targets}
        parameters = {p.name: p for p in self.parameters}
        target_map = {t.id: t for t in self.targets}
        for target in self.targets:
            if target.frame_target_id is not None:
                frame = target_map.get(target.frame_target_id)
                if (
                    frame is None
                    or frame.id == target.id
                    or frame.frame_target_id is not None
                ):
                    raise ValueError(
                        "Frame context requires an existing top-level target"
                    )
        live_pages = {"main"}
        seen_pages = {"main"}
        active_page = "main"
        for step in self.steps:
            schema = argument_schema(step.operation)
            if step.operation == "assert_read":
                check_read_arguments(step.arguments)
            elif step.operation == "click_dialog":
                check_dialog_arguments(step.arguments)
            elif set(step.arguments) != set(schema):
                raise ValueError("Operation arguments do not match its schema")
            if step.operation in {
                "navigate",
                "assert_url",
                "open_page",
                "switch_page",
                "close_page",
            }:
                if step.target_id is not None:
                    raise ValueError("Page operation cannot have a target")
            elif step.operation != "scroll" or step.target_id is not None:
                if step.target_id not in targets:
                    raise ValueError("Operation requires an existing target")
            if len(step.provenance) > 16:
                raise ValueError("Too many provenance fields")
            for key, argument in step.arguments.items():
                if isinstance(argument, ParameterReference):
                    parameter = parameters.get(argument.parameter)
                    kinds = {schema[key]}
                    if schema[key] == "number":
                        kinds.add("integer")
                    if (
                        key
                        in {
                            "destination_target_id",
                            "page_id",
                            "action",
                            "type",
                            "asset_id",
                            "sha256",
                        }
                        or parameter is None
                        or parameter.type not in kinds
                    ):
                        raise ValueError(
                            "Operation requires a declared parameter of the matching type"
                        )
                    if "default" in parameter.model_fields_set:
                        check_argument(
                            key,
                            parameter.default,
                            schema[key],
                            self.allowed_origins,
                            targets,
                        )
                else:
                    check_argument(
                        key, argument, schema[key], self.allowed_origins, targets
                    )
            if step.operation in {"open_page", "switch_page", "close_page"}:
                if step.page_id is not None:
                    raise ValueError(
                        "Page lifecycle operations use only arguments.page_id"
                    )
            elif (step.page_id or active_page) not in live_pages:
                raise ValueError("Operation requires a live page")
            if step.operation in {
                "open_page",
                "click_popup",
                "switch_page",
                "close_page",
            }:
                page_id = step.arguments["page_id"]
                assert isinstance(page_id, str)
                if step.operation in {"open_page", "click_popup"}:
                    if page_id in seen_pages:
                        raise ValueError("Page IDs cannot be reused")
                    seen_pages.add(page_id)
                    live_pages.add(page_id)
                else:
                    if page_id not in live_pages:
                        raise ValueError("Operation requires a live page")
                    if step.operation == "switch_page":
                        active_page = page_id
                    else:
                        if page_id == active_page:
                            raise ValueError(
                                "Switch to another page before closing the active page"
                            )
                        live_pages.remove(page_id)
            if step.operation == "drag_drop":
                destination = step.arguments["destination_target_id"]
                assert isinstance(destination, str)
                assert step.target_id is not None
                if (
                    target_map[step.target_id].frame_target_id
                    != target_map[destination].frame_target_id
                ):
                    raise ValueError(
                        "Drag source and destination must use the same frame"
                    )
        return self


def argument_schema(operation: str) -> dict[str, str]:
    return {
        "assert_read": {
            "attribute": "string",
            "name": "string",
            "equals": "string",
            "contains": "string",
            "pattern": "string",
            "nonempty": "boolean",
            "positive": "boolean",
        },
        "upload_file": {"asset_id": "string", "sha256": "string"},
        "open_page": {"page_id": "string", "url": "string"},
        "switch_page": {"page_id": "string"},
        "close_page": {"page_id": "string"},
        "click_popup": {"page_id": "string"},
        "click_dialog": {
            "action": "string",
            "type": "string",
            "message": "string",
            "prompt_text": "string",
        },
        "navigate": {"url": "string"},
        "assert_url": {"url": "string"},
        "fill": {"value": "string"},
        "select": {"value": "string"},
        "assert_text": {"text": "string"},
        "assert_text_exact": {"text": "string"},
        "assert_value": {"value": "string"},
        "scroll": {"delta_x": "number", "delta_y": "number"},
        "press": {"key": "string"},
        "wait_for": {"state": "string"},
        "drag_drop": {"destination_target_id": "string"},
        "assert_checked": {"checked": "boolean"},
    }.get(operation, {})


def check_dialog_arguments(arguments: Mapping[str, object]) -> None:
    if (
        not {"action", "type", "message"} <= arguments.keys()
        or arguments.keys() - {"action", "type", "message", "prompt_text"}
        or arguments.get("action") not in ("accept", "dismiss")
        or arguments.get("type") not in ("alert", "confirm", "prompt")
    ):
        raise ValueError("Dialog requires an explicit action, type and exact message")
    if "prompt_text" in arguments and (arguments["action"], arguments["type"]) != (
        "accept",
        "prompt",
    ):
        raise ValueError("Prompt text applies only to accepted prompts")


READ_ATTRIBUTES = frozenset(
    {
        "text",
        "value",
        "checked",
        "title",
        "alt",
        "placeholder",
        "role",
        "aria-label",
        "aria-description",
        "aria-valuenow",
        "aria-valuetext",
        "aria-expanded",
        "aria-selected",
        "aria-disabled",
        "aria-pressed",
        "aria-invalid",
        "data-testid",
    }
)


def check_read_pattern(pattern: str) -> None:
    """A deliberately small, linear-time JS/Python regex intersection.

    No groups, alternation, backreferences or counted repetition. At most one
    quantifier, anchored at the start, prevents both exponential backtracking
    and quadratic unanchored searches on the bounded DOM value.
    """
    if not pattern or len(pattern) > 200:
        raise ValueError("Read pattern must contain 1 to 200 characters")
    token = re.compile(
        r"\\[dDsSwWnrt.^$*+?\[\]{}()|\\/]|\[[a-zA-Z0-9 ^_.,:$%/\-]+\]|[^\\[\]()|{}*+?]"
    )
    offset = 0
    quantifiers = 0
    while offset < len(pattern):
        match = token.match(pattern, offset)
        if not match:
            raise ValueError("Unsupported read pattern syntax")
        atom = match.group()
        offset = match.end()
        if offset < len(pattern) and pattern[offset] in "*+?":
            if atom in {"^", "$"}:
                raise ValueError("Invalid read pattern quantifier")
            quantifiers += 1
            offset += 1
    if quantifiers > 1 or (quantifiers and not pattern.startswith("^")):
        raise ValueError("Read patterns allow one start-anchored quantifier")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError("Invalid read pattern") from exc


def check_read_arguments(arguments: Mapping[str, object]) -> None:
    schema = argument_schema("assert_read")
    if (
        arguments.keys() - schema.keys()
        or arguments.get("attribute") not in READ_ATTRIBUTES
    ):
        raise ValueError("Unsupported read attribute or constraint")
    if not (
        arguments.keys() & {"equals", "contains", "nonempty", "positive", "pattern"}
    ):
        raise ValueError("Read assertion requires a condition")
    for key, value in arguments.items():
        check_scalar(schema[key], value)
        if key in {"nonempty", "positive"} and value is not True:
            raise ValueError("Read boolean conditions must be true")
        if key == "name" and (
            not isinstance(value, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value)
        ):
            raise ValueError("Read output name must be an identifier")
        if key == "pattern":
            assert isinstance(value, str)
            check_read_pattern(value)


def check_argument(
    key: str, value: object, kind: str, origins: list[str], targets: set[str]
) -> None:
    check_scalar(kind, value)
    if key == "asset_id":
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", value
        ):
            raise ValueError("Upload asset ID must be a UUID")
    elif key == "sha256":
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Upload requires a frozen SHA-256 digest")
    elif key == "page_id":
        if not isinstance(value, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", value
        ):
            raise ValueError("Page ID must be a canonical identifier")
    elif key in {"delta_x", "delta_y"}:
        assert isinstance(value, (int, float))
        if abs(value) > 10000:
            raise ValueError("Scroll delta must be between -10000 and 10000")
    elif key == "key":
        assert isinstance(value, str)
        if (
            not value
            or len(value) > 128
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("Key must contain 1 to 128 printable characters")
    elif key == "state" and value not in ("visible", "hidden", "attached", "detached"):
        raise ValueError("Unsupported wait state")
    elif key == "destination_target_id" and value not in targets:
        raise ValueError("Drag destination requires an existing target")
    elif key == "url":
        assert isinstance(value, str)
        check_url(value, origins)


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
            check_argument(
                key,
                value,
                argument_schema(step.operation)[key],
                definition.allowed_origins,
                {t.id for t in definition.targets},
            )
    return result
