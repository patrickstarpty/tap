"""Frozen AA acceptance evidence. It does not certify subsequent LCA edits."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


class SourceModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class AcceptanceField(SourceModel):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
    value: Annotated[str, Field(max_length=4000)]
    readback_value: Annotated[str, Field(max_length=4000)]
    attribute: Literal[
        "text", "value", "checked", "aria-pressed", "aria-valuenow", "data-active"
    ]
    target_texts: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=200)]],
        Field(min_length=1, max_length=6),
    ]
    scope_text: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @model_validator(mode="after")
    def nonblank_targets(self) -> "AcceptanceField":
        if self.id in {
            "final_url",
            "provenance",
            "source_url",
            "observed_at",
            "observation_sequence",
        }:
            raise ValueError("Acceptance field ID is reserved for browser provenance")
        if any(not text.strip() for text in self.target_texts) or (
            self.scope_text is not None and not self.scope_text.strip()
        ):
            raise ValueError("Acceptance target descriptions must not be blank")
        return self


class ReviewedAcceptanceContract(SourceModel):
    version: Literal[1]
    reviewed: Literal[True]
    fields: Annotated[list[AcceptanceField], Field(min_length=1, max_length=20)]

    @field_validator("version", "reviewed", mode="before")
    @classmethod
    def exact_wire_types(cls, value: object, info: ValidationInfo) -> object:
        expected = int if info.field_name == "version" else bool
        if type(value) is not expected:
            raise ValueError("Invalid acceptance contract wire type")
        return value

    @model_validator(mode="after")
    def unique_fields(self) -> "ReviewedAcceptanceContract":
        if len({field.id for field in self.fields}) != len(self.fields):
            raise ValueError("Duplicate acceptance field")
        return self


class SourceIds(SourceModel):
    task_id: Annotated[str, Field(min_length=1, max_length=80)]
    execution_id: Annotated[str, Field(min_length=1, max_length=80)]
    bundle_id: Annotated[str, Field(min_length=1, max_length=80)]
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TargetContract(SourceModel):
    target_texts: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=200)]],
        Field(min_length=1, max_length=6),
    ]
    scope_text: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @model_validator(mode="after")
    def nonblank(self) -> "TargetContract":
        if any(not text.strip() for text in self.target_texts) or (
            self.scope_text is not None and not self.scope_text.strip()
        ):
            raise ValueError("Blank target contract")
        return self


class SourceAcceptance(SourceModel):
    source: SourceIds
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    review_status: Literal["reviewed", "legacy_unreviewed"]
    acceptance_contract: ReviewedAcceptanceContract | None

    @model_validator(mode="after")
    def status_matches_contract(self) -> "SourceAcceptance":
        if self.source.input_sha256 != self.input_sha256:
            raise ValueError("Source input digest mismatch")
        if (self.review_status == "reviewed") != (self.acceptance_contract is not None):
            raise ValueError("Source review status does not match contract")
        return self
