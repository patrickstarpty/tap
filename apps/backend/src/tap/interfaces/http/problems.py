"""Stable RFC 9457 errors for the public HTTP boundary."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tap.contracts.http import ProblemDetails
from tap.interfaces.http.dependencies import KnowledgeRuntimeUnavailable
from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable
from tap.modules.knowledge.application.answers import (
    AnswerSelectionRejected,
    AnswerSnapshotUnavailable,
    DocumentStateChanged,
)
from tap.modules.knowledge.application.citations import CitationStale, CitationUnavailable
from tap.modules.knowledge.domain.documents import DocumentParseRejected
from tap.modules.knowledge.ports.documents import (
    DocumentCapacityExceeded,
    DocumentNotFound,
    InvalidDocumentCursor,
    RetryNotAllowed,
)
from tap.modules.knowledge.ports.errors import (
    SEARCH_EXECUTION_REJECTED_TYPE,
    SEARCH_UNAVAILABLE_TYPE,
    ArtifactUnavailable,
    ModelUnavailable,
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
UNSUPPORTED_DOCUMENT_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/unsupported-document",
    title="Unsupported document",
    status=status.HTTP_400_BAD_REQUEST,
    detail="The document filename, media type, or content is not supported.",
)
EMPTY_DOCUMENT_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/empty-document",
    title="Empty document",
    status=status.HTTP_400_BAD_REQUEST,
    detail="The document contains no processable content.",
)
DOCUMENT_TOO_LARGE_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/document-too-large",
    title="Document too large",
    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    detail="The document exceeds the 25 MiB upload limit.",
)
DOCUMENT_NOT_FOUND_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/document-not-found",
    title="Document not found",
    status=status.HTTP_404_NOT_FOUND,
    detail="The requested document does not exist.",
)
DOCUMENT_NOT_RETRYABLE_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/document-not-retryable",
    title="Document is not retryable",
    status=status.HTTP_409_CONFLICT,
    detail="Only a failed document can be retried.",
)
DOCUMENT_STATE_CHANGED_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/document-state-changed",
    title="Document state changed",
    status=status.HTTP_409_CONFLICT,
    detail="A selected document is no longer ready at its selected revision.",
)
DOCUMENT_LIMIT_REACHED_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/document-limit-reached",
    title="Document limit reached",
    status=status.HTTP_429_TOO_MANY_REQUESTS,
    detail="The local knowledge space has reached its document limit.",
)
SOURCE_SELECTION_REQUIRED_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/source-selection-required",
    title="Source selection required",
    status=status.HTTP_400_BAD_REQUEST,
    detail="Select between one and twenty unique ready documents.",
)
UNSUPPORTED_ANSWER_CONTROL_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/unsupported-answer-control",
    title="Unsupported answer control",
    status=status.HTTP_400_BAD_REQUEST,
    detail="The answer request contains a control unavailable in this demo.",
)
EMBEDDING_UNAVAILABLE_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/embedding-unavailable",
    title="Embedding unavailable",
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The embedding service is currently unavailable.",
)
ANSWER_SNAPSHOT_UNAVAILABLE_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/answer-snapshot-unavailable",
    title="Answer snapshot unavailable",
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The grounded answer could not be committed atomically.",
)
CITATION_STALE_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/citation-stale",
    title="Citation stale",
    status=status.HTTP_404_NOT_FOUND,
    detail="The citation no longer resolves to its exact source revision.",
)
CITATION_UNAVAILABLE_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/citation-unavailable",
    title="Citation unavailable",
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The citation provider is currently unavailable.",
)


class InvalidDocumentUpload(Exception):
    """A public document upload violates a fixed metadata or byte constraint."""

    def __init__(self, code: str = "unsupported-document") -> None:
        self.code = code


def document_upload_problem(error: InvalidDocumentUpload) -> ProblemDetails:
    if error.code == "document-too-large":
        return DOCUMENT_TOO_LARGE_PROBLEM
    return UNSUPPORTED_DOCUMENT_PROBLEM


def document_parse_problem(error: DocumentParseRejected) -> ProblemDetails:
    if error.code == "document-too-large":
        return DOCUMENT_TOO_LARGE_PROBLEM
    if error.code == "empty-document":
        return EMPTY_DOCUMENT_PROBLEM
    return UNSUPPORTED_DOCUMENT_PROBLEM


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
    @app.exception_handler(ArtifactUnavailable)
    async def knowledge_runtime_unavailable_problem(
        _request: Request, _error: KnowledgeRuntimeUnavailable | ArtifactUnavailable
    ) -> JSONResponse:
        return problem_response(KNOWLEDGE_RUNTIME_UNAVAILABLE_PROBLEM)

    @app.exception_handler(InvalidDocumentUpload)
    async def invalid_document_upload_problem(
        _request: Request, error: InvalidDocumentUpload
    ) -> JSONResponse:
        return problem_response(document_upload_problem(error))

    @app.exception_handler(DocumentParseRejected)
    async def document_parse_rejected_problem(
        _request: Request, error: DocumentParseRejected
    ) -> JSONResponse:
        return problem_response(document_parse_problem(error))

    @app.exception_handler(DocumentNotFound)
    async def document_not_found_problem(
        _request: Request, _error: DocumentNotFound
    ) -> JSONResponse:
        return problem_response(DOCUMENT_NOT_FOUND_PROBLEM)

    @app.exception_handler(RetryNotAllowed)
    async def document_not_retryable_problem(
        _request: Request, _error: RetryNotAllowed
    ) -> JSONResponse:
        return problem_response(DOCUMENT_NOT_RETRYABLE_PROBLEM)

    @app.exception_handler(DocumentCapacityExceeded)
    async def document_limit_reached_problem(
        _request: Request, _error: DocumentCapacityExceeded
    ) -> JSONResponse:
        return problem_response(DOCUMENT_LIMIT_REACHED_PROBLEM)

    @app.exception_handler(InvalidDocumentCursor)
    async def invalid_document_cursor_problem(
        _request: Request, _error: InvalidDocumentCursor
    ) -> JSONResponse:
        return problem_response(VALIDATION_PROBLEM)

    @app.exception_handler(AnswerSelectionRejected)
    async def answer_selection_problem(
        _request: Request, error: AnswerSelectionRejected
    ) -> JSONResponse:
        problem = (
            SOURCE_SELECTION_REQUIRED_PROBLEM
            if error.code == "source-selection-required"
            else UNSUPPORTED_ANSWER_CONTROL_PROBLEM
        )
        return problem_response(problem)

    @app.exception_handler(DocumentStateChanged)
    @app.exception_handler(AuthorizationDenied)
    async def document_state_changed_problem(
        _request: Request, _error: DocumentStateChanged | AuthorizationDenied
    ) -> JSONResponse:
        return problem_response(DOCUMENT_STATE_CHANGED_PROBLEM)

    @app.exception_handler(PolicyUnavailable)
    async def policy_unavailable_problem(
        _request: Request, _error: PolicyUnavailable
    ) -> JSONResponse:
        return problem_response(SEARCH_UNAVAILABLE_PROBLEM)

    @app.exception_handler(ModelUnavailable)
    async def embedding_unavailable_problem(
        _request: Request, _error: ModelUnavailable
    ) -> JSONResponse:
        return problem_response(EMBEDDING_UNAVAILABLE_PROBLEM)

    @app.exception_handler(AnswerSnapshotUnavailable)
    async def answer_snapshot_unavailable_problem(
        _request: Request, _error: AnswerSnapshotUnavailable
    ) -> JSONResponse:
        return problem_response(ANSWER_SNAPSHOT_UNAVAILABLE_PROBLEM)

    @app.exception_handler(CitationStale)
    async def citation_stale_problem(_request: Request, _error: CitationStale) -> JSONResponse:
        return problem_response(CITATION_STALE_PROBLEM)

    @app.exception_handler(CitationUnavailable)
    async def citation_unavailable_problem(
        _request: Request, _error: CitationUnavailable
    ) -> JSONResponse:
        return problem_response(CITATION_UNAVAILABLE_PROBLEM)

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

    @app.exception_handler(Exception)
    async def unexpected_rest_problem(_request: Request, _error: Exception) -> JSONResponse:
        return problem_response(KNOWLEDGE_RUNTIME_UNAVAILABLE_PROBLEM)
