"""Stable RFC 9457 errors for the public HTTP boundary."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tap.contracts.problems import ProblemDetails, build_problem
from tap.interfaces.http.dependencies import KnowledgeRuntimeUnavailable
from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable
from tap.modules.knowledge.application.answers import (
    AnswerSelectionRejected,
    AnswerSnapshotUnavailable,
    DocumentStateChanged,
)
from tap.modules.knowledge.application.citations import CitationStale, CitationUnavailable
from tap.modules.knowledge.application.demo_policy import DocumentPolicyChanged
from tap.modules.knowledge.domain.documents import DocumentParseRejected
from tap.modules.knowledge.domain.sources import (
    SourceCommandConflict,
    SourceCommandPending,
    SourceCommandReplay,
    SourceUnavailable,
)
from tap.modules.knowledge.ports.documents import (
    DocumentCapacityExceeded,
    DocumentNotFound,
    InvalidDocumentCursor,
    RetryNotAllowed,
)
from tap.modules.knowledge.ports.errors import (
    AnswerUnavailable,
    ArtifactUnavailable,
    ModelUnavailable,
    SearchBoundsExceeded,
    SearchUnavailable,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"


class InvalidDocumentUpload(Exception):
    """A public document upload violates a fixed metadata or byte constraint."""

    def __init__(self, code: str = "unsupported-document") -> None:
        self.code = code


def document_upload_problem(error: InvalidDocumentUpload) -> str:
    if error.code == "document-too-large":
        return "document-too-large"
    return "unsupported-document"


def document_parse_problem(error: DocumentParseRejected) -> str:
    if error.code == "document-too-large":
        return "document-too-large"
    if error.code == "empty-document":
        return "empty-document"
    return "unsupported-document"


def problem_response(code: str, request: Request) -> JSONResponse:
    problem = build_problem(code, correlation_id=request.state.correlation_id)
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(by_alias=True, exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
        headers={"X-Correlation-ID": problem.correlation_id},
    )


def problem_response_metadata(description: str) -> dict[str, object]:
    return {
        "description": description,
        "content": {
            PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}},
        },
    }


class RequestCorrelationMiddleware:
    """Capture request identity without task groups that alter cancellation propagation."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        correlation_id = uuid4().hex
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Correlation-ID"] = correlation_id
            await send(message)

        await self.app(scope, receive, send_with_correlation)


