"""Pinned OpenCode 1.19.5 legacy session protocol helpers."""

import base64
import http.client
import json
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

_SESSION_ID = re.compile(r"ses_[A-Za-z0-9_-]{1,100}\Z")
_REQUIRED_PATHS = frozenset(
    {
        "/session",
        "/session/{sessionID}/message",
        "/session/{sessionID}/children",
        "/session/{sessionID}/prompt_async",
        "/session/{sessionID}/abort",
        "/event",
    }
)


def parse_sse(chunks: Iterable[bytes]) -> tuple[tuple[dict[str, object], ...], int]:
    """Parse bounded legacy SSE JSON data, ignoring transport heartbeats."""
    events: list[dict[str, object]] = []
    invalid = 0
    pending = bytearray()
    data: list[bytes] = []
    for chunk in chunks:
        for byte in chunk:
            if byte == 10:
                line = bytes(pending).rstrip(b"\r")
                pending.clear()
                if not line:
                    if data:
                        try:
                            event = json.loads(b"\n".join(data))
                            if isinstance(event, dict) and "type" in event:
                                if len(events) < 512:
                                    events.append(event)
                                else:
                                    invalid += 1
                            else:
                                invalid += 1
                        except (ValueError, UnicodeDecodeError):
                            invalid += 1
                        data.clear()
                elif line.startswith(b"data:") and len(data) < 16:
                    data.append(line[5:].lstrip())
            elif len(pending) < 4096:
                pending.append(byte)
            else:
                invalid += 1
    return tuple(events), invalid


def _messages_terminal(messages: list[object]) -> str:
    finished = False
    for entry in messages:
        if not isinstance(entry, dict):
            return "incomplete"
        info = entry.get("info")
        if not isinstance(info, dict):
            return "incomplete"
        if info.get("error"):
            return "failed"
        if info.get("role") == "assistant" and info.get("finish"):
            finished = True
        parts = entry.get("parts")
        if not isinstance(parts, list):
            return "incomplete"
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "tool":
                state = part.get("state")
                if isinstance(state, dict) and state.get("status") == "error":
                    return "failed"
    return "complete" if finished else "incomplete"


def classify_session(
    idle_seen: bool, messages: list[object], children: list[dict[str, object]]
) -> str:
    """Idle and terminal assistant facts are distinct requirements."""
    if not idle_seen:
        return "incomplete"
    root = _messages_terminal(messages)
    if root != "complete":
        return root
    for child in children:
        if child.get("idle_seen") is not True:
            return "incomplete"
        child_messages = child.get("messages")
        if not isinstance(child_messages, list):
            return "incomplete"
        outcome = _messages_terminal(child_messages)
        if outcome != "complete":
            return outcome
    return "complete"


class LegacyClient:
    """Authenticated loopback-only client for the installed legacy routes."""

    def __init__(
        self, origin: str, password: str, *, timeout_seconds: float = 5
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not password
            or timeout_seconds <= 0
        ):
            raise ValueError("legacy client requires a loopback server and password")
        self._port = parsed.port
        self._auth = (
            "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
        )
        self._timeout = timeout_seconds

    def _request(
        self, method: str, path: str, body: object | None = None
    ) -> tuple[int, object | None]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self._port, timeout=self._timeout
        )
        payload = None if body is None else json.dumps(body).encode()
        try:
            connection.request(
                method,
                path,
                body=payload,
                headers={
                    "Authorization": self._auth,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("legacy response exceeds evidence cap")
            return response.status, json.loads(raw) if raw else None
        finally:
            connection.close()

    def verify_schema(self) -> None:
        status, document = self._request("GET", "/doc")
        if (
            status != 200
            or not isinstance(document, dict)
            or document.get("openapi") != "3.1.0"
            or not isinstance(document.get("paths"), dict)
            or not _REQUIRED_PATHS.issubset(document["paths"])
        ):
            raise ValueError("installed legacy OpenAPI contract mismatch")

    def create_session(self) -> str:
        status, response = self._request("POST", "/session", {})
        if status != 200 or not isinstance(response, dict):
            raise ValueError("session creation failed")
        session_id = response.get("id")
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ValueError("invalid session ID")
        return session_id

    def prompt_async(self, session_id: str, parts: list[dict[str, object]]) -> str:
        self._check_id(session_id)
        status, _ = self._request(
            "POST", f"/session/{session_id}/prompt_async", {"parts": parts}
        )
        if status != 204:
            raise ValueError("prompt admission failed")
        return "admitted"

    def abort(self, session_id: str) -> str:
        self._check_id(session_id)
        status, response = self._request("POST", f"/session/{session_id}/abort", {})
        if status != 200 or response is not True:
            raise ValueError("abort request failed")
        return "cancel_requested"

    def reconcile(self, session_id: str, *, idle_sessions: set[str]) -> str:
        self._check_id(session_id)
        status, messages = self._request("GET", f"/session/{session_id}/message")
        child_status, raw_children = self._request(
            "GET", f"/session/{session_id}/children"
        )
        if (
            status != 200
            or not isinstance(messages, list)
            or child_status != 200
            or not isinstance(raw_children, list)
        ):
            return "incomplete"
        children: list[dict[str, object]] = []
        for raw in raw_children:
            if not isinstance(raw, dict) or raw.get("parentID") != session_id:
                return "incomplete"
            child_id = raw.get("id")
            if not isinstance(child_id, str) or not _SESSION_ID.fullmatch(child_id):
                return "incomplete"
            child_message_status, child_messages = self._request(
                "GET", f"/session/{child_id}/message"
            )
            if child_message_status != 200 or not isinstance(child_messages, list):
                return "incomplete"
            children.append(
                {"idle_seen": child_id in idle_sessions, "messages": child_messages}
            )
        return classify_session(session_id in idle_sessions, messages, children)

    @staticmethod
    def _check_id(session_id: str) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("invalid session ID")
