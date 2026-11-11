import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from tap_platform.browser_automation.input_transport import WorkerInputClient


def test_worker_input_transport_rejects_non_loopback_and_short_tokens():
    for origin in (
        "https://example.com",
        "http://127.0.0.1:80/path",
        "http://user@127.0.0.1:80",
    ):
        with pytest.raises(ValueError):
            WorkerInputClient(origin, "w" * 32)
    with pytest.raises(ValueError):
        WorkerInputClient("http://127.0.0.1:80", "short")


@pytest.mark.asyncio
async def test_worker_input_transport_authenticates_and_refuses_redirects(monkeypatch):
    mode = ["input"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer " + "w" * 32
            if mode[0] == "redirect":
                self.send_response(302)
                self.send_header("Location", "https://example.com/")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"value": "synthetic"}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        client = WorkerInputClient(f"http://127.0.0.1:{server.server_port}", "w" * 32)
        assert await client("p", "s", "a") == {"value": "synthetic"}
        mode[0] = "redirect"
        with pytest.raises(ValueError, match="redirect"):
            await client("p", "s", "a")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
