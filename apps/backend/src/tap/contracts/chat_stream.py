"""Public SSE event models, deliberately separate from the HTTP DTO graph."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class StreamContractModel(BaseModel):
    """Base model for browser-visible stream payloads."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class TurnStartedPayload(StreamContractModel):
    state: Literal["running"]


class TurnStartedEvent(StreamContractModel):
    type: Literal["turn.started"]
    payload: TurnStartedPayload


class AnswerDeltaPayload(StreamContractModel):
    text: str


class AnswerDeltaEvent(StreamContractModel):
    type: Literal["answer.delta"]
    payload: AnswerDeltaPayload


class TurnCompletedPayload(StreamContractModel):
    answer: str


class TurnCompletedEvent(StreamContractModel):
    type: Literal["turn.completed"]
    payload: TurnCompletedPayload


class TurnAbstainedPayload(StreamContractModel):
    reason: str


class TurnAbstainedEvent(StreamContractModel):
    type: Literal["turn.abstained"]
    payload: TurnAbstainedPayload


class TurnCanceledPayload(StreamContractModel):
    partial_answer_retained: bool


class TurnCanceledEvent(StreamContractModel):
    type: Literal["turn.canceled"]
    payload: TurnCanceledPayload


class TurnFailedPayload(StreamContractModel):
    code: str
    retryable: bool


class TurnFailedEvent(StreamContractModel):
    type: Literal["turn.failed"]
    payload: TurnFailedPayload


ChatStreamEvent = Annotated[
    TurnStartedEvent
    | AnswerDeltaEvent
    | TurnCompletedEvent
    | TurnAbstainedEvent
    | TurnCanceledEvent
    | TurnFailedEvent,
    Field(discriminator="type"),
]


class ChatEventEnvelope(StreamContractModel):
    """A recoverable, ordered event persisted for one chat turn."""

    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    chat_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    event: ChatStreamEvent
