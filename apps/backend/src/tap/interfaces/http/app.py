"""FastAPI application factory used for deterministic OpenAPI export."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tap.contracts.http import ChatTurnAccepted, ChatTurnRequest, ProblemDetails
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
NOT_IMPLEMENTED_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/turn-not-implemented",
    title="Turn workflow not implemented",
    status=status.HTTP_501_NOT_IMPLEMENTED,
    detail="The durable chat turn workflow is not available yet.",
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


def problem_response(problem: ProblemDetails) -> JSONResponse:
    """Return a RFC 9457 problem with a status consistent with its body."""
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(by_alias=True, exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
    )


def problem_response_metadata(description: str) -> dict[str, object]:
    """Build OpenAPI failure metadata from the public Problem Details model."""
    return {
        "description": description,
        "content": {
            PROBLEM_MEDIA_TYPE: {"schema": ProblemDetails.model_json_schema(by_alias=True)},
        },
    }


def create_app() -> FastAPI:
    """Build the HTTP application without starting middleware or external clients."""
    app = FastAPI(title="TAP API", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    async def request_validation_problem(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return problem_response(VALIDATION_PROBLEM)

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

    @app.post(
        "/v1/chats/{chat_id}/turns",
        operation_id="chat_create_turn",
        response_model=ChatTurnAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata(
                "Request validation failed"
            ),
            status.HTTP_501_NOT_IMPLEMENTED: problem_response_metadata(
                "Turn workflow not implemented"
            ),
        },
    )
    async def create_chat_turn(chat_id: str, request: ChatTurnRequest) -> ChatTurnAccepted:
        """Reserve the public route until the durable turn workflow is implemented."""
        del chat_id, request
        return problem_response(NOT_IMPLEMENTED_PROBLEM)  # type: ignore[return-value]

    return app
