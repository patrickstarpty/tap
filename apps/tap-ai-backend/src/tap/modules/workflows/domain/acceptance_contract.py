"""Explicit user-reviewed input facts, independent of model-authored BDD plans."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from pydantic.alias_generators import to_camel

Label = Annotated[str, Field(strict=True, min_length=1, max_length=200)]
Value = Annotated[str, Field(strict=True, max_length=4000)]


class BrowserRequiredInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True
    )
    id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    value: Value
    readback_value: Value
    attribute: Literal["text", "value", "checked", "aria-pressed", "aria-valuenow"]
    target_texts: tuple[Label, ...] = Field(min_length=1, max_length=6)
    scope_text: Label | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        if any(not label.strip() for label in self.target_texts) or (
            self.scope_text is not None and not self.scope_text.strip()
        ):
            raise ValueError("reviewed input labels must be nonblank")
        if self.id in {
            "final_url",
            "provenance",
            "source_url",
            "observed_at",
            "observation_sequence",
        }:
            raise ValueError("reviewed input ID is reserved for browser provenance")
        return self

    def matches_target(self, action: dict[str, Any]) -> bool:
        targets = action.get("target_texts")
        return (
            isinstance(targets, (list, tuple))
            and bool(targets)
            and all(isinstance(t, str) and t in self.target_texts for t in targets)
            and action.get("scope_text") == self.scope_text
        )

    def matches_read(self, action: dict[str, Any]) -> bool:
        return (
            action.get("type") == "read"
            and action.get("name") == self.id
            and action.get("equals") == self.readback_value
            and action.get("attribute", "text") == self.attribute
            and self.matches_target(action)
        )


class BrowserAcceptanceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    reviewed: StrictBool
    fields: tuple[BrowserRequiredInput, ...] = Field(min_length=1, max_length=20)

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("acceptance contract version must be integer 1")
        return value

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.reviewed is not True:
            raise ValueError("acceptance contract requires explicit user review")
        if len({field.id for field in self.fields}) != len(self.fields):
            raise ValueError("duplicate reviewed input ID")
        return self

    @classmethod
    def from_input_data(cls, data: dict[str, Any]) -> BrowserAcceptanceContract | None:
        if "acceptance_contract" not in data:
            return None  # Historical freeform requests remain explicitly unreviewed.
        return cls.model_validate(data["acceptance_contract"])
