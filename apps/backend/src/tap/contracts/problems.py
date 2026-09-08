"""Single authority for safe, registered HTTP and public stream failures."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

FailureStage = Literal["search", "embedding", "answer", "graph", "model", "recorder", "execution"]


@dataclass(frozen=True, slots=True)
class ProblemDefinition:
    code: str
    title: str
    status: int
    detail: str
    retryable: bool
    failure_stage: FailureStage | None = None

    @property
    def type(self) -> str:
        return f"https://tap.example/problems/{self.code}"


PROBLEM_REGISTRY = MappingProxyType(
    {
        definition.code: definition
        for definition in (
            ProblemDefinition(
                "source-not-found",
                "Source not found",
                404,
                "The Source is unavailable in this Project.",
                False,
            ),
            ProblemDefinition(
                "source-unavailable",
                "Source state changed",
                409,
                "The Source is no longer available for this command.",
                False,
            ),
            ProblemDefinition(
                "source-command-pending",
                "Source command in progress",
                503,
                "The original Source command is still in progress; retry the same key.",
                True,
            ),
            ProblemDefinition(
                "request-validation",
                "Request validation failed",
                422,
                "The request body does not match the public API contract.",
                False,
                None,
            ),
            ProblemDefinition(
                "knowledge-runtime-unavailable",
                "Knowledge runtime unavailable",
                503,
                "The knowledge runtime is not configured.",
                True,
                None,
            ),
            ProblemDefinition(
                "search-unavailable",
                "Search unavailable",
                503,
                "The search provider is currently unavailable.",
                True,
                "search",
            ),
            ProblemDefinition(
                "search-execution-rejected",
                "Search execution rejected",
                503,
                "The search execution exceeded a safety bound.",
                True,
                "search",
            ),
            ProblemDefinition(
                "unsupported-document",
                "Unsupported document",
                400,
                "The document filename, media type, or content is not supported.",
                False,
                None,
            ),
            ProblemDefinition(
                "empty-document",
                "Empty document",
                400,
                "The document contains no processable content.",
                False,
                None,
            ),
            ProblemDefinition(
                "document-too-large",
                "Document too large",
                413,
                "The document exceeds the 25 MiB upload limit.",
                False,
                None,
            ),
            ProblemDefinition(
                "document-not-found",
                "Document not found",
                404,
                "The requested document does not exist.",
                False,
                None,
            ),
            ProblemDefinition(
                "document-not-retryable",
                "Document is not retryable",
                409,
                "Only a failed document can be retried.",
                False,
                None,
            ),
            ProblemDefinition(
                "document-state-changed",
                "Document state changed",
                409,
                "A selected document is no longer ready at its selected revision.",
                False,
                None,
            ),
            ProblemDefinition(
                "document-limit-reached",
                "Document limit reached",
                429,
                "The local knowledge space has reached its document limit.",
                False,
                None,
            ),
            ProblemDefinition(
                "source-selection-required",
                "Source selection required",
                400,
                "Select between one and twenty unique ready documents.",
                False,
                None,
            ),
            ProblemDefinition(
                "unsupported-answer-control",
                "Unsupported answer control",
                400,
                "The answer request contains a control unavailable in this demo.",
                False,
                None,
            ),
            ProblemDefinition(
                "embedding-unavailable",
                "Embedding unavailable",
                503,
                "The embedding service is currently unavailable.",
                True,
                "embedding",
            ),
            ProblemDefinition(
                "answer-unavailable",
                "Answer unavailable",
                503,
                "The answer service is currently unavailable.",
                True,
                "answer",
            ),
            ProblemDefinition(
                "answer-snapshot-unavailable",
                "Answer snapshot unavailable",
                503,
                "The grounded answer could not be committed atomically.",
                True,
                "answer",
            ),
            ProblemDefinition(
                "citation-stale",
                "Citation stale",
                404,
                "The citation no longer resolves to its exact source revision.",
                False,
                None,
            ),
            ProblemDefinition(
                "citation-unavailable",
                "Citation unavailable",
                503,
                "The citation provider is currently unavailable.",
                True,
                None,
            ),
            ProblemDefinition(
                "turn-not-implemented",
                "Turn workflow not implemented",
                501,
                "The durable chat turn workflow is not available yet.",
                False,
                None,
            ),
            ProblemDefinition(
                "scope-mismatch",
                "Scope mismatch",
                403,
                "The requested project does not match the current scope.",
                False,
                None,
            ),
            ProblemDefinition(
                "authorization-denied",
                "Authorization denied",
                403,
                "The current actor and scope do not allow this operation.",
                False,
                None,
            ),
            ProblemDefinition(
                "idempotency-conflict",
                "Idempotency conflict",
                409,
                "The idempotency key already identifies a different request.",
                False,
                None,
            ),
            ProblemDefinition(
                "revision-conflict",
                "Revision conflict",
                409,
                "The requested revision conflicts with the current revision.",
                False,
                None,
            ),
            ProblemDefinition(
                "association-conflict",
                "Association conflict",
                409,
                "An asset is already associated with another asset.",
                False,
                None,
            ),
            ProblemDefinition(
                "automation-mapping-required",
                "Automation mapping required",
                409,
                "The published revision requires a compatible step mapping.",
                False,
                None,
            ),
            ProblemDefinition(
                "graph-unavailable",
                "Graph unavailable",
                503,
                "The graph service is currently unavailable.",
                True,
                "graph",
            ),
            ProblemDefinition(
                "model-unavailable",
                "Model unavailable",
                503,
                "The model service is currently unavailable.",
                True,
                "model",
            ),
            ProblemDefinition(
                "recorder-unavailable",
                "Recorder unavailable",
                503,
                "The recorder service is currently unavailable.",
                True,
                "recorder",
            ),
            ProblemDefinition(
                "execution-provider-unavailable",
                "Execution provider unavailable",
                503,
                "The execution provider is currently unavailable.",
                True,
                "execution",
            ),
        )
    }
)
_BY_TYPE = {definition.type: definition for definition in PROBLEM_REGISTRY.values()}


class ProblemDetails(BaseModel):
    """Closed RFC 9457 error projection; only registered safe text is accepted."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "type": {"const": item.type},
                        "title": {"const": item.title},
                        "status": {"const": item.status},
                        "detail": {"const": item.detail},
                        "retryable": {"const": item.retryable},
                        "failureStage": {"const": item.failure_stage},
                    },
                    **({"required": ["failureStage"]} if item.failure_stage is not None else {}),
                }
                for item in sorted(PROBLEM_REGISTRY.values(), key=lambda value: value.code)
            ]
        },
    )
    type: str = Field(
        strict=True,
        pattern=r"^https://",
        json_schema_extra={"enum": [uri for uri in sorted(_BY_TYPE)]},
    )
    title: str = Field(strict=True, min_length=1)
    status: int = Field(strict=True, ge=400, le=599)
    detail: str = Field(strict=True, min_length=1)
    instance: str | None = Field(default=None, strict=True, min_length=1, max_length=2048)
    correlation_id: str = Field(
        strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    retryable: bool = Field(strict=True)
    failure_stage: FailureStage | None = None

    @model_validator(mode="after")
    def validate_registered_problem(self) -> Self:
        definition = _BY_TYPE.get(self.type)
        if definition is None or (
            self.title,
            self.status,
            self.detail,
            self.retryable,
            self.failure_stage,
        ) != (
            definition.title,
            definition.status,
            definition.detail,
            definition.retryable,
            definition.failure_stage,
        ):
            raise ValueError("problem must match registered safe semantics")
        return self


def build_problem(code: str, *, correlation_id: str, instance: str | None = None) -> ProblemDetails:
    definition = PROBLEM_REGISTRY[code]
    return ProblemDetails(
        type=definition.type,
        title=definition.title,
        status=definition.status,
        detail=definition.detail,
        retryable=definition.retryable,
        failure_stage=definition.failure_stage,
        correlation_id=correlation_id,
        instance=instance,
    )


def problem_registry_document() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "failureStages": [
            "search",
            "embedding",
            "answer",
            "graph",
            "model",
            "recorder",
            "execution",
        ],
        "problems": [
            {
                "code": item.code,
                "type": item.type,
                "title": item.title,
                "status": item.status,
                "detail": item.detail,
                "retryable": item.retryable,
                **({"failureStage": item.failure_stage} if item.failure_stage is not None else {}),
            }
            for item in sorted(PROBLEM_REGISTRY.values(), key=lambda value: value.code)
        ],
    }
