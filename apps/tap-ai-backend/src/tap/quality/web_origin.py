"""The Tapper web app's exact loopback `Origin` for quality scripts.

The Tapper API rejects every non-GET request whose `Origin` is not the configured
loopback web origin (`OriginPolicyMiddleware`), so corpus uploads and chat checks
send the same value the browser would.
"""

from __future__ import annotations

import os


def web_origin() -> str:
    """`http://{TAPPER_WEB_HOST}:{TAPPER_WEB_PORT}`, defaulting to the demo web origin."""
    host = os.environ.get("TAPPER_WEB_HOST", "127.0.0.1")
    port = os.environ.get("TAPPER_WEB_PORT", "5173")
    return f"http://{host}:{port}"
