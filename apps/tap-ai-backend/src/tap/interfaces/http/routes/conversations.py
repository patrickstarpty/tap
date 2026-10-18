"""Project-scoped durable Conversation HTTP API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.contracts.http import (
    AnswerFeedbackRequest,
    AnswerFeedbackView,
    CitationPreview,
    ConversationAccepted,
    ConversationCreateRequest,
    ConversationDetail,
    ConversationEventItem,
    ConversationEventPage,
    ConversationPage,
    ConversationRenameRequest,
    ConversationResolvedResourceView,
    ConversationSummary,
    ConversationTurnInputView,
    ConversationTurnSummary,
    TurnTrace,
)
from tap.interfaces.http.conversation_selection import freeze_conversation_selection
from tap.interfaces.http.dependencies import (
    conversation_service,
    feedback_service,
    knowledge_service,
    trace_service,
)
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.interfaces.http.sse import encode_sse
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA, resolve_skill_selection
from tap.modules.ai.domain.assets import AssetRevisionRejected
from tap.modules.ai.domain.models import (
    ModelCapability,
    ModelGatewayRejected,
    ModelGatewayUnavailable,
    ModelNotSelectable,
    schema_digest,
)
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationNotFound,
    ConversationService,
)
from tap.modules.chat.application.feedback import FeedbackService
from tap.modules.chat.application.ports import DraftStream, DraftStreamUnavailable
from tap.modules.chat.domain.conversations import TurnInput
from tap.modules.chat.domain.feedback import AnswerFeedback
from tap.modules.knowledge.application.answers import DocumentStateChanged
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable
from tap.platform.telemetry import bind_trace, span, trace_id_of

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
        ModelGatewayUnavailable,
        ModelNotSelectable,
    ):
        return
    if current != turn.input_snapshot.value:
        raise ConversationConflict("idempotency-conflict")


async def _input(body: ConversationCreateRequest, request: Request) -> TurnInput:
    scope = request.state.project_scope
    services = request.app.state.http_services
    if services.model_catalog is not None:
        # Grounded and Agent answers use structured output, so only those models are selectable.
        selectable = {
            item.alias
            for item in await services.model_catalog.list_models(scope)
            if ModelCapability.STRUCTURED in item.capabilities
        }
        if body.model_alias not in selectable:
            raise ModelNotSelectable
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
    selection = await freeze_conversation_selection(
        services.knowledge,
        source_revision_ids=tuple(body.source_revision_ids),
        document_revision_ids=tuple(body.document_revision_ids),
    )
    return TurnInput(
        message=body.message,
        actor_id=scope.actor_id,
        identity_mode=scope.identity_mode.value,
        model_alias=body.model_alias,
        source_revision_ids=tuple(body.source_revision_ids),
        document_revision_ids=tuple(body.document_revision_ids),
        resolved_resources=selection.resolved_resources,
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
        acl_digest=selection.acl_digest,
        retrieval_policy_digest=selection.retrieval_policy_digest,
    )


def _turn(value, *, feedback: AnswerFeedback | None = None):
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
        graph_context_status=None
        if value.answer_snapshot is None
        else {
            "NOT_REQUESTED": "NOT_SELECTED",
            "REJECTED": "FAILED",
        }.get(
            value.answer_snapshot.value.graph_context_status.value,
            value.answer_snapshot.value.graph_context_status.value,
        ),
        graph_snapshot_id=None
        if value.answer_snapshot is None
        else value.answer_snapshot.value.graph_snapshot_id,
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
            insights_query_id=frozen.insights_query_id,
        ),
        trace_id=trace_id_of(value.traceparent),
        my_feedback=(
            None
            if feedback is None or feedback.rating is None
            else AnswerFeedbackView(rating=feedback.rating, comment=feedback.comment)
        ),
    )


def _summary(value):
    return ConversationSummary(
        conversation_id=value.conversation_id,
        title=value.title,
        created_at=value.created_at.isoformat(),
        updated_at=value.updated_at.isoformat(),
    )


def _detail(value, feedback_by_turn: dict[str, AnswerFeedback] | None = None):
    feedback_by_turn = feedback_by_turn or {}
    return ConversationDetail(
        conversation_id=value.conversation_id,
        title=value.title,
        created_at=value.created_at.isoformat(),
        updated_at=value.updated_at.isoformat(),
        turns=[_turn(turn, feedback=feedback_by_turn.get(turn.turn_id)) for turn in value.turns],
    )


@router.post(
    "",
    response_model=ConversationAccepted,
    status_code=202,
    operation_id="conversation_create",
    responses={
        404: problem_response_metadata("Approved revision unavailable"),
        409: problem_response_metadata("Idempotency conflict"),
        422: problem_response_metadata("Request validation failed or model not selectable"),
        503: problem_response_metadata("Runtime or model catalog unavailable"),
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
    with bind_trace(scope=request.state.project_scope, turn_id=turn_id):
        with span(
            "turn.request",
            {"tap.conversation_id": conversation_id, "tap.turn_id": turn_id},
        ):
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
    q: str | None = Query(
        None,
        max_length=120,
        description="Case-insensitive title substring; surrounding whitespace is ignored.",
    ),
    service: ConversationService = Depends(conversation_service),
):
    values, next_cursor = await service.list(limit=limit, cursor=cursor, query=q)
    return ConversationPage(items=[_summary(v) for v in values], next_cursor=next_cursor)


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    operation_id="conversation_get",
    responses={404: problem_response_metadata("Conversation not found")},
)
async def get(
    conversation_id: str,
    request: Request,
    service: ConversationService = Depends(conversation_service),
):
    conversation = await service.load(conversation_id)
    feedback_by_turn: dict[str, AnswerFeedback] = {}
    services = request.app.state.http_services
    if services.feedback is not None:
        feedback_by_turn = await services.feedback.mine(
            tuple(turn.turn_id for turn in conversation.turns)
        )
    return _detail(conversation, feedback_by_turn)


@router.patch(
    "/{conversation_id}",
    response_model=ConversationSummary,
    operation_id="conversation_rename",
    responses={
        403: problem_response_metadata("Only the creating actor may rename"),
        404: problem_response_metadata("Conversation not found"),
        422: problem_response_metadata("Request validation failed"),
    },
)
async def rename(
    conversation_id: str,
    body: ConversationRenameRequest,
    request: Request,
    service: ConversationService = Depends(conversation_service),
):
    return _summary(
        await service.rename(
            conversation_id, body.title, actor_id=request.state.project_scope.actor_id
        )
    )


@router.delete(
    "/{conversation_id}",
    status_code=204,
    response_class=Response,
    operation_id="conversation_delete",
    responses={
        403: problem_response_metadata("Only the creating actor may delete"),
        404: problem_response_metadata("Conversation not found"),
    },
)
async def delete(
    conversation_id: str,
    request: Request,
    service: ConversationService = Depends(conversation_service),
) -> Response:
    await service.delete(conversation_id, actor_id=request.state.project_scope.actor_id)
    return Response(status_code=204)


@router.post(
    "/{conversation_id}/turns",
    response_model=ConversationAccepted,
    status_code=202,
    operation_id="conversation_append_turn",
    responses={
        404: problem_response_metadata("Conversation or approved revision not found"),
        409: problem_response_metadata("Idempotency conflict"),
        422: problem_response_metadata("Request validation failed or model not selectable"),
        503: problem_response_metadata("Runtime or model catalog unavailable"),
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
    # Unknown or deleted Conversations fail before any input resolution work.
    await service.load(conversation_id)
    turn_id = uuid4().hex
    with bind_trace(scope=request.state.project_scope, turn_id=turn_id):
        with span(
            "turn.request",
            {"tap.conversation_id": conversation_id, "tap.turn_id": turn_id},
        ):
            turn = await service.append(
                conversation_id, turn_id, idempotency_key, await _input(body, request)
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


_TERMINAL_TURN_STATES = frozenset({"completed", "abstained", "failed", "canceled"})
_DRAFT_BLOCK_MS = 15_000
_DEGRADED_POLL_SECONDS = 1.0


def _draft_key(turn_id: str) -> str:
    return f"tap:turn:{turn_id}:draft"


def _wake_key(conversation_id: str) -> str:
    return f"tap:conv:{conversation_id}:events"


def _encode_draft_frame(
    *, conversation_id: str, turn_id: str, sequence: int, attempt: int, seq: int, text: str
) -> str:
    """An `answer.draft` SSE frame: still a `ChatEventEnvelope`, but never
    carries an `id:` line -- drafts are not persisted MySQL events, and the
    client orders/dedupes them by `attempt`/`seq`, never by `sequence`."""

    envelope = ChatEventEnvelope.model_validate(
        {
            "eventId": uuid4().hex,
            "sequence": sequence,
            "chatId": conversation_id,
            "turnId": turn_id,
            "occurredAt": datetime.now(timezone.utc).isoformat(),
            "schemaVersion": 1,
            "event": {
                "type": "answer.draft",
                "payload": {"attempt": attempt, "seq": seq, "text": text},
            },
        }
    ).model_dump(mode="json", by_alias=True)
    data = json.dumps(envelope, separators=(",", ":"))
    return f"event: answer.draft\ndata: {data}\n\n"


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
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID", pattern=r"^(0|[1-9][0-9]*)$"),
    service: ConversationService = Depends(conversation_service),
):
    resume = 0 if last_event_id is None else int(last_event_id)
    await service.load(conversation_id)
    services = request.app.state.http_services
    draft_stream: DraftStream | None = getattr(services, "draft_stream", None)

    async def generate():
        nonlocal resume
        degraded = draft_stream is None
        replayed_for: tuple[str, int] | None = None
        last_draft_id = "$"
        last_wake_id = "$"

        with span("conversation.stream", {"tap.conversation_id": conversation_id}) as current_span:
            if degraded:
                current_span.set_attribute("tap.stream.degraded", True)
            while True:
                try:
                    value = await service.load(conversation_id)
                except ConversationNotFound:
                    # Deleted while streaming: close the stream instead of failing mid-body.
                    return
                fresh = [event for event in value.events if event.sequence > resume]
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

                current_turn = next(
                    (
                        turn
                        for turn in reversed(value.turns)
                        if turn.state not in _TERMINAL_TURN_STATES
                    ),
                    None,
                )
                if current_turn is None:
                    # Every Turn is terminal and every authoritative event has
                    # been sent: close the stream (the client's reconnect
                    # semantics are unaffected -- there is nothing left to
                    # resume into).
                    return

                if degraded:
                    await asyncio.sleep(_DEGRADED_POLL_SECONDS)
                    continue

                assert draft_stream is not None  # degraded is False only when set
                replay_key = (current_turn.turn_id, current_turn.attempt)
                if replayed_for != replay_key:
                    try:
                        chunks = await draft_stream.replay(
                            current_turn.turn_id, attempt=current_turn.attempt
                        )
                    except DraftStreamUnavailable:
                        degraded = True
                        current_span.set_attribute("tap.stream.degraded", True)
                        continue
                    for chunk in chunks:
                        yield _encode_draft_frame(
                            conversation_id=conversation_id,
                            turn_id=current_turn.turn_id,
                            sequence=resume,
                            attempt=chunk.attempt,
                            seq=chunk.seq,
                            text=chunk.text,
                        )
                    replayed_for = replay_key
                    last_draft_id = "$"
                    last_wake_id = "$"

                draft_key = _draft_key(current_turn.turn_id)
                wake_key = _wake_key(conversation_id)
                try:
                    entries = await draft_stream.read(
                        {draft_key: last_draft_id, wake_key: last_wake_id},
                        block_ms=_DRAFT_BLOCK_MS,
                    )
                except DraftStreamUnavailable:
                    degraded = True
                    current_span.set_attribute("tap.stream.degraded", True)
                    continue
                if not entries:
                    # Block timed out with nothing new: keep the connection
                    # alive and let the top of the loop re-check MySQL once,
                    # without busy-polling Redis or MySQL in between.
                    yield ": keepalive\n\n"
                    continue
                for entry in entries:
                    if entry.key == draft_key:
                        last_draft_id = entry.id
                        attempt = entry.fields.get("attempt")
                        seq = entry.fields.get("seq")
                        text = entry.fields.get("text")
                        if attempt is None or seq is None or text is None:
                            continue
                        try:
                            yield _encode_draft_frame(
                                conversation_id=conversation_id,
                                turn_id=current_turn.turn_id,
                                sequence=resume,
                                attempt=int(attempt),
                                seq=int(seq),
                                text=text,
                            )
                        except (TypeError, ValueError):
                            continue
                    elif entry.key == wake_key:
                        last_wake_id = entry.id

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get(
    "/{conversation_id}/turns/{turn_id}/trace",
    response_model=TurnTrace,
    operation_id="conversation_turn_trace",
    responses={404: problem_response_metadata("Conversation, Turn, or trace not found")},
)
async def turn_trace(conversation_id: str, turn_id: str, request: Request) -> TurnTrace:
    trace = await trace_service(request).turn_trace(
        request.state.project_scope, conversation_id, turn_id
    )
    if trace is None:
        raise ConversationNotFound
    return trace


@router.put(
    "/{conversation_id}/turns/{turn_id}/feedback",
    response_model=AnswerFeedbackView,
    operation_id="conversation_put_turn_feedback",
    responses={
        404: problem_response_metadata("Conversation or Turn not found"),
        409: problem_response_metadata("Turn is not completed"),
        422: problem_response_metadata("Request validation failed"),
    },
)
async def put_turn_feedback(
    conversation_id: str,
    turn_id: str,
    body: AnswerFeedbackRequest,
    service: FeedbackService = Depends(feedback_service),
) -> AnswerFeedbackView:
    try:
        feedback = await service.submit(conversation_id, turn_id, body.rating, body.comment)
    except AuthorizationDenied:
        # Do not reveal that the Conversation exists but belongs to another actor.
        raise ConversationNotFound from None
    assert feedback.rating is not None  # submit() always stores a non-null rating
    return AnswerFeedbackView(rating=feedback.rating, comment=feedback.comment)


@router.delete(
    "/{conversation_id}/turns/{turn_id}/feedback",
    status_code=204,
    response_class=Response,
    operation_id="conversation_delete_turn_feedback",
    responses={404: problem_response_metadata("Conversation or Turn not found")},
)
async def delete_turn_feedback(
    conversation_id: str,
    turn_id: str,
    service: FeedbackService = Depends(feedback_service),
) -> Response:
    try:
        await service.retract(conversation_id, turn_id)
    except AuthorizationDenied:
        raise ConversationNotFound from None
    return Response(status_code=204)
