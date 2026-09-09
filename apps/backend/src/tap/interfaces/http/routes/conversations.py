"""Project-scoped durable Conversation HTTP API."""

from __future__ import annotations

import asyncio
import hashlib
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.contracts.http import (
    CitationPreview,
    ConversationAccepted,
    ConversationCreateRequest,
    ConversationDetail,
    ConversationEventItem,
    ConversationEventPage,
    ConversationPage,
    ConversationResolvedResourceView,
    ConversationSummary,
    ConversationTurnInputView,
    ConversationTurnSummary,
)
from tap.interfaces.http.dependencies import conversation_service, knowledge_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.interfaces.http.sse import encode_sse
from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA, resolve_skill_selection
from tap.modules.ai.domain.assets import AssetRevisionRejected
from tap.modules.ai.domain.models import ModelGatewayRejected, schema_digest
from tap.modules.chat.application.conversations import ConversationConflict, ConversationService
from tap.modules.chat.domain.conversations import FrozenResource, TurnInput, content_digest
from tap.modules.knowledge.application.answers import DocumentStateChanged
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
    dependencies=[Depends(project_authorization("knowledge.answer"))],
    responses={422: problem_response_metadata("Request validation failed")},
)


def _matches_replay(turn, body: ConversationCreateRequest) -> bool:
    value = turn.input_snapshot.value
    return (
        value.message == body.message
        and value.model_alias == body.model_alias
        and value.source_revision_ids == tuple(body.source_revision_ids)
        and value.document_revision_ids == tuple(body.document_revision_ids)
        and value.agent_revision_id == body.agent_revision_id
        and value.skill_revision_ids == tuple(body.skill_revision_ids)
    )


async def _validate_replay(turn, body: ConversationCreateRequest, request: Request) -> None:
    if not _matches_replay(turn, body):
        raise ConversationConflict("idempotency-conflict")
    try:
        current = await _input(body, request)
    except (
        AssetRevisionRejected,
        DocumentStateChanged,
        KnowledgeRuntimeUnavailable,
        ModelGatewayRejected,
    ):
        return
    if current != turn.input_snapshot.value:
        raise ConversationConflict("idempotency-conflict")


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
        agent = await services.asset_catalog.resolve_agent(
            scope,
            body.agent_revision_id,
            tools=frozenset({"knowledge.answer"}),
            output_schema_digest=schema_digest(VALIDATION_OUTPUT_SCHEMA),
        )
        agent_digest = agent.content_digest
    skills = (
        []
        if services.asset_catalog is None
        else [
            resolve_skill_selection(
                await services.asset_catalog.get_skill(scope, revision_id),
                task="knowledge.answer",
            )
            for revision_id in body.skill_revision_ids
        ]
    )
    if body.skill_revision_ids and services.asset_catalog is None:
        raise KnowledgeRuntimeUnavailable
    if services.knowledge is None:
        raise KnowledgeRuntimeUnavailable
    revisions, policy = await services.knowledge.resolve_conversation_selection(
        tuple((*body.source_revision_ids, *body.document_revision_ids))
    )
    return TurnInput(
        message=body.message,
        actor_id=scope.actor_id,
        identity_mode=scope.identity_mode.value,
        model_alias=body.model_alias,
        source_revision_ids=tuple(body.source_revision_ids),
        document_revision_ids=tuple(body.document_revision_ids),
        resolved_resources=tuple(
            FrozenResource(
                source_id=item.source_id or item.document_id,
                document_id=item.document_id,
                revision_id=item.revision_id,
                source_content_hash=item.source_content_hash,
                source_revision_id=(
                    item.revision_id if item.revision_id in body.source_revision_ids else None
                ),
                document_revision_id=item.revision_id,
                label=(
                    getattr(item, "source_name", None)
                    or getattr(item, "filename", None)
                    or item.source_id
                    or item.document_id
                ),
            )
            for item in revisions
        ),
        agent_revision_id=body.agent_revision_id,
        agent_revision_digest=agent_digest,
        agent_label=(
            None
            if body.agent_revision_id is None
            else getattr(agent, "display_name", body.agent_revision_id)
        ),
        skill_revision_ids=tuple(body.skill_revision_ids),
        skill_revision_digests=tuple(item.content_digest for item in skills),
        skill_labels=tuple(
            getattr(item, "display_name", revision_id)
            for item, revision_id in zip(skills, body.skill_revision_ids, strict=True)
        ),
        agent_system_instruction=(
            None if body.agent_revision_id is None else agent.system_instruction
        ),
        agent_system_instruction_digest=None
        if body.agent_revision_id is None
        else agent.system_instruction_digest,
        agent_tool_allowlist=()
        if body.agent_revision_id is None
        else tuple(sorted(agent.tool_allowlist)),
        agent_output_schema_json=None
        if body.agent_revision_id is None
        else agent.output_schema_json,
        agent_output_schema_digest=None
        if body.agent_revision_id is None
        else agent.output_schema_digest,
        skill_instruction_templates=tuple(cast(str, item.instruction_template) for item in skills),
        skill_instruction_template_digests=tuple(
            item.instruction_template_digest for item in skills
        ),
        acl_digest=policy.acl_digest,
        retrieval_policy_digest=content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        ),
    )


