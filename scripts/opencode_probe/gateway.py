"""Task-local, bounded LiteLLM forwarding gate for capability probes.

Only the supervisor owns the upstream key. The OpenCode child receives a
random token for this loopback server and cannot select another upstream.
"""

import http.client
import json
import math
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


_ALLOWED_FIELDS = frozenset(
    {
        "model",
        "messages",
        "max_tokens",
        "max_completion_tokens",
        "stream",
        "stream_options",
        "temperature",
        "top_p",
        "stop",
        "seed",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "response_format",
        "presence_penalty",
        "frequency_penalty",
        "n",
    }
)
_MESSAGE_FIELDS = frozenset({"role", "content", "name", "tool_call_id", "tool_calls"})


def _safe_message(message: object) -> bool:
    if not isinstance(message, dict) or set(message) - _MESSAGE_FIELDS:
        return False
    if message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
        return False
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        if not isinstance(content, list) or not all(
            isinstance(part, dict)
            and set(part) == {"type", "text"}
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            for part in content
        ):
            return False
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list) or not all(
        isinstance(call, dict)
        and set(call) == {"id", "type", "function"}
        and call.get("type") == "function"
        and isinstance(call.get("id"), str)
        and isinstance(call.get("function"), dict)
        and set(call["function"]) == {"name", "arguments"}
        and all(isinstance(value, str) for value in call["function"].values())
        for call in calls
    ):
        return False
    return True


def _allowed_payload(raw: object, output_limit: int) -> dict[str, object] | None:
    if not isinstance(raw, dict) or set(raw) - _ALLOWED_FIELDS:
        return None
    if (
        raw.get("model") != "qwen-plus"
        or raw.get("n", 1) != 1
        or type(raw.get("n", 1)) is not int
    ):
        return None
    messages = raw.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) > 100
        or not all(_safe_message(message) for message in messages)
    ):
        return None
    requested = raw.get("max_tokens", raw.get("max_completion_tokens", output_limit))
    if type(requested) is not int or not 0 < requested <= output_limit:
        return None
    if (
        "max_tokens" in raw
        and "max_completion_tokens" in raw
        and raw["max_tokens"] != raw["max_completion_tokens"]
    ):
        return None
    if "stream" in raw and type(raw["stream"]) is not bool:
        return None
    tools = raw.get("tools", [])
    if (
        not isinstance(tools, list)
        or len(tools) > 50
        or not all(
            isinstance(tool, dict)
            and set(tool) == {"type", "function"}
            and tool.get("type") == "function"
            and isinstance(tool.get("function"), dict)
            and isinstance(tool["function"].get("name"), str)
            and set(tool["function"]) <= {"name", "description", "parameters", "strict"}
            for tool in tools
        )
    ):
        return None
    result = {
        key: raw[key]
        for key in _ALLOWED_FIELDS
        if key in raw and key not in {"max_completion_tokens", "stream_options"}
    }
    result["model"] = "qwen-plus"
    result["n"] = 1
    result["max_tokens"] = requested
    if raw.get("stream") is True:
        result["stream_options"] = {"include_usage": True}
    return result


@dataclass(frozen=True)
class CallRecord:
    gate_request_id: str
    request_id: str | None
    claimed_session_id: str | None
    status: int
    usage_verified: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated_cost_usd: float | None


