"""Project-scoped, provider-free model catalog."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from tap.contracts.http import ModelCatalogItem, ModelCatalogPage
from tap.interfaces.http.dependencies import model_catalog_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get(
    "/models",
    operation_id="ai_list_models",
    response_model=ModelCatalogPage,
    responses={503: problem_response_metadata("Model catalog unavailable")},
    dependencies=[Depends(project_authorization("ai.models.read"))],
)
async def list_models(request: Request) -> ModelCatalogPage:
    scope = request.state.project_scope
    models = await model_catalog_service(request).list_models(scope)
    return ModelCatalogPage(
        default_alias=model_catalog_service(request).default_alias,
        items=[
            ModelCatalogItem(
                alias=model.alias,
                display_name=model.display_name,
                capabilities=sorted(capability.value for capability in model.capabilities),
            )
            for model in models
        ],
    )