def _turn(value):
    frozen = value.input_snapshot.value
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
        input=ConversationTurnInputView(
            message=frozen.message,
            model_alias=frozen.model_alias,
            source_revision_ids=list(frozen.source_revision_ids),
            document_revision_ids=list(frozen.document_revision_ids),
            resolved_resources=[
                ConversationResolvedResourceView(
                    source_id=item.source_id,
                    document_id=item.document_id,
                    source_revision_id=item.source_revision_id,
                    document_revision_id=item.document_revision_id or item.revision_id,
                    label=item.label or item.source_id,
                )
                for item in frozen.resolved_resources
            ],
            agent_revision_id=frozen.agent_revision_id,
            agent_label=frozen.agent_label or frozen.agent_revision_id,
            skill_revision_ids=list(frozen.skill_revision_ids),
            skill_labels=list(frozen.skill_labels or frozen.skill_revision_ids),
        ),
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
    replay = await service.replay(conversation_id, idempotency_key)
    if replay is not None:
        await _validate_replay(replay, body, request)
        return ConversationAccepted(
            conversation_id=conversation_id, turn_id=replay.turn_id, state="queued"
        )
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
    replay = await service.replay(conversation_id, idempotency_key)
    if replay is not None:
        await _validate_replay(replay, body, request)
        return ConversationAccepted(
            conversation_id=conversation_id, turn_id=replay.turn_id, state="queued"
        )
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
    "/{conversation_id}/turns/{turn_id}/citations/{citation_id}",
    response_model=CitationPreview,
    operation_id="conversation_get_citation",
    responses={
        404: problem_response_metadata("Conversation Turn citation not found"),
        409: problem_response_metadata("Citation evidence is stale"),
        503: problem_response_metadata("Citation evidence unavailable"),
    },
)
async def citation(
    request: Request,
    conversation_id: str,
    turn_id: str,
    citation_id: str,
    service: ConversationService = Depends(conversation_service),
):
    await service.authorize_citation(conversation_id, turn_id, citation_id)
    return await knowledge_service(request).historical_citation(citation_id)


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
                turn_id=e.turn_id or str(e.payload.get("turnId") or ""),
                event_type=e.event_type,
                payload=dict(e.payload),
                occurred_at=e.occurred_at.isoformat(),
            )
            for e in value.events
        ]
    )


@router.get(
    "/{conversation_id}/stream",
    response_class=StreamingResponse,
    response_model=ChatEventEnvelope,
    operation_id="conversation_stream",
    responses={
        404: problem_response_metadata("Conversation not found"),
        422: problem_response_metadata("Invalid Last-Event-ID"),
        200: {
            "description": "Recoverable conversation event stream",
            "content": {
                "text/event-stream": {"schema": {"$ref": "#/components/schemas/ChatEventEnvelope"}}
            },
        },
    },
)
async def stream(
    conversation_id: str,
    last_event_id: str | None = Header(None, alias="Last-Event-ID", pattern=r"^(0|[1-9][0-9]*)$"),
    service: ConversationService = Depends(conversation_service),
):
    resume = 0 if last_event_id is None else int(last_event_id)

    async def generate():
        nonlocal resume
        idle = 0
        while idle < 10:
            value = await service.load(conversation_id)
            fresh = [event for event in value.events if event.sequence > resume]
            if fresh:
                idle = 0
                for event in fresh:
                    turn_id = event.turn_id or str(event.payload.get("turnId") or "")
                    if not turn_id:
                        raise ValueError("persisted event is missing its Turn binding")
                    envelope = ChatEventEnvelope.model_validate(
                        {
                            "eventId": event.event_id,
                            "sequence": event.sequence,
                            "chatId": conversation_id,
                            "turnId": turn_id,
                            "occurredAt": event.occurred_at.isoformat(),
                            "schemaVersion": 1,
                            "event": {"type": event.event_type, "payload": dict(event.payload)},
                        }
                    ).model_dump(mode="json", by_alias=True)
                    yield encode_sse((envelope,), last_event_id=str(resume))
                    resume = event.sequence
            else:
                idle += 1
                await asyncio.sleep(0.1)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
