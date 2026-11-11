"""Production budget gate keeps durable admission ahead of upstream calls."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tap.platform.coding.gateway import BudgetGate


class Upstream(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        type(self).calls += 1
        type(self).last_body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body = b'{"usage":{"prompt_tokens":2,"completion_tokens":3},"choices":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _send(gate, payload=None):
    body = json.dumps(
        payload
        or {
            "model": "qwen-plus",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 8,
        }
    ).encode()
    request = Request(
        gate.url + "/v1/chat/completions",
        body,
        {"Authorization": "Bearer " + gate.token},
        method="POST",
    )
    try:
        with urlopen(request) as response:
            return response.status
    except HTTPError as error:
        return error.code


def test_durable_reservation_precedes_forward_and_settles_verified_usage():
    Upstream.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    records = []
    try:

        def reserve(identifier, maximum):
            assert Upstream.calls == 0
            records.append(("reserve", identifier, maximum))

        def settle(record):
            records.append(
                ("settle", record.usage_verified, record.prompt_tokens, record.completion_tokens)
            )

        with BudgetGate(
            f"http://127.0.0.1:{server.server_port}",
            "secret",
            max_calls=1,
            max_seconds=5,
            max_cost_usd=0.1,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
            max_input_tokens=1000,
            on_reserve=reserve,
            on_settle=settle,
        ) as gate:
            assert _send(gate) == 200
        assert Upstream.calls == 1
        assert records[0][0] == "reserve"
        assert records[1] == ("settle", True, 2, 3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_failed_reservation_never_forwards():
    Upstream.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with BudgetGate(
            f"http://127.0.0.1:{server.server_port}",
            "secret",
            max_calls=1,
            max_seconds=5,
            max_cost_usd=0.1,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
            max_input_tokens=1000,
            on_reserve=lambda *_: (_ for _ in ()).throw(ValueError("stale lease")),
        ) as gate:
            assert _send(gate) == 429
        assert Upstream.calls == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("settlement_fails", [False, True])
def test_durable_settlement_releases_only_verified_unused_reservation(settlement_fails):
    Upstream.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def settle(record):
        if settlement_fails:
            raise ValueError("durable settlement failed")

    try:
        with BudgetGate(
            f"http://127.0.0.1:{server.server_port}",
            "secret",
            max_calls=3,
            max_seconds=5,
            max_cost_usd=0.001025,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
            max_input_tokens=1000,
            on_settle=settle,
        ) as gate:
            assert _send(gate) == (502 if settlement_fails else 200)
            if settlement_fails:
                assert gate.snapshot()["reserved_cost_usd"] == pytest.approx(0.001016)
            else:
                # Each response uses 2 input + 3 output tokens: USD 0.000008.
                # A second maximum reservation fits; a third does not.
                assert gate.snapshot()["reserved_cost_usd"] == pytest.approx(0.000008)
                assert _send(gate) == 200
            assert _send(gate) == 429
        assert Upstream.calls == (1 if settlement_fails else 2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_long_tool_history_is_not_rejected_before_native_context_compaction():
    from tap.platform.coding.gateway import _allowed_payload

    messages = [{"role": "user", "content": "Complete the task"}]
    for index in range(120):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": f"c{index}",
                            "type": "function",
                            "function": {"name": "tap_browser", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": f"c{index}", "content": "actual observation"},
            ]
        )
    request = {"model": "qwen-plus", "max_tokens": 2048, "messages": messages}
    assert _allowed_payload(request, 2048)["messages"] == messages


def test_native_assistant_reasoning_survives_budgeted_tool_continuation():
    messages = [
        {"role": "user", "content": "Inspect the frozen input"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "Synthetic reasoning",
            "tool_calls": [
                {
                    "id": "read-1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "read-1", "content": "Frozen input"},
    ]
    Upstream.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with BudgetGate(
            f"http://127.0.0.1:{server.server_port}",
            "secret",
            max_calls=1,
            max_seconds=5,
            max_cost_usd=0.1,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
            max_input_tokens=1000,
        ) as gate:
            assert _send(gate, {"model": "qwen-plus", "messages": messages, "max_tokens": 8}) == 200
            assert Upstream.last_body["messages"] == messages
            assert gate.snapshot()["calls"] == 1
            assert _send(gate) == 429
        assert Upstream.calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "role,value",
    [
        ("user", "text"),
        ("tool", "text"),
        ("system", "text"),
        ("assistant", {}),
        ("assistant", ["text"]),
    ],
)
def test_reasoning_field_does_not_allow_other_roles_or_nontext_values(role, value):
    from tap.platform.coding.gateway import _safe_message

    assert not _safe_message({"role": role, "content": "text", "reasoning_content": value})


def test_local_budget_denial_has_a_safe_nonretryable_reason():
    with BudgetGate(
        "http://127.0.0.1:9",
        "secret",
        max_calls=1,
        max_seconds=5,
        max_cost_usd=0.000001,
        input_price_per_million=2,
        output_price_per_million=6,
        max_input_tokens=129024,
    ) as gate:
        request = Request(
            gate.url + "/v1/chat/completions",
            json.dumps(
                {"model": "qwen-plus", "messages": [{"role": "user", "content": "hello"}]}
            ).encode(),
            {"Authorization": "Bearer " + gate.token},
            method="POST",
        )
        with pytest.raises(HTTPError) as failure:
            urlopen(request)
        body = json.loads(failure.value.read())
        assert body["error"] == {
            "type": "tap_budget_exhausted",
            "code": "budget_cost",
            "message": "Task stopped: budget_cost",
            "retryable": False,
        }
        assert gate.snapshot()["forwarded"] == 0
