"""One-use, bounded memory-only input delivery. Never written to disk."""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Any


class InputVault:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[str, str, datetime, dict[str, Any]]] = {}

    def put(
        self,
        project_id: str,
        session_id: str,
        payload: dict[str, Any],
        *,
        expires_at: str | None,
    ) -> str:
        deadline = datetime.now(UTC) + timedelta(seconds=120)
        if expires_at:
            deadline = min(deadline, datetime.fromisoformat(expires_at))
        with self._lock:
            self.purge_expired()
            if len(self._entries) >= 1024 or deadline <= datetime.now(UTC):
                raise ValueError("ephemeral browser input unavailable")
            reference = secrets.token_hex(32)
            self._entries[reference] = (project_id, session_id, deadline, dict(payload))
            return reference

    def consume(
        self, project_id: str, session_id: str, reference: str
    ) -> dict[str, Any]:
        with self._lock:
            self.purge_expired()
            found = self._entries.get(reference)
            if found is None or found[:2] != (project_id, session_id):
                raise ValueError("ephemeral browser input unavailable")
            self._entries.pop(reference)
            return found[3]

    def discard(self, reference: str) -> None:
        with self._lock:
            self._entries.pop(reference, None)

    def discard_session(self, project_id: str, session_id: str) -> None:
        with self._lock:
            for reference, entry in tuple(self._entries.items()):
                if entry[:2] == (project_id, session_id):
                    self._entries.pop(reference)

    def purge_expired(self) -> None:
        with self._lock:
            for reference, entry in tuple(self._entries.items()):
                if entry[2] <= datetime.now(UTC):
                    self._entries.pop(reference)
