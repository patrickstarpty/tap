"""Governed knowledge review, publication, and withdrawal commands."""

from fastapi import APIRouter, Depends, Request

from tap.contracts.http import (
    KnowledgePublicationDetail,
    KnowledgePublishRequest,
    KnowledgeReviewSummary,
)
from tap.interfaces.http.dependencies import (
    knowledge_review_service,
    review_expected_version,
    source_command_key,
)
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization

router = APIRouter(
    tags=["knowledge"],
    responses={
        code: problem_response_metadata("Knowledge review request failed")
        for code in (404, 409, 422, 503)
    },
)


@router.post(
    "/knowledge/reviews/{review_id}/approve",
    operation_id="knowledge_approve_review",
    response_model=KnowledgeReviewSummary,
    dependencies=[Depends(project_authorization("knowledge.review.approve"))],
)
async def approve_review(
    request: Request,
    review_id: str,
    expected_version: int = Depends(review_expected_version),
) -> KnowledgeReviewSummary:
    return await knowledge_review_service(request).approve_review(review_id, expected_version)


@router.post(
    "/knowledge/reviews/{review_id}/publish",
    operation_id="knowledge_publish_review",
    response_model=KnowledgePublicationDetail,
    dependencies=[Depends(project_authorization("knowledge.publish"))],
)
async def publish_review(
    request: Request,
    review_id: str,
    body: KnowledgePublishRequest,
    key: str = Depends(source_command_key),
) -> KnowledgePublicationDetail:
    return await knowledge_review_service(request).publish_review(review_id, body.generation, key)


@router.post(
    "/knowledge/publications/{publication_id}/withdraw",
    operation_id="knowledge_withdraw_publication",
    response_model=KnowledgePublicationDetail,
    dependencies=[Depends(project_authorization("knowledge.publish"))],
)
async def withdraw_publication(
    request: Request,
    publication_id: str,
    key: str = Depends(source_command_key),
) -> KnowledgePublicationDetail:
    return await knowledge_review_service(request).withdraw_publication(publication_id, key)
