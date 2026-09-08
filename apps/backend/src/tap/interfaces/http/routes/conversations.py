"""Project-scoped durable Conversation HTTP API."""

from __future__ import annotations

import hashlib
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response

from tap.contracts.http import (
    ConversationAccepted,
    ConversationCreateRequest,
    ConversationDetail,
    ConversationEventItem,
    ConversationEventPage,
    ConversationPage,
    ConversationSummary,
    ConversationTurnSummary,
)
from tap.interfaces.http.dependencies import conversation_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.interfaces.http.sse import encode_sse
from tap.modules.ai.domain.models import ModelGatewayRejected
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import TurnInput, content_digest
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
    dependencies=[Depends(project_authorization("knowledge.answer"))],
)


async def _input(body: ConversationCreateRequest, request: Request) -> TurnInput:
    scope = request.state.project_scope
    services = request.app.state.http_services
    if services.model_catalog is not None:
        aliases = {item.alias for item in await services.model_catalog.list_models(scope)}
        if body.model_alias not in aliases:
            raise ModelGatewayRejected
    elif body.model_alias:
        raise KnowledgeRuntimeUnavailable
    agent_digest = None
    if body.agent_revision_id is not None:
        if services.asset_catalog is None:
            raise KnowledgeRuntimeUnavailable
        agent = await services.asset_catalog.get_agent(scope, body.agent_revision_id)
        agent_digest = agent.content_digest
    skills = (
        []
        if services.asset_catalog is None
        else [
            await services.asset_catalog.get_skill(scope, revision_id)
            for revision_id in body.skill_revision_ids
        ]
    )
    if body.skill_revision_ids and services.asset_catalog is None:
        raise KnowledgeRuntimeUnavailable
    return TurnInput(
        message=body.message,
        actor_id=scope.actor_id,
        identity_mode=scope.identity_mode.value,
        model_alias=body.model_alias,
        source_revision_ids=tuple(body.source_revision_ids),
        document_revision_ids=tuple(body.document_revision_ids),
        agent_revision_id=body.agent_revision_id,
        agent_revision_digest=agent_digest,
        skill_revision_ids=tuple(body.skill_revision_ids),
        skill_revision_digests=tuple(item.content_digest for item in skills),
        retrieval_policy_digest=content_digest("tapper-demo-policy-v1"),
    )


def _turn(value):
    return ConversationTurnSummary(
        turn_id=value.turn_id,
        state=value.state,
        attempt=value.attempt,
        input_snapshot_digest=value.input_snapshot.digest,
        answer_evidence_snapshot_id=None
        if value.answer_snapshot is None
        else value.answer_snapshot.snapshot_id,
        answer_evidence_snapshot_digest=None
        if value.answer_snapshot is None
        else value.answer_snapshot.digest,
    )


def _detail(value):
    return ConversationDetail(
        conversation_id=value.conversation_id,
        title=value.title,
        created_at=value.created_at.isoformat(),
        updated_at=value.updated_at.isoformat(),
        turns=[_turn(turn) for turn in value.turns],
    )


@router.post(
    "",
    response_model=ConversationAccepted,
    status_code=202,
    operation_id="conversation_create",
    responses={
        404: problem_response_metadata("Approved revision unavailable"),
        409: problem_response_metadata("Idempotency conflict"),
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Runtime unavailable"),
    },
)
async def create(
    body: ConversationCreateRequest,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=128),
    service: ConversationService = Depends(conversation_service),
):
    material = "/".join(
        (
            request.state.project_scope.enterprise_id,
            request.state.project_scope.project_id,
            idempotency_key,
        )
    )
    conversation_id = hashlib.sha256(material.encode()).hexdigest()[:32]
    turn_id = hashlib.sha256((material + "/first-turn").encode()).hexdigest()[:32]
    turn = await service.create(
        conversation_id, turn_id, idempotency_key, await _input(body, request)
    )
    return ConversationAccepted(
        conversation_id=conversation_id, turn_id=turn.turn_id, state="queued"
    )


@router.get(
    "",
    response_model=ConversationPage,
    operation_id="conversation_list",
    responses={422: problem_response_metadata("Invalid cursor")},
)
async def list_conversations(
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = None,
    service: ConversationService = Depends(conversation_service),
):
    values, next_cursor = await service.list(limit=limit, cursor=cursor)
    return ConversationPage(
        items=[
            ConversationSummary(
                conversation_id=v.conversation_id,
                title=v.title,
                created_at=v.created_at.isoformat(),
                updated_at=v.updated_at.isoformat(),
            )
            for v in values
        ],
        next_cursor=next_cursor,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    operation_id="conversation_get",
    responses={404: problem_response_metadata("Conversation not found")},
)
async def get(conversation_id: str, service: ConversationService = Depends(conversation_service)):
    return _detail(await service.load(conversation_id))


@router.post(
    "/{conversation_id}/turns",
    response_model=ConversationAccepted,
    status_code=202,
    operation_id="conversation_append_turn",
    responses={
        404: problem_response_metadata("Conversation or approved revision not found"),
        409: problem_response_metadata("Idempotency conflict"),
        422: problem_response_metadata("Request validation failed"),
    },
)
async def append(
    conversation_id: str,
    body: ConversationCreateRequest,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=128),
    service: ConversationService = Depends(conversation_service),
):
    turn = await service.append(
        conversation_id, uuid4().hex, idempotency_key, await _input(body, request)
    )
    return ConversationAccepted(
        conversation_id=conversation_id, turn_id=turn.turn_id, state="queued"
    )


@router.post(
    "/{conversation_id}/turns/{turn_id}/cancel",
    response_model=ConversationTurnSummary,
    operation_id="conversation_cancel_turn",
    responses={404: problem_response_metadata("Conversation or Turn not found")},
)
async def cancel(
    conversation_id: str, turn_id: str, service: ConversationService = Depends(conversation_service)
):
    return _turn(await service.cancel(conversation_id, turn_id))


@router.get(
    "/{conversation_id}/events",
    response_model=ConversationEventPage,
    operation_id="conversation_list_events",
    responses={404: problem_response_metadata("Conversation not found")},
)
async def events(
    conversation_id: str, service: ConversationService = Depends(conversation_service)
):
    value = await service.load(conversation_id)
    return ConversationEventPage(
        items=[
            ConversationEventItem(
                event_id=e.event_id,
                sequence=e.sequence,
                event_type=e.event_type,
                payload=dict(e.payload),
                occurred_at=e.occurred_at.isoformat(),
            )
            for e in value.events
        ]
    )


@router.get(
    "/{conversation_id}/stream",
    response_class=Response,
    operation_id="conversation_stream",
    responses={
        404: problem_response_metadata("Conversation not found"),
        422: problem_response_metadata("Invalid Last-Event-ID"),
    },
)
async def stream(
    conversation_id: str,
    last_event_id: str | None = Header(None, alias="Last-Event-ID", pattern=r"^(0|[1-9][0-9]*)$"),
    service: ConversationService = Depends(conversation_service),
):
    value = await service.load(conversation_id)
    body = encode_sse(
        (
            {"sequence": e.sequence, "eventType": e.event_type, "payload": dict(e.payload)}
            for e in value.events
        ),
        last_event_id=last_event_id,
    )
    return Response(body, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
