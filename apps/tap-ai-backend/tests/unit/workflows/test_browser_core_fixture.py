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
                {
                    "messages": [
                        {"content": "Core lookup: inspect the page; customer=Ada; region=west"}
                    ],
                    "tools": tools,
                }
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
        browse = send([{"type": "function", "function": {"name": "tap_browser"}}])
        arguments = browse["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
        action = json.loads(arguments)["action"]
        assert action["type"] == "plan"
        assert action["steps"][0]["id"] == "inspect"
        assert runner.SyntheticModel.stages["lookup"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_local_worker_receives_runtime_selection_but_not_model_credentials():
    script = Path(__file__).resolve().parents[5] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("local_browser_core_runner", script)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    env = {
        "TAP_BROWSER_EXECUTION_MODE": "local",
        "TAP_BROWSER_LOCAL_TEST_TARGET": "http://127.0.0.1:18080",
        "TAP_BROWSER_LOCAL_OPENCODE_BINARY": "/private/tmp/opencode",
        "LITELLM_MASTER_KEY": "private-model-credential",
    }
    selected = runner.selected_browser_env(env)
    assert selected["TAP_BROWSER_EXECUTION_MODE"] == "local"
    assert selected["TAP_BROWSER_LOCAL_TEST_TARGET"] == "http://127.0.0.1:18080"
    assert "LITELLM_MASTER_KEY" not in selected
    assert "TAP_BROWSER_LOCAL_OPENCODE_BINARY" not in selected
