"""Execution-side request budget and credential isolation."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from scripts.opencode_probe.gateway import BudgetGate


class Upstream(BaseHTTPRequestHandler):
    calls = 0
    usage = True
    stream = False
    saw_stream_usage_request = False
    saw_other_headers = False
    lock = threading.Lock()

    def do_POST(self) -> None:
        with self.lock:
            type(self).calls += 1
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).saw_stream_usage_request = request.get("stream_options") == {
            "include_usage": True
        }
        type(self).saw_other_headers = "x-evil" in self.headers
        if self.stream:
            body = (
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                + (
                    b'data: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
                    if self.usage
                    else b""
                )
                + b"data: [DONE]\n\n"
            )
        else:
            body = (
                b'{"id":"chatcmpl-test","choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
                if self.usage
                else b'{"id":"chatcmpl-test","choices":[]}'
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-request-id", "req-test")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def send(
    gate: BudgetGate,
    *,
    path: str = "/v1/chat/completions",
    model: str = "qwen-plus",
    token: str | None = None,
    stream: bool = False,
) -> int:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 8,
            "stream": stream,
        }
    ).encode()
    request = Request(
        gate.url + path,
        body,
        {"Authorization": "Bearer " + (token or gate.token), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            response.read()
            return response.status
    except HTTPError as error:
        return error.code


@pytest.fixture
def upstream() -> tuple[ThreadingHTTPServer, threading.Thread]:
    Upstream.calls = 0
    Upstream.stream = False
    Upstream.usage = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, thread
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_concurrent_cap_and_denials_never_reach_upstream(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    with BudgetGate(
        f"http://127.0.0.1:{server.server_port}",
        "secret-upstream",
        max_calls=2,
        max_seconds=10,
        max_cost_usd=0.05,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    ) as gate:
        results: list[int] = []
        threads = [threading.Thread(target=lambda: results.append(send(gate))) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results.count(200) == 2
        assert results.count(429) == 6
        assert gate.calls == 2
        assert gate.budget_exceeded
        assert all(item.request_id == "req-test" for item in gate.records)
    assert Upstream.calls == 2


def test_auth_model_path_and_host_are_fixed(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    with BudgetGate(
        f"http://127.0.0.1:{server.server_port}",
        "secret-upstream",
        max_calls=2,
        max_seconds=10,
        max_cost_usd=0.05,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    ) as gate:
        assert send(gate, token="wrong") == 401
        assert send(gate, model="other") == 403
        assert send(gate, path="/v1/models") == 404
        assert send(gate, path="/v1/chat/completions?target=evil") == 404
        assert gate.calls == 0
    assert Upstream.calls == 0
    with pytest.raises(ValueError):
        BudgetGate(
            "http://evil.example:24000",
            "secret",
            max_calls=2,
            max_seconds=10,
            max_cost_usd=0.05,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
        )


def test_unknown_usage_stops_subsequent_upstream_calls(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    Upstream.usage = False
    try:
        with BudgetGate(
            f"http://127.0.0.1:{server.server_port}",
            "secret-upstream",
            max_calls=3,
            max_seconds=10,
            max_cost_usd=0.05,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
        ) as gate:
            assert send(gate) == 502
            assert send(gate) == 429
            assert gate.calls == 1
            assert gate.budget_exceeded
    finally:
        Upstream.usage = True
    assert Upstream.calls == 1


def test_stream_usage_is_bounded_and_verified(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    Upstream.stream = True
    with BudgetGate(
        f"http://127.0.0.1:{server.server_port}",
        "secret-upstream",
        max_calls=2,
        max_seconds=10,
        max_cost_usd=0.05,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    ) as gate:
        assert send(gate, stream=True) == 200
        assert Upstream.saw_stream_usage_request
        assert gate.records[0].usage_verified


def test_stream_without_usage_fails_closed(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    Upstream.stream = True
    Upstream.usage = False
    with BudgetGate(
        f"http://127.0.0.1:{server.server_port}",
        "secret-upstream",
        max_calls=2,
        max_seconds=10,
        max_cost_usd=0.05,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    ) as gate:
        assert send(gate, stream=True) == 502
        assert send(gate, stream=True) == 429
        assert gate.calls == 1


def test_session_header_is_recorded_without_forwarding_other_headers(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    with BudgetGate(
        f"http://127.0.0.1:{server.server_port}",
        "secret-upstream",
        max_calls=2,
        max_seconds=10,
        max_cost_usd=0.05,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    ) as gate:
        body = b'{"model":"qwen-plus","messages":[],"max_tokens":8}'
        request = Request(
            gate.url + "/v1/chat/completions",
            body,
            {
                "Authorization": "Bearer " + gate.token,
                "x-opencode-session-id": "ses_child123",
                "x-evil": "ignored",
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
        assert gate.records[0].claimed_session_id == "ses_child123"
        assert gate.records[0].gate_request_id.startswith("probe_")
        assert gate.records[0].request_id == "req-test"
    assert Upstream.saw_other_headers is False


def test_cost_reservation_denies_before_upstream(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    with BudgetGate(
        f"http://127.0.0.1:{server.server_port}",
        "secret-upstream",
        max_calls=2,
        max_seconds=10,
        max_cost_usd=0.000001,
        input_price_per_million=100.0,
        output_price_per_million=100.0,
    ) as gate:
        assert send(gate) == 429
        assert gate.calls == 0
    assert Upstream.calls == 0


def test_price_must_be_verified_by_supervisor(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    with pytest.raises(TypeError):
        BudgetGate(
            f"http://127.0.0.1:{server.server_port}", "secret-upstream", max_calls=1, max_seconds=5
        )


def test_nonfinite_price_cannot_disable_budget(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    with pytest.raises(ValueError):
        BudgetGate(
            f"http://127.0.0.1:{server.server_port}",
            "secret-upstream",
            max_calls=1,
            max_seconds=5,
            max_cost_usd=float("nan"),
            input_price_per_million=1.0,
            output_price_per_million=2.0,
        )


def test_usage_record_keeps_unknown_distinct_from_zero(
    upstream: tuple[ThreadingHTTPServer, threading.Thread],
) -> None:
    server, _ = upstream
    with BudgetGate(
        f"http://127.0.0.1:{server.server_port}",
        "secret-upstream",
        max_calls=2,
        max_seconds=10,
        max_cost_usd=0.05,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    ) as gate:
        assert send(gate) == 200
        assert gate.records[0].prompt_tokens == 1
        assert gate.records[0].completion_tokens == 1
        assert gate.records[0].estimated_cost_usd == pytest.approx(0.000003)
