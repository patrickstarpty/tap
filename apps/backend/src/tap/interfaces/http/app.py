"""FastAPI application factory used for deterministic OpenAPI export."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import APIRouter, Depends, FastAPI, Path, Request, status
from fastapi.routing import APIRoute

from tap.contracts.http import ChatTurnAccepted, ChatTurnRequest, RuntimeMode
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.middleware.origin import OriginPolicyMiddleware, validate_origins
from tap.interfaces.http.problems import (
    problem_response,
    problem_response_metadata,
    register_problem_handlers,
)
from tap.interfaces.http.routes.citations import router as citations_router
from tap.interfaces.http.routes.health import router as health_router
from tap.interfaces.http.routes.knowledge_answers import router as knowledge_answers_router
from tap.interfaces.http.routes.knowledge_documents import router as knowledge_documents_router
from tap.interfaces.http.scope import resolve_project_scope
from tap.modules.access.domain.context import IdentityMode
from tap.modules.access.domain.policy import AuthorizationDenied

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    services: HttpServices | None = None,
    *,
    lifespan: Lifespan | None = None,
    validation_mode: bool = False,
    allowed_origins: frozenset[str] = frozenset(),
) -> FastAPI:
    """Build the HTTP application without starting middleware or external clients."""
    app = FastAPI(title="TAP API", version="0.1.0", lifespan=lifespan)
    app.state.http_services = services or HttpServices()
    validate_origins(allowed_origins)
    app.state.validation_mode = validation_mode
    app.add_middleware(OriginPolicyMiddleware, allowed_origins=allowed_origins)
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
    async def create_chat_turn(
        chat_id: str, request: ChatTurnRequest, http_request: Request
    ) -> ChatTurnAccepted:
        """Reserve the public route until the durable turn workflow is implemented."""
        del chat_id, request
        return problem_response("turn-not-implemented", http_request)  # type: ignore[return-value]

    @app.get(
        "/api/v1/runtime-mode",
        operation_id="runtime_get_mode",
        response_model=RuntimeMode,
        responses={
            403: problem_response_metadata("Authorization denied"),
            503: problem_response_metadata("Runtime unavailable"),
        },
    )
    async def runtime_mode(request: Request) -> RuntimeMode:
        scope = await resolve_project_scope(request)
        if scope.identity_mode is not IdentityMode.VALIDATION:
            raise AuthorizationDenied("unsupported-runtime-mode")
        return RuntimeMode(
            mode="validation",
            project_id=scope.project_id,
            actor_id=scope.actor_id,
            identity_mode="validation",
        )

    async def project_path(project_id: str = Path(min_length=1, max_length=128)) -> str:
        return project_id

    for router in (knowledge_documents_router, knowledge_answers_router, citations_router):
        app.include_router(
            router,
            prefix="/api/v1/projects/{project_id}",
            dependencies=[Depends(project_path)],
            responses={
                403: problem_response_metadata("Project scope or authorization denied"),
            },
        )
        if validation_mode:
            aliases = APIRouter()
            aliases.include_router(router)
            for route in aliases.routes:
                if isinstance(route, APIRoute) and route.operation_id is not None:
                    route.operation_id = "validation_" + route.operation_id
            app.include_router(
                aliases,
                prefix="/v1",
                deprecated=True,
                responses={
                    403: problem_response_metadata("Project scope or authorization denied"),
                },
            )
    if validation_mode:
        citation_route = citations_router.routes[0]
        assert isinstance(citation_route, APIRoute)
        app.add_api_route(
            "/v1/citations/{citation_id}",
            citation_route.endpoint,
            methods=["GET"],
            response_model=citation_route.response_model,
            operation_id="validation_legacy_citation_get_preview",
            deprecated=True,
            dependencies=citation_route.dependencies,
            tags=citation_route.tags,
            responses=citation_route.responses
            | {
                403: problem_response_metadata("Project scope or authorization denied"),
            },
        )
    app.include_router(health_router)
    return app
