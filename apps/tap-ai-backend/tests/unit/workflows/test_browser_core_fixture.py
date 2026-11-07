"""Synthetic OpenCode fixture must not consume the browse stage during a probe."""

import importlib.util
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen


def test_no_tool_probe_does_not_consume_first_browser_action():
    script = Path(__file__).resolve().parents[5] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("browser_core_runner", script)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.SyntheticModel.stages.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), runner.SyntheticModel)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        def send(tools):
            body = json.dumps(
                {"messages": [{"content": "Core lookup: inspect the page"}], "tools": tools}
            ).encode()
            request = Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                body,
                {"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read().split(b"data: ", 1)[1].split(b"\n", 1)[0])

        probe = send([])
        assert probe["choices"][0]["delta"].get("tool_calls") is None
        browse = send([{"type": "function"}])
        arguments = browse["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
        command = json.loads(arguments)["command"]
        assert "browserctl.mjs" in command
        assert runner.SyntheticModel.stages["lookup"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
