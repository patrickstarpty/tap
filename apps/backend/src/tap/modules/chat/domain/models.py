"""Framework-free domain values for durable Knowledge Chat turns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import NewType

ChatId = NewType("ChatId", str)
TurnId = NewType("TurnId", str)
CommandId = NewType("CommandId", str)
EventId = NewType("EventId", str)


class TurnState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    ABSTAINED = "abstained"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Turn:
    turn_id: TurnId
    chat_id: ChatId
    client_request_id: str
    message: str
    state: TurnState
    last_sequence: int
    created_at: datetime
