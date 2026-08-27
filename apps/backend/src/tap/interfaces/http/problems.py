"""Stable RFC 9457 errors for the public HTTP boundary."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tap.contracts.http import ProblemDetails
from tap.interfaces.http.dependencies import KnowledgeRuntimeUnavailable
from tap.modules.knowledge.ports.errors import (
    SEARCH_EXECUTION_REJECTED_TYPE,
    SEARCH_UNAVAILABLE_TYPE,
    SearchBoundsExceeded,
    SearchUnavailable,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
VALIDATION_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/request-validation",
    title="Request validation failed",
    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail="The request body does not match the public API contract.",
)
KNOWLEDGE_RUNTIME_UNAVAILABLE_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/knowledge-runtime-unavailable",
    title="Knowledge runtime unavailable",
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The knowledge runtime is not configured.",
)
SEARCH_UNAVAILABLE_PROBLEM = ProblemDetails(
    type=SEARCH_UNAVAILABLE_TYPE,
    title="Search unavailable",
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The search provider is currently unavailable.",
)
SEARCH_EXECUTION_REJECTED_PROBLEM = ProblemDetails(
    type=SEARCH_EXECUTION_REJECTED_TYPE,
    title="Search execution rejected",
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The search execution exceeded a safety bound.",
)


class InvalidDocumentUpload(Exception):
    """A public document upload violates a fixed metadata or byte constraint."""

    def __init__(self, code: str = "unsupported-document") -> None:
        self.code = code


def document_upload_problem(error: InvalidDocumentUpload) -> ProblemDetails:
    if error.code == "document-too-large":
        return ProblemDetails(
            type="https://tap.example/problems/document-too-large",
            title="Document too large",
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The document exceeds the 25 MiB upload limit.",
        )
    return ProblemDetails(
        type="https://tap.example/problems/unsupported-document",
        title="Unsupported document",
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="The document filename or media type is not supported.",
    )


def problem_response(problem: ProblemDetails) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(by_alias=True, exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
    )


def problem_response_metadata(description: str) -> dict[str, object]:
    return {
        "description": description,
        "content": {
            PROBLEM_MEDIA_TYPE: {"schema": ProblemDetails.model_json_schema(by_alias=True)},
        },
    }


def register_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def request_validation_problem(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return problem_response(VALIDATION_PROBLEM)

    @app.exception_handler(KnowledgeRuntimeUnavailable)
    async def knowledge_runtime_unavailable_problem(
        _request: Request, _error: KnowledgeRuntimeUnavailable
    ) -> JSONResponse:
        return problem_response(KNOWLEDGE_RUNTIME_UNAVAILABLE_PROBLEM)

    @app.exception_handler(InvalidDocumentUpload)
    async def invalid_document_upload_problem(
        _request: Request, error: InvalidDocumentUpload
    ) -> JSONResponse:
        return problem_response(document_upload_problem(error))

    @app.exception_handler(SearchUnavailable)
    async def search_unavailable_problem(
        _request: Request, _error: SearchUnavailable
    ) -> JSONResponse:
        return problem_response(SEARCH_UNAVAILABLE_PROBLEM)

    @app.exception_handler(SearchBoundsExceeded)
    async def search_execution_rejected_problem(
        _request: Request, _error: SearchBoundsExceeded
    ) -> JSONResponse:
        return problem_response(SEARCH_EXECUTION_REJECTED_PROBLEM)
