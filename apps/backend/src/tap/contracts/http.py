"""Public HTTP DTOs for the first Knowledge Chat contract slice."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    """Base model that exposes camelCase JSON without accepting unknown fields."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class SourceFamily(str, Enum):
    DOC = "doc"
    CODE = "code"
    BDD = "bdd"
    FAILURE = "failure"


class ResourceMode(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    SCOPE = "scope"


class AnswerMode(str, Enum):
    QUICK = "quick"
    DEEP = "deep"


class DocumentAnchor(ContractModel):
    type: Literal["document"]
    heading_path: list[str] | None = None
    page: int | None = None
    bbox: list[float] | None = None
    start_offset: int | None = None
    end_offset: int | None = None


class CodeAnchor(ContractModel):
    type: Literal["code"]
    repo: str = Field(min_length=1)
    path: str = Field(min_length=1)
    symbol: str | None = None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class BddAnchor(ContractModel):
    type: Literal["bdd"]
    feature_id: str = Field(min_length=1)
    scenario_id: str | None = None
    step_id: str | None = None


class OpenApiAnchor(ContractModel):
    type: Literal["openapi"]
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    json_pointer: str = Field(min_length=1)


class FailureAnchor(ContractModel):
    type: Literal["failure"]
    incident_id: str = Field(min_length=1)
    run_id: str | None = None
    time_start: str | None = None
    time_end: str | None = None


StructuralAnchorValue = Annotated[
    DocumentAnchor | CodeAnchor | BddAnchor | OpenApiAnchor | FailureAnchor,
    Field(discriminator="type"),
]


class StructuralAnchor(RootModel[StructuralAnchorValue]):
    """A closed, structural location inside one authorized source family."""


class ResourceRef(ContractModel):
    """Browser-provided retrieval intent; it cannot contain policy or ACL facts."""

    family: SourceFamily
    source_id: str = Field(min_length=1)
    mode: ResourceMode = ResourceMode.PREFERRED
    requested_revision: str | None = None
    anchor: StructuralAnchor | None = None


class ChatTurnRequest(ContractModel):
    """A browser request to create one turn in an existing chat."""

    client_request_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    answer_mode: AnswerMode = AnswerMode.QUICK
    source_scope: list[SourceFamily] | None = None
    resource_refs: list[ResourceRef] | None = None
    requested_environment: str | None = None
    requested_corpus_version: str | None = None


class ChatTurnAccepted(ContractModel):
    """The durable identity returned after a turn has been accepted for processing."""

    chat_id: str
    turn_id: str
    state: Literal["queued"]


class ProblemDetails(ContractModel):
    """RFC 9457 problem details returned by the public HTTP interface."""

    type: str = Field(pattern=r"^https://")
    title: str = Field(min_length=1)
    status: int = Field(ge=100, le=599)
    detail: str = Field(min_length=1)
    instance: str | None = None
