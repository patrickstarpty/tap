"""Governed knowledge review, publication, and withdrawal commands."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from tap.contracts.http import (
    KnowledgePublicationDetail,
    KnowledgePublicationPage,
    KnowledgePublishRequest,
    KnowledgeReviewDecisionPage,
    KnowledgeReviewDecisionRequest,
    KnowledgeReviewDetail,
    KnowledgeReviewHistoryPage,
    KnowledgeReviewInventory,
    KnowledgeReviewItemComparison,
    KnowledgeReviewOpenRequest,
    KnowledgeReviewPage,
    KnowledgeReviewSummary,
    PublishedKnowledgeSourcePage,
)
from tap.interfaces.http.dependencies import (
    HttpServices,
    knowledge_review_service,
    review_expected_version,
    source_command_key,
)
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.access.application.policy import require_authorized
from tap.modules.access.domain.authorization import ResourceRef
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

router = APIRouter(
    tags=["knowledge"],
    responses={
        code: problem_response_metadata("Knowledge review request failed")
        for code in (404, 409, 422, 503)
    },
)


@router.post(
    "/knowledge/documents/{document_id}/review",
    operation_id="knowledge_open_document_review",
    response_model=KnowledgeReviewDetail,
    dependencies=[
        Depends(project_authorization("knowledge.read", resource_id_param="document_id"))
    ],
)
async def open_document_review(
    request: Request,
    document_id: str,
    body: KnowledgeReviewOpenRequest,
    key: str = Depends(source_command_key),
) -> KnowledgeReviewDetail:
    service = knowledge_review_service(request)
    actual_review_id = await service.resolve_open_review(document_id, body.source_revision_id)
    services: HttpServices = request.app.state.http_services
    if services.authorization_policy is None:
        raise KnowledgeRuntimeUnavailable
    scope = request.state.project_scope
    await require_authorized(
        services.authorization_policy,
        scope,
        "knowledge.review.edit",
        ResourceRef(
            enterprise_id=scope.enterprise_id,
            project_id=scope.project_id,
            kind="knowledge-review",
            resource_id=actual_review_id,
        ),
    )
    return await service.open_review(document_id, body.source_revision_id, actual_review_id, key)


@router.get(
    "/knowledge/reviews",
    operation_id="knowledge_list_reviews",
    response_model=KnowledgeReviewPage,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def list_reviews(
    request: Request,
    source_revision_id: str | None = Query(
        default=None, alias="sourceRevisionId", min_length=1, max_length=256
    ),
    limit: int = Query(default=50, ge=1, le=100),
    after_review_id: str | None = Query(
        default=None, alias="afterReviewId", min_length=1, max_length=256
    ),
) -> KnowledgeReviewPage:
    return await knowledge_review_service(request).list_reviews(
        source_revision_id, limit, after_review_id
    )


@router.get(
    "/knowledge/reviews/{review_id}",
    operation_id="knowledge_get_review",
    response_model=KnowledgeReviewDetail,
    dependencies=[Depends(project_authorization("knowledge.read", resource_id_param="review_id"))],
)
async def get_review(request: Request, review_id: str) -> KnowledgeReviewDetail:
    return await knowledge_review_service(request).get_review(review_id)


@router.get(
    "/knowledge/reviews/{review_id}/inventory",
    operation_id="knowledge_list_review_inventory",
    response_model=KnowledgeReviewInventory,
    dependencies=[Depends(project_authorization("knowledge.read", resource_id_param="review_id"))],
)
async def get_review_inventory(
    request: Request,
    review_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    after_item_id: str | None = Query(
        default=None, alias="afterItemId", min_length=1, max_length=256
    ),
) -> KnowledgeReviewInventory:
    return await knowledge_review_service(request).get_review_inventory(
        review_id, limit, after_item_id
    )


@router.get(
    "/knowledge/reviews/{review_id}/decision-history",
    operation_id="knowledge_list_review_decision_history",
    response_model=KnowledgeReviewDecisionPage,
    dependencies=[Depends(project_authorization("knowledge.read", resource_id_param="review_id"))],
)
async def list_review_decision_history(
    request: Request,
    review_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    after_version: int | None = Query(default=None, alias="afterVersion", ge=1),
) -> KnowledgeReviewDecisionPage:
    return await knowledge_review_service(request).list_review_decision_history(
        review_id, limit, after_version
    )


@router.get(
    "/knowledge/reviews/{review_id}/history",
    operation_id="knowledge_list_review_history",
    response_model=KnowledgeReviewHistoryPage,
    dependencies=[Depends(project_authorization("knowledge.read", resource_id_param="review_id"))],
)
async def list_review_history(
    request: Request,
    review_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    after_version: int | None = Query(default=None, alias="afterVersion", ge=1),
) -> KnowledgeReviewHistoryPage:
    return await knowledge_review_service(request).list_review_history(
        review_id, limit, after_version
    )


@router.get(
    "/knowledge/reviews/{review_id}/publications",
    operation_id="knowledge_list_review_publications",
    response_model=KnowledgePublicationPage,
    dependencies=[Depends(project_authorization("knowledge.read", resource_id_param="review_id"))],
)
async def list_review_publications(
    request: Request,
    review_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    after_publication_id: str | None = Query(
        default=None, alias="afterPublicationId", min_length=1, max_length=256
    ),
) -> KnowledgePublicationPage:
    return await knowledge_review_service(request).list_review_publications(
        review_id, limit, after_publication_id
    )


@router.get(
    "/knowledge/reviews/{review_id}/items/{item_id}/comparison",
    operation_id="knowledge_compare_review_item",
    response_model=KnowledgeReviewItemComparison,
    dependencies=[
        Depends(project_authorization("knowledge.original.read", resource_id_param="review_id"))
    ],
)
async def compare_review_item(
    request: Request, review_id: str, item_id: str
) -> KnowledgeReviewItemComparison:
    return await knowledge_review_service(request).compare_review_item(review_id, item_id)


@router.put(
    "/knowledge/reviews/{review_id}/items/{item_id}/decision",
    operation_id="knowledge_update_review_item_decision",
    response_model=KnowledgeReviewDetail,
    dependencies=[
        Depends(project_authorization("knowledge.review.edit", resource_id_param="review_id"))
    ],
)
async def update_review_item_decision(
    request: Request,
    review_id: str,
    item_id: str,
    body: KnowledgeReviewDecisionRequest,
    expected_version: int = Depends(review_expected_version),
) -> KnowledgeReviewDetail:
    return await knowledge_review_service(request).update_item_decision(
        review_id,
        item_id,
        body.model_dump(by_alias=True, mode="json"),
        expected_version,
    )


@router.post(
    "/knowledge/reviews/{review_id}/return",
    operation_id="knowledge_return_review",
    response_model=KnowledgeReviewDetail,
    dependencies=[
        Depends(project_authorization("knowledge.review.edit", resource_id_param="review_id"))
    ],
)
async def return_review(
    request: Request,
    review_id: str,
    expected_version: int = Depends(review_expected_version),
) -> KnowledgeReviewDetail:
    return await knowledge_review_service(request).return_review(review_id, expected_version)


@router.post(
    "/knowledge/reviews/{review_id}/submit",
    operation_id="knowledge_submit_review",
    response_model=KnowledgeReviewDetail,
    dependencies=[
        Depends(project_authorization("knowledge.review.edit", resource_id_param="review_id"))
    ],
)
async def submit_review(
    request: Request,
    review_id: str,
    expected_version: int = Depends(review_expected_version),
) -> KnowledgeReviewDetail:
    return await knowledge_review_service(request).submit_review(review_id, expected_version)


@router.post(
    "/knowledge/reviews/{review_id}/approve",
    operation_id="knowledge_approve_review",
    response_model=KnowledgeReviewSummary,
    dependencies=[
        Depends(project_authorization("knowledge.review.approve", resource_id_param="review_id"))
    ],
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
    dependencies=[
        Depends(project_authorization("knowledge.publish", resource_id_param="review_id"))
    ],
)
async def publish_review(
    request: Request,
    review_id: str,
    body: KnowledgePublishRequest,
    expected_version: int = Depends(review_expected_version),
    key: str = Depends(source_command_key),
) -> KnowledgePublicationDetail:
    return await knowledge_review_service(request).publish_review(
        review_id, body.generation, expected_version, key
    )


@router.get(
    "/knowledge/publications/current",
    operation_id="knowledge_get_current_publication",
    response_model=KnowledgePublicationDetail,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def get_current_publication(request: Request) -> KnowledgePublicationDetail:
    return await knowledge_review_service(request).get_current_publication()


@router.get(
    "/knowledge/published-sources",
    operation_id="knowledge_list_published_sources",
    response_model=PublishedKnowledgeSourcePage,
    dependencies=[Depends(project_authorization("knowledge.read"))],
)
async def list_published_sources(request: Request) -> PublishedKnowledgeSourcePage:
    return await knowledge_review_service(request).list_published_sources()


@router.get(
    "/knowledge/publications/{publication_id}",
    operation_id="knowledge_get_publication",
    response_model=KnowledgePublicationDetail,
    dependencies=[
        Depends(project_authorization("knowledge.read", resource_id_param="publication_id"))
    ],
)
async def get_publication(request: Request, publication_id: str) -> KnowledgePublicationDetail:
    return await knowledge_review_service(request).get_publication(publication_id)


@router.post(
    "/knowledge/publications/{publication_id}/withdraw",
    operation_id="knowledge_withdraw_publication",
    response_model=KnowledgePublicationDetail,
    dependencies=[
        Depends(project_authorization("knowledge.publish", resource_id_param="publication_id"))
    ],
)
async def withdraw_publication(
    request: Request,
    publication_id: str,
    expected_version: int = Depends(review_expected_version),
    key: str = Depends(source_command_key),
) -> KnowledgePublicationDetail:
    return await knowledge_review_service(request).withdraw_publication(
        publication_id, expected_version, key
    )


@router.get(
    "/knowledge/reviews/{review_id}/items/{item_id}/original",
    operation_id="knowledge_read_review_original",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
    dependencies=[
        Depends(project_authorization("knowledge.original.read", resource_id_param="review_id"))
    ],
)
async def read_review_original(request: Request, review_id: str, item_id: str) -> Response:
    content, media_type = await knowledge_review_service(request).read_original(review_id, item_id)
    return Response(
        content,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )
