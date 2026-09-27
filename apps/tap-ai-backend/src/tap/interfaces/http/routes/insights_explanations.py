"""Project-scoped report-to-Tapper turns through the durable Conversation ledger."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from tap.contracts.http import (
    ConversationCreateRequest,
    InsightsExplanationAccepted,
    InsightsExplanationRequest,
    InsightsExplanationResult,
)
from tap.interfaces.http.dependencies import conversation_service
from tap.interfaces.http.routes.conversations import _input
from tap.interfaces.http.scope import project_authorization
from tap.modules.ai.ports.insights import InsightsAuthorizationChanged, InsightsQueryUnavailable
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationNotFound,
    ConversationService,
)

router = APIRouter(
    prefix="/insights/explanations",
    tags=["insights-explanations"],
    dependencies=[Depends(project_authorization("knowledge.answer"))],
)


@router.post(
    "",
    response_model=InsightsExplanationAccepted,
    status_code=202,
    operation_id="insights_explain_report",
)
async def explain(
    body: InsightsExplanationRequest,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=128),
    service: ConversationService = Depends(conversation_service),
) -> InsightsExplanationAccepted:
    if request.app.state.http_services.insights_explanation is None:
        raise HTTPException(status_code=503, detail="Insights explanation is unavailable")
    scope = request.state.project_scope
    material = "/".join((scope.enterprise_id, scope.project_id, scope.actor_id, idempotency_key))
    conversation_id = body.conversation_id or hashlib.sha256(material.encode()).hexdigest()[:32]
    turn_id = hashlib.sha256(
        (material + "/" + conversation_id + "/insights-turn").encode()
    ).hexdigest()[:32]
    replay = await service.replay(conversation_id, idempotency_key)
    if replay is not None:
        frozen = replay.input_snapshot.value
        if (
            frozen.message != body.question
            or frozen.actor_id != scope.actor_id
            or frozen.insights_query_id != body.query_id
            or frozen.insights_report_refs != tuple(body.resource_refs)
            or frozen.source_revision_ids != tuple(body.source_revision_ids)
            or frozen.document_revision_ids != tuple(body.document_revision_ids)
        ):
            raise ConversationConflict("idempotency-conflict")
        return InsightsExplanationAccepted(
            conversation_id=conversation_id, turn_id=replay.turn_id, state=replay.state
        )
    ordinary = await _input(
        ConversationCreateRequest(
            message=body.question,
            model_alias="tapper-chat",
            source_revision_ids=body.source_revision_ids,
            document_revision_ids=body.document_revision_ids,
        ),
        request,
    )
    value = replace(
        ordinary,
        insights_query_id=body.query_id,
        insights_report_refs=tuple(body.resource_refs),
    )
    if body.conversation_id is None:
        turn = await service.create(conversation_id, turn_id, idempotency_key, value)
    else:
        existing = await service.load(conversation_id)
        if not existing.turns or existing.turns[0].input_snapshot.value.actor_id != scope.actor_id:
            raise ConversationNotFound
        turn = await service.append(conversation_id, turn_id, idempotency_key, value)
    return InsightsExplanationAccepted(
        conversation_id=conversation_id, turn_id=turn.turn_id, state=turn.state
    )


@router.get(
    "/{conversation_id}/turns/{turn_id}",
    response_model=InsightsExplanationResult | InsightsExplanationAccepted,
    responses={202: {"model": InsightsExplanationAccepted}},
    operation_id="insights_get_explanation",
)
async def get_explanation(
    conversation_id: str,
    turn_id: str,
    request: Request,
    response: Response,
    service: ConversationService = Depends(conversation_service),
):
    runtime = request.app.state.http_services.insights_explanation
    if runtime is None:
        raise HTTPException(status_code=503, detail="Insights explanation is unavailable")
    conversation = await service.load(conversation_id)
    turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
    if (
        turn is None
        or turn.input_snapshot.value.insights_query_id is None
        or turn.input_snapshot.value.actor_id != request.state.project_scope.actor_id
    ):
        raise ConversationNotFound
    if turn.state in {"queued", "running"}:
        response.status_code = 202
        return InsightsExplanationAccepted(
            conversation_id=conversation_id, turn_id=turn_id, state=turn.state
        )
    if turn.state != "completed" or turn.answer_snapshot is None:
        raise HTTPException(status_code=503, detail="Insights explanation is unavailable")
    result = turn.answer_snapshot.value.insights_explanation
    if result is None:
        raise HTTPException(status_code=503, detail="Insights explanation is unavailable")
    try:
        await asyncio.wait_for(
            runtime.reauthorize_result(
                request.state.project_scope,
                turn.input_snapshot.value.insights_query_id,
                turn.input_snapshot.value.insights_report_refs,
                turn.input_snapshot.value.resolved_resources,
                result,
            ),
            timeout=20,
        )
    except InsightsAuthorizationChanged as exc:
        raise HTTPException(status_code=403, detail="Insights authorization changed") from exc
    except (InsightsQueryUnavailable, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="Insights explanation is unavailable") from exc
    return InsightsExplanationResult.model_validate(result)
