"""Project-scoped Prompt Suggestions HTTP API."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from tap.contracts.http import PromptSuggestionItem, PromptSuggestionPage, PromptSuggestionSource
from tap.interfaces.http.dependencies import prompt_suggestion_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.chat.application.suggestions import PromptSuggestionService
from tap.modules.chat.domain.suggestions import SuggestionKey

router = APIRouter(
    prefix="/prompt-suggestions",
    tags=["prompt-suggestions"],
    dependencies=[Depends(project_authorization("knowledge.answer"))],
    responses={422: problem_response_metadata("Request validation failed")},
)


@router.get(
    "",
    response_model=PromptSuggestionPage,
    operation_id="prompt_suggestion_list",
)
async def list_prompt_suggestions(
    request: Request,
    locale: Literal["en", "zh"] = Query(...),
    service: PromptSuggestionService = Depends(prompt_suggestion_service),
):
    key = SuggestionKey(actor_id=request.state.project_scope.actor_id, locale=locale)
    views = await service.list(key)
    return PromptSuggestionPage(
        items=[
            PromptSuggestionItem(
                id=view.suggestion_id,
                question=view.question,
                sources=[
                    PromptSuggestionSource(source_id=source.source_id, name=source.name)
                    for source in view.sources
                ],
            )
            for view in views
        ]
    )
