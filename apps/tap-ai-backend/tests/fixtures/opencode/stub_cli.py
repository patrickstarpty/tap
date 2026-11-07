"""Deterministic subprocess fixture for OpenCode process capture."""

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
elif mode == "serve":
    import json
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