def register_problem_handlers(app: FastAPI) -> None:
    original_openapi = app.openapi

    def openapi_with_problems() -> dict[str, Any]:
        schema = original_openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        # Response metadata uses a shared component while preserving the Problem
        # media type. Runtime docs and the offline exporter call this same hook.
        problem_schema = ProblemDetails.model_json_schema(
            by_alias=True, ref_template="#/components/schemas/{model}"
        )
        components.update(problem_schema.pop("$defs", {}))
        components["ProblemDetails"] = problem_schema
        return schema

    app.openapi = openapi_with_problems  # type: ignore[method-assign]
    app.add_middleware(RequestCorrelationMiddleware)

    @app.exception_handler(RequestValidationError)
    async def request_validation_problem(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return problem_response("request-validation", _request)

    @app.exception_handler(KnowledgeRuntimeUnavailable)
    @app.exception_handler(ArtifactUnavailable)
    async def knowledge_runtime_unavailable_problem(
        _request: Request, _error: KnowledgeRuntimeUnavailable | ArtifactUnavailable
    ) -> JSONResponse:
        return problem_response("knowledge-runtime-unavailable", _request)

    @app.exception_handler(InvalidDocumentUpload)
    async def invalid_document_upload_problem(
        _request: Request, error: InvalidDocumentUpload
    ) -> JSONResponse:
        return problem_response(document_upload_problem(error), _request)

    @app.exception_handler(DocumentParseRejected)
    async def document_parse_rejected_problem(
        _request: Request, error: DocumentParseRejected
    ) -> JSONResponse:
        return problem_response(document_parse_problem(error), _request)

    @app.exception_handler(SourceCommandConflict)
    async def source_conflict(request: Request, error: SourceCommandConflict) -> JSONResponse:
        return problem_response("idempotency-conflict", request)

    @app.exception_handler(SourceCommandPending)
    async def source_pending(request: Request, error: SourceCommandPending) -> JSONResponse:
        response = problem_response("source-command-pending", request)
        response.headers["Retry-After"] = "1"
        return response

    @app.exception_handler(SourceUnavailable)
    async def source_unavailable(request: Request, error: SourceUnavailable) -> JSONResponse:
        return problem_response(
            "association-conflict"
            if str(error) == "source-dedupe-conflict"
            else "source-not-found",
            request,
        )

    @app.exception_handler(SourceCommandReplay)
    async def source_failed_replay(request: Request, error: SourceCommandReplay) -> JSONResponse:
        code = error.result.body.get("code") if error.result.body is not None else None
        if code not in {"source-unavailable", "source-not-found", "document-not-retryable"}:
            return problem_response("knowledge-runtime-unavailable", request)
        return problem_response(str(code), request)

    @app.exception_handler(DocumentNotFound)
    async def document_not_found_problem(
        _request: Request, _error: DocumentNotFound
    ) -> JSONResponse:
        return problem_response("document-not-found", _request)

    @app.exception_handler(RetryNotAllowed)
    async def document_not_retryable_problem(
        _request: Request, _error: RetryNotAllowed
    ) -> JSONResponse:
        return problem_response("document-not-retryable", _request)

    @app.exception_handler(DocumentCapacityExceeded)
    async def document_limit_reached_problem(
        _request: Request, _error: DocumentCapacityExceeded
    ) -> JSONResponse:
        return problem_response("document-limit-reached", _request)

    @app.exception_handler(InvalidDocumentCursor)
    async def invalid_document_cursor_problem(
        _request: Request, _error: InvalidDocumentCursor
    ) -> JSONResponse:
        return problem_response("request-validation", _request)

    @app.exception_handler(AnswerSelectionRejected)
    async def answer_selection_problem(
        _request: Request, error: AnswerSelectionRejected
    ) -> JSONResponse:
        problem = (
            "source-selection-required"
            if error.code == "source-selection-required"
            else "unsupported-answer-control"
        )
        return problem_response(problem, _request)

    @app.exception_handler(DocumentPolicyChanged)
    @app.exception_handler(DocumentStateChanged)
    async def document_state_changed_problem(
        _request: Request, _error: DocumentStateChanged | DocumentPolicyChanged
    ) -> JSONResponse:
        return problem_response("document-state-changed", _request)

    @app.exception_handler(AuthorizationDenied)
    async def authorization_problem(_request: Request, error: AuthorizationDenied) -> JSONResponse:
        code = "scope-mismatch" if error.args == ("scope-mismatch",) else "authorization-denied"
        return problem_response(code, _request)

    @app.exception_handler(PolicyUnavailable)
    async def policy_unavailable_problem(
        _request: Request, _error: PolicyUnavailable
    ) -> JSONResponse:
        return problem_response("search-unavailable", _request)

    @app.exception_handler(ModelUnavailable)
    async def embedding_unavailable_problem(
        _request: Request, _error: ModelUnavailable
    ) -> JSONResponse:
        return problem_response("embedding-unavailable", _request)

    @app.exception_handler(AnswerUnavailable)
    async def answer_unavailable_problem(
        _request: Request, _error: AnswerUnavailable
    ) -> JSONResponse:
        return problem_response("answer-unavailable", _request)

    @app.exception_handler(AnswerSnapshotUnavailable)
    async def answer_snapshot_unavailable_problem(
        _request: Request, _error: AnswerSnapshotUnavailable
    ) -> JSONResponse:
        return problem_response("answer-snapshot-unavailable", _request)

    @app.exception_handler(CitationStale)
    async def citation_stale_problem(_request: Request, _error: CitationStale) -> JSONResponse:
        return problem_response("citation-stale", _request)

    @app.exception_handler(CitationUnavailable)
    async def citation_unavailable_problem(
        _request: Request, _error: CitationUnavailable
    ) -> JSONResponse:
        return problem_response("citation-unavailable", _request)

    @app.exception_handler(SearchUnavailable)
    async def search_unavailable_problem(
        _request: Request, _error: SearchUnavailable
    ) -> JSONResponse:
        return problem_response("search-unavailable", _request)

    @app.exception_handler(SearchBoundsExceeded)
    async def search_execution_rejected_problem(
        _request: Request, _error: SearchBoundsExceeded
    ) -> JSONResponse:
        return problem_response("search-execution-rejected", _request)

    @app.exception_handler(Exception)
    async def unexpected_rest_problem(_request: Request, _error: Exception) -> JSONResponse:
        return problem_response("knowledge-runtime-unavailable", _request)
