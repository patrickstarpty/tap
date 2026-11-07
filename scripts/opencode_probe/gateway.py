"""Task-local, bounded LiteLLM forwarding gate for capability probes.

Only the supervisor owns the upstream key. The OpenCode child receives a
random token for this loopback server and cannot select another upstream.
"""

import http.client
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


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

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("gate is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "BudgetGate":
        gate = self

        class Handler(BaseHTTPRequestHandler):
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
                    payload = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    self._reject(400)
                    return
                if not isinstance(payload, dict) or payload.get("model") != "qwen-plus":
                    self._reject(403)
                    return
                requested = payload.get(
                    "max_tokens",
                    payload.get("max_completion_tokens", gate._max_output_tokens),
                )
                if (
                    not isinstance(requested, int)
                    or not 0 < requested <= gate._max_output_tokens
                ):
                    self._reject(400)
                    return
                if payload.get("stream") is True:
                    payload["stream_options"] = {"include_usage": True}
                payload["max_tokens"] = requested
                payload.pop("max_completion_tokens", None)
                payload["model"] = "qwen-plus"
                forwarded = json.dumps(payload, separators=(",", ":")).encode()
                claimed_session_id = self.headers.get("x-opencode-session-id")
                if claimed_session_id and not re.fullmatch(
                    r"ses_[a-zA-Z0-9_-]{1,100}", claimed_session_id
                ):
                    self._reject(400)
                    return
                gate_request_id = "probe_" + secrets.token_hex(12)
                reservation = (
                    len(forwarded) * gate._input_price + requested * gate._output_price
                ) / 1_000_000
                with gate._lock:
                    if (
                        time.monotonic() >= gate._deadline
                        or gate.budget_exceeded
                        or gate.calls >= gate._max_calls
                        or gate._reserved_cost + reservation > gate._max_cost
                    ):
                        gate.budget_exceeded = True
                        self._reject(429)
                        return
                    gate.calls += 1
                    gate._reserved_cost += reservation
                connection = http.client.HTTPConnection(
                    "127.0.0.1",
                    gate._upstream_port,
                    timeout=max(0.1, gate._deadline - time.monotonic()),
                )
                try:
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
                    content = bytearray()
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
                            break
                    request_id = response.getheader("x-request-id")
                    if len(content) > gate._max_output_bytes:
                        gate.budget_exceeded = True
                        status = 502
                        content = bytearray()
                    else:
                        status = response.status
                    usage_verified = False
                    prompt_tokens: int | None = None
                    completion_tokens: int | None = None
                    estimated_cost_usd: float | None = None
                    if status == 200:
                        try:
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
                            usage_verified = all(
                                type(usage.get(name)) is int and usage[name] >= 0
                                for name in ("prompt_tokens", "completion_tokens")
                            )
                        except (ValueError, KeyError, TypeError, IndexError):
                            pass
                        if usage_verified:
                            prompt_tokens = usage["prompt_tokens"]
                            completion_tokens = usage["completion_tokens"]
                            estimated_cost_usd = (
                                prompt_tokens * gate._input_price
                                + completion_tokens * gate._output_price
                            ) / 1_000_000
                            if estimated_cost_usd > reservation:
                                gate.budget_exceeded = True
                                status = 502
                                content = bytearray()
                        if not usage_verified:
                            gate.budget_exceeded = True
                            status = 502
                            content = bytearray()
                    with gate._lock:
                        gate.records.append(
                            CallRecord(
                                gate_request_id,
                                request_id,
                                claimed_session_id,
                                response.status,
                                usage_verified,
                                prompt_tokens,
                                completion_tokens,
                                estimated_cost_usd,
                            )
                        )
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
                except (OSError, http.client.HTTPException, TimeoutError):
                    with gate._lock:
                        gate.records.append(
                            CallRecord(
                                gate_request_id,
                                None,
                                claimed_session_id,
                                502,
                                False,
                                None,
                                None,
                                None,
                            )
                        )
                        gate.budget_exceeded = True
                    self._reject(502)
                finally:
                    connection.close()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._server = None
        self._thread = None
