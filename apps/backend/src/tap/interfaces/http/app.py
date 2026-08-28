"""FastAPI application factory used for deterministic OpenAPI export."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI, status

from tap.contracts.http import ChatTurnAccepted, ChatTurnRequest, ProblemDetails
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.problems import (
    problem_response,
    problem_response_metadata,
    register_problem_handlers,
)
from tap.interfaces.http.routes.citations import router as citations_router
from tap.interfaces.http.routes.health import router as health_router
from tap.interfaces.http.routes.knowledge_answers import router as knowledge_answers_router
from tap.interfaces.http.routes.knowledge_documents import router as knowledge_documents_router

NOT_IMPLEMENTED_PROBLEM = ProblemDetails(
    type="https://tap.example/problems/turn-not-implemented",
    title="Turn workflow not implemented",
    status=status.HTTP_501_NOT_IMPLEMENTED,
    detail="The durable chat turn workflow is not available yet.",
)


Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    services: HttpServices | None = None,
    *,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    """Build the HTTP application without starting middleware or external clients."""
    app = FastAPI(title="TAP API", version="0.1.0", lifespan=lifespan)
    app.state.http_services = services or HttpServices()
    register_problem_handlers(app)

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

    app.include_router(knowledge_documents_router)
    app.include_router(knowledge_answers_router)
    app.include_router(citations_router)
    app.include_router(health_router)
    return app
