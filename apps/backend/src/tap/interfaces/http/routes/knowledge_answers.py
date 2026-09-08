"""Public grounded-answer route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from tap.contracts.http import RetrievalAnswerRequest, RetrievalAnswerResponse
from tap.interfaces.http.dependencies import knowledge_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post(
    "/answers",
    operation_id="knowledge_create_answer",
    dependencies=[Depends(project_authorization("knowledge.answer"))],
    response_model=RetrievalAnswerResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: problem_response_metadata("Invalid answer selection"),
        status.HTTP_409_CONFLICT: problem_response_metadata("Document state changed"),
        status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata("Invalid answer request"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata(
            "Knowledge runtime unavailable"
        ),
    },
)
async def create_answer(
    request: Request, answer_request: RetrievalAnswerRequest
) -> RetrievalAnswerResponse:
    return await knowledge_service(request).answer(answer_request)
