"""Pinned OpenCode 1.19.5 legacy session protocol helpers."""

import base64
import http.client
import json
import re
import secrets
from dataclasses import dataclass, field
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


def _latest_user_id(messages: list[object]) -> str | None:
    for entry in reversed(messages):
        if isinstance(entry, dict) and isinstance(entry.get("info"), dict):
            info = entry["info"]
            user_id = info.get("id")
            if info.get("role") == "user" and isinstance(user_id, str):
                return user_id
    return None


def _messages_terminal(messages: list[object], expected_user_id: str) -> str:
    start = next(
        (
            i
            for i, entry in enumerate(messages)
            if isinstance(entry, dict)
            and isinstance(entry.get("info"), dict)
            and entry["info"].get("role") == "user"
            and entry["info"].get("id") == expected_user_id
        ),
        None,
    )
    if start is None:
        return "incomplete"
    last_assistant: dict[str, object] | None = None
    for entry in messages[start + 1 :]:
        if not isinstance(entry, dict) or not isinstance(entry.get("info"), dict):
            return "incomplete"
        info = entry["info"]
        if info.get("role") == "user":
            return "incomplete"
        if info.get("error"):
            return "failed"
        parts = entry.get("parts")
        if not isinstance(parts, list):
            return "incomplete"
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "tool":
                state = part.get("state")
                if not isinstance(state, dict):
                    return "incomplete"
                if state.get("status") == "error":
                    return "failed"
                if state.get("status") != "completed":
                    return "incomplete"
        if info.get("role") == "assistant":
            if info.get("parentID") != expected_user_id:
                return "incomplete"
            last_assistant = info
    return (
        "complete"
        if last_assistant and last_assistant.get("finish") == "stop"
        else "incomplete"
    )


def classify_session(
    idle_seen: bool,
    messages: list[object],
    children: list[dict[str, object]],
    *,
    expected_user_id: str | None = None,
) -> str:
    """Require current user input, fresh idle, and final assistant/tool facts."""
    if not idle_seen or expected_user_id is None:
        return "incomplete"
    root = _messages_terminal(messages, expected_user_id)
    if root != "complete":
        return root
    for child in children:
        if child.get("idle_seen") is not True:
            return "incomplete"
        child_messages = child.get("messages")
        if not isinstance(child_messages, list):
            return "incomplete"
        child_user_id = _latest_user_id(child_messages)
        if child_user_id is None:
            return "incomplete"
        outcome = _messages_terminal(child_messages, child_user_id)
        if outcome != "complete":
            return outcome
    return "complete"


@dataclass
class _Operation:
    user_message_id: str
    baseline_children: frozenset[str]
    current_assistant_seen: bool = False
    idle_sessions: set[str] = field(default_factory=set)


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
        self._operations: dict[str, _Operation] = {}
        self.serve_pid: int | None = None

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
        baseline_status, baseline = self._request(
            "GET", f"/session/{session_id}/children"
        )
        baseline_children = self._child_ids(baseline, session_id)
        if baseline_status != 200 or baseline_children is None:
            raise ValueError("child baseline unavailable before prompt")
        message_id = "msg_" + secrets.token_hex(12)
        self._operations[session_id] = _Operation(
            message_id, frozenset(baseline_children)
        )
        try:
            status, _ = self._request(
                "POST",
                f"/session/{session_id}/prompt_async",
                {"parts": parts, "messageID": message_id},
            )
            if status != 204:
                raise ValueError("prompt admission failed")
        except Exception:
            self._operations.pop(session_id, None)
            raise
        return message_id

    def observe_event(self, root_session_id: str, event: dict[str, object]) -> None:
        """Accept live events only after the current operation was admitted."""
        operation = self._operations.get(root_session_id)
        if operation is None:
            return
        properties = event.get("properties")
        if not isinstance(properties, dict):
            return
        if event.get("type") == "message.updated":
            info = properties.get("info")
            if (
                isinstance(info, dict)
                and info.get("role") == "assistant"
                and info.get("sessionID") == root_session_id
                and info.get("parentID") == operation.user_message_id
            ):
                operation.current_assistant_seen = True
        elif operation.current_assistant_seen and (
            event.get("type") == "session.idle"
            or (
                event.get("type") == "session.status"
                and properties.get("status") == {"type": "idle"}
            )
        ):
            session_id = properties.get("sessionID")
            if isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id):
                operation.idle_sessions.add(session_id)

    def abort(self, session_id: str) -> str:
        self._check_id(session_id)
        status, response = self._request("POST", f"/session/{session_id}/abort", {})
        if status != 200 or response is not True:
            raise ValueError("abort request failed")
        return "cancel_requested"

    def reconcile(self, session_id: str) -> str:
        self._check_id(session_id)
        operation = self._operations.get(session_id)
        if operation is None or not operation.current_assistant_seen:
            return "incomplete"
        status, messages = self._request("GET", f"/session/{session_id}/message")
        child_status, raw_children = self._request(
            "GET", f"/session/{session_id}/children"
        )
        if status != 200 or not isinstance(messages, list) or child_status != 200:
            return "incomplete"
        observed_children = self._child_ids(raw_children, session_id)
        if observed_children is None:
            return "incomplete"
        linked_child_ids: set[str] = set()
        for entry in messages:
            if not isinstance(entry, dict) or not isinstance(entry.get("info"), dict):
                continue
            if (
                entry["info"].get("role") != "assistant"
                or entry["info"].get("parentID") != operation.user_message_id
            ):
                continue
            parts = entry.get("parts")
            if not isinstance(parts, list):
                return "incomplete"
            for part in parts:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "tool"
                    and part.get("tool") == "task"
                ):
                    state = part.get("state")
                    metadata = (
                        state.get("metadata") if isinstance(state, dict) else None
                    )
                    child_id = (
                        metadata.get("sessionId")
                        if isinstance(metadata, dict)
                        else None
                    )
                    if not isinstance(child_id, str) or not _SESSION_ID.fullmatch(
                        child_id
                    ):
                        return "incomplete"
                    linked_child_ids.add(child_id)
        if not observed_children.difference(operation.baseline_children).issubset(
            linked_child_ids
        ):
            return "incomplete"
        children: list[dict[str, object]] = []
        for child_id in linked_child_ids:
            if child_id not in observed_children:
                return "incomplete"
            child_message_status, child_messages = self._request(
                "GET", f"/session/{child_id}/message"
            )
            if child_message_status != 200 or not isinstance(child_messages, list):
                return "incomplete"
            children.append(
                {
                    "idle_seen": child_id in operation.idle_sessions,
                    "messages": child_messages,
                }
            )
        return classify_session(
            session_id in operation.idle_sessions,
            messages,
            children,
            expected_user_id=operation.user_message_id,
        )

    @staticmethod
    def _check_id(session_id: str) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("invalid session ID")

    @staticmethod
    def _child_ids(raw_children: object, parent_id: str) -> set[str] | None:
        if not isinstance(raw_children, list):
            return None
        result: set[str] = set()
        for child in raw_children:
            if not isinstance(child, dict) or child.get("parentID") != parent_id:
                return None
            child_id = child.get("id")
            if not isinstance(child_id, str) or not _SESSION_ID.fullmatch(child_id):
                return None
            result.add(child_id)
        return result
