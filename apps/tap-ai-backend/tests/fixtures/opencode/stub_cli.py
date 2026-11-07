"""Deterministic subprocess fixture for OpenCode process capture."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]
if mode == "interleaved":
    sys.stdout.write('{"type":"step_')
    sys.stdout.flush()
    sys.stderr.write("diagnostic\n")
    sys.stderr.flush()
    sys.stdout.write('finish","sessionID":"ses_test"}\n')
elif mode == "invalid":
    sys.stdout.write("{invalid\n")
    sys.exit(7)
elif mode == "long":
    sys.stdout.write("x" * 100_000 + "\n")
elif mode == "orphan":
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    Path("child.pid").write_text(str(child.pid))
elif mode == "closed_stdio_orphan":
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path("child.pid").write_text(str(child.pid))
elif mode == "stubborn":
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    Path("child.pid").write_text(str(child.pid))
    time.sleep(0.2)
elif mode == "hang":
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    Path("child.pid").write_text(str(child.pid))
    time.sleep(30)
elif mode == "--version":
    print("1.19.5")
elif mode == "debug" and sys.argv[2:] == ["config"]:
    import yaml

    Path("debug.pid").write_text(str(os.getpid()))
    config = json.loads(Path(".opencode/opencode.json").read_text())
    inline = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
    config.update(inline)
    config["provider"]["litellm"]["options"]["apiKey"] = "***"
    for path in Path(".opencode/agents").glob("*.md"):
        profile = yaml.safe_load(path.read_text().split("\n---\n", 1)[0].removeprefix("---\n"))
        config["agent"][path.stem] = profile
    if os.environ.get("STUB_BAD_CONFIG"):
        config["agent"]["sneaky"] = {"permission": {"*": "allow"}}
    print(json.dumps(config))
elif mode == "serve":
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                {
                    "openapi": "3.1.0",
                    "paths": {
                        p: {}
                        for p in (
                            "/session",
                            "/session/{sessionID}/message",
                            "/session/{sessionID}/children",
                            "/session/{sessionID}/prompt_async",
                            "/session/{sessionID}/abort",
                            "/event",
                        )
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    port = int(sys.argv[sys.argv.index("--port") + 1])
    Path("serve.pid").write_text(str(os.getpid()))
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
