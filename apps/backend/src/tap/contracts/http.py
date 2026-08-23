"""Public HTTP DTOs for the first Knowledge Chat contract slice."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    """Base model that exposes camelCase JSON without accepting unknown fields."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ChatTurnRequest(ContractModel):
    """A browser request to create one turn in an existing chat."""

    client_request_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    answer_mode: Literal["quick", "deep"] = "quick"
    source_scope: list[str] | None = None
    resource_refs: list[str] | None = None
    requested_environment: str | None = None
    requested_corpus_version: str | None = None


class ChatTurnAccepted(ContractModel):
    """The durable identity returned after a turn has been accepted for processing."""

    chat_id: str
    turn_id: str
    state: Literal["queued"]
