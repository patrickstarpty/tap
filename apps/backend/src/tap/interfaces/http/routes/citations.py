"""Public citation-preview route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from tap.contracts.http import CitationPreview
from tap.interfaces.http.dependencies import knowledge_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization

router = APIRouter(prefix="/knowledge/citations", tags=["knowledge"])


@router.get(
    "/{citation_id}",
    operation_id="citation_get_preview",
    dependencies=[Depends(project_authorization("knowledge.read"))],
    response_model=CitationPreview,
    responses={
        status.HTTP_404_NOT_FOUND: problem_response_metadata("Citation stale"),
        status.HTTP_422_UNPROCESSABLE_ENTITY: problem_response_metadata("Invalid citation ID"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_response_metadata(
            "Knowledge runtime unavailable"
        ),
    },
)
async def get_citation(request: Request, citation_id: str) -> CitationPreview:
    return await knowledge_service(request).citation(citation_id)
