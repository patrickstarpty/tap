"""Project-scoped model-call detail HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from tap.contracts.http import ModelCallDetail
from tap.interfaces.http.dependencies import trace_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.chat.application.conversations import ConversationNotFound

router = APIRouter(
    prefix="/model-calls",
    tags=["model-calls"],
    dependencies=[Depends(project_authorization("knowledge.answer"))],
    responses={422: problem_response_metadata("Request validation failed")},
)


@router.get(
    "/{call_id}",
    response_model=ModelCallDetail,
    operation_id="model_call_detail",
    responses={404: problem_response_metadata("Model call not found")},
)
async def model_call_detail(call_id: str, request: Request) -> ModelCallDetail:
    detail = await trace_service(request).model_call(request.state.project_scope, call_id)
    if detail is None:
        raise ConversationNotFound
    return detail
