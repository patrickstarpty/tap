"""FastAPI application factory used for deterministic OpenAPI export."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, status

from tap.contracts.http import ChatTurnAccepted, ChatTurnRequest


def create_app() -> FastAPI:
    """Build the HTTP application without starting middleware or external clients."""
    app = FastAPI(title="TAP API", version="0.1.0")

    @app.post(
        "/v1/chats/{chat_id}/turns",
        operation_id="chat_create_turn",
        response_model=ChatTurnAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_chat_turn(chat_id: str, request: ChatTurnRequest) -> ChatTurnAccepted:
        """Reserve the public route until the durable turn workflow is implemented."""
        del chat_id, request
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)

    return app
