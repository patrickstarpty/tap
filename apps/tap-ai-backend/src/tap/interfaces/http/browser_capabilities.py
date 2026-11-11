"""Ephemeral panel capabilities, issued only after project/task authorization.

These do not replace user authentication. The loopback application gateway must
recheck task availability on every request and revoke on termination. Restarting
the gateway intentionally revokes every grant instead of restoring write access.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserGrant:
    project_id: str
    task_id: str
    session_id: str
    controller_id: str
    actor_id: str
    expires_at: float


class BrowserCapabilities:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int = 300,
        capacity: int = 256,
    ) -> None:
        if not 1 <= ttl_seconds <= 300 or not 1 <= capacity <= 4096:
            raise ValueError("invalid browser capability limits")
        self._clock = clock
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._grants: dict[str, BrowserGrant] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        project_id: str,
        task_id: str,
        session_id: str,
        *,
        actor_id: str = "local-validation",
        controller_id: str | None = None,
        replace_session: bool = False,
    ) -> tuple[str, BrowserGrant]:
        if not all(
            isinstance(value, str) and 0 < len(value) <= 128
            for value in (project_id, task_id, session_id)
        ):
            raise ValueError("invalid capability binding")
        if controller_id is not None and (
            not replace_session or not re.fullmatch(r"[a-f0-9]{32}", controller_id)
        ):
            raise ValueError("invalid trusted controller identity")
        with self._lock:
            if replace_session:
                self._grants = {
                    key: grant
                    for key, grant in self._grants.items()
                    if (grant.project_id, grant.task_id, grant.session_id, grant.actor_id)
                    != (project_id, task_id, session_id, actor_id)
                }
            now = self._clock()
            self._grants = {
                key: grant for key, grant in self._grants.items() if grant.expires_at > now
            }
            if len(self._grants) >= self._capacity:
                raise RuntimeError("browser panel capacity reached")
            token = secrets.token_urlsafe(32)
            grant = BrowserGrant(
                project_id,
                task_id,
                session_id,
                controller_id or secrets.token_hex(16),
                actor_id,
                now + self._ttl,
            )
            self._grants[hashlib.sha256(token.encode()).hexdigest()] = grant
            return token, grant

    def verify(self, token: str, project_id: str, task_id: str, session_id: str) -> BrowserGrant:
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise PermissionError("browser capability unavailable")
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            grant = self._grants.get(key)
            if grant is not None and grant.expires_at <= self._clock():
                self._grants.pop(key, None)
                grant = None
            if grant is None or (grant.project_id, grant.task_id, grant.session_id) != (
                project_id,
                task_id,
                session_id,
            ):
                raise PermissionError("browser capability unavailable")
            return grant

    def revoke_session(self, project_id: str, session_id: str) -> None:
        with self._lock:
            self._grants = {
                key: grant
                for key, grant in self._grants.items()
                if (grant.project_id, grant.session_id) != (project_id, session_id)
            }