class BudgetGate:
    def __init__(
        self,
        upstream_url: str,
        upstream_key: str,
        *,
        max_calls: int,
        max_seconds: int,
        max_cost_usd: float,
        input_price_per_million: float,
        output_price_per_million: float,
        max_input_tokens: int,
        max_input_bytes: int = 32768,
        max_output_bytes: int = 1048576,
        max_output_tokens: int = 512,
    ) -> None:
        parsed = urlsplit(upstream_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not parsed.port
        ):
            raise ValueError("upstream must be a loopback HTTP origin")
        if (
            not upstream_key
            or max_calls <= 0
            or max_seconds <= 0
            or not math.isfinite(max_cost_usd)
            or not math.isfinite(input_price_per_million)
            or not math.isfinite(output_price_per_million)
            or max_cost_usd <= 0
            or input_price_per_million <= 0
            or output_price_per_million <= 0
            or max_input_bytes <= 0
            or max_input_tokens <= 0
            or max_output_bytes <= 0
            or max_output_tokens <= 0
        ):
            raise ValueError("invalid finite gate budget")
        self._upstream_port = parsed.port
        self._upstream_key = upstream_key
        self._max_calls = max_calls
        self._deadline = time.monotonic() + max_seconds
        self._max_cost = max_cost_usd
        self._input_price = input_price_per_million
        self._output_price = output_price_per_million
        self._max_input_bytes = max_input_bytes
        self._max_input_tokens = max_input_tokens
        self._max_output_bytes = max_output_bytes
        self._max_output_tokens = max_output_tokens
        self._reserved_cost = 0.0
        self._lock = threading.Lock()
        self.calls = 0
        self.budget_exceeded = False
        self.records: list[CallRecord] = []
        self.token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._sockets: set[socket.socket] = set()
        self._closed = False

    def _track(self, connection: socket.socket) -> None:
        with self._lock:
            if self._closed:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            else:
                self._sockets.add(connection)

    def _untrack(self, connection: socket.socket | None) -> None:
        if connection is not None:
            with self._lock:
                self._sockets.discard(connection)

    def _close(self, *, expired: bool) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if expired:
                self.budget_exceeded = True
            connections = tuple(self._sockets)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def _expire(self) -> None:
        self._close(expired=True)

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("gate is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "BudgetGate":
        gate = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                gate._track(self.connection)
                self.connection.settimeout(max(0.1, gate._deadline - time.monotonic()))

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    gate._untrack(self.connection)

            def log_message(self, *_args: object) -> None:
                pass

            def _reject(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self._reject(404)
                    return
                if self.headers.get("Authorization") != f"Bearer {gate.token}":
                    self._reject(401)
                    return
                if self.headers.get("Transfer-Encoding"):
                    self._reject(400)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    size = -1
                if not 0 < size <= gate._max_input_bytes:
                    self._reject(413)
                    return
                raw = self.rfile.read(size)
                try:
                    raw_payload = json.loads(
                        raw,
                        parse_constant=lambda _value: (_ for _ in ()).throw(
                            ValueError()
                        ),
                    )
                except (ValueError, UnicodeDecodeError):
                    self._reject(400)
                    return
                if (
                    isinstance(raw_payload, dict)
                    and raw_payload.get("model") != "qwen-plus"
                ):
                    self._reject(403)
                    return
                payload = _allowed_payload(raw_payload, gate._max_output_tokens)
                if payload is None:
                    self._reject(400)
                    return
                requested = payload["max_tokens"]
                assert isinstance(requested, int)
                forwarded = json.dumps(
                    payload, separators=(",", ":"), allow_nan=False
                ).encode()
                if len(forwarded) > gate._max_input_bytes:
                    self._reject(413)
                    return
                claimed_session_id = self.headers.get("x-opencode-session-id")
                if claimed_session_id and not re.fullmatch(
                    r"ses_[a-zA-Z0-9_-]{1,100}", claimed_session_id
                ):
                    self._reject(400)
                    return
                gate_request_id = "probe_" + secrets.token_hex(12)
                reservation = (
                    gate._max_input_tokens * gate._input_price
                    + requested * gate._output_price
                ) / 1_000_000
                with gate._lock:
                    denied = (
                        time.monotonic() >= gate._deadline
                        or gate.budget_exceeded
                        or gate.calls >= gate._max_calls
                        or gate._reserved_cost + reservation > gate._max_cost
                    )
                    if denied:
                        gate.budget_exceeded = True
                    else:
                        gate.calls += 1
                        gate._reserved_cost += reservation
                if denied:
                    self._reject(429)
                    return
                connection: http.client.HTTPConnection | None = None
                upstream_status: int | None = None
                request_id: str | None = None
                prompt_tokens: int | None = None
                completion_tokens: int | None = None
                estimated_cost_usd: float | None = None
                usage_verified = False
                status = 502
                content = bytearray()
                try:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        gate._upstream_port,
                        timeout=max(0.1, gate._deadline - time.monotonic()),
                    )
                    connection.connect()
                    if connection.sock is not None:
                        gate._track(connection.sock)
                    connection.request(
                        "POST",
                        "/v1/chat/completions",
                        body=forwarded,
                        headers={
                            "Authorization": f"Bearer {gate._upstream_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    upstream_status = response.status
                    request_id = response.getheader("x-request-id")
                    while True:
                        remaining = gate._deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("gate deadline")
                        if connection.sock is not None:
                            connection.sock.settimeout(remaining)
                        chunk = response.read(
                            min(4096, gate._max_output_bytes + 1 - len(content))
                        )
                        if not chunk:
                            break
                        content.extend(chunk)
                        if len(content) > gate._max_output_bytes:
                            raise ValueError("upstream output exceeded cap")
                    if upstream_status != 200:
                        raise ValueError("upstream response has unknown usage")
                    if payload.get("stream") is True:
                        frames = [
                            line[6:]
                            for line in bytes(content).splitlines()
                            if line.startswith(b"data: ")
                        ]
                        if b"[DONE]" not in frames:
                            raise ValueError("missing terminal stream frame")
                        usages = [
                            json.loads(frame)["usage"]
                            for frame in frames
                            if frame != b"[DONE]" and b'"usage"' in frame
                        ]
                        usage = usages[-1]
                    else:
                        usage = json.loads(content)["usage"]
                    if not isinstance(usage, dict) or not all(
                        type(usage.get(name)) is int and usage[name] >= 0
                        for name in ("prompt_tokens", "completion_tokens")
                    ):
                        raise ValueError("untrusted usage")
                    prompt_tokens = usage["prompt_tokens"]
                    completion_tokens = usage["completion_tokens"]
                    estimated_cost_usd = (
                        prompt_tokens * gate._input_price
                        + completion_tokens * gate._output_price
                    ) / 1_000_000
                    if estimated_cost_usd > reservation:
                        raise ValueError("usage exceeded reservation")
                    usage_verified = True
                    status = 200
                except Exception:
                    with gate._lock:
                        gate.budget_exceeded = True
                    content = bytearray()
                finally:
                    if connection is not None:
                        gate._untrack(connection.sock)
                        connection.close()
                    with gate._lock:
                        gate.records.append(
                            CallRecord(
                                gate_request_id,
                                request_id,
                                claimed_session_id,
                                upstream_status if upstream_status is not None else 502,
                                usage_verified,
                                prompt_tokens,
                                completion_tokens,
                                estimated_cost_usd,
                            )
                        )
                try:
                    self.send_response(status)
                    self.send_header(
                        "Content-Type",
                        "text/event-stream"
                        if payload.get("stream") is True and status == 200
                        else "application/json",
                    )
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except OSError:
                    with gate._lock:
                        gate.budget_exceeded = True

        class OwnedServer(ThreadingHTTPServer):
            daemon_threads = False
            block_on_close = True

        self._server = OwnedServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._timer = threading.Timer(
            max(0, self._deadline - time.monotonic()), self._expire
        )
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, *_args: object) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._close(expired=False)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._server = None
        self._thread = None
        self._timer = None
