"""Production budget gate keeps durable admission ahead of upstream calls."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tap.platform.coding.gateway import BudgetGate


class Upstream(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        type(self).calls += 1
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b'{"usage":{"prompt_tokens":2,"completion_tokens":3},"choices":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _send(gate):
    body = json.dumps(
        {"model": "qwen-plus", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 8}
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
