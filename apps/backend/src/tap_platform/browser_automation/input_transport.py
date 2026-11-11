"""Worker-only one-time input retrieval over an authenticated loopback channel."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("browser input redirect refused")


class WorkerInputClient:
    def __init__(self, origin: str, token: str):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or len(token) < 32
        ):
            raise ValueError(
                "browser input requires loopback origin and worker credential"
            )
        self._origin = f"http://127.0.0.1:{parsed.port}"
        self._token = token

    def _consume(
        self, project_id: str, session_id: str, action_id: str
    ) -> dict[str, Any]:
        if any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
            for value in (project_id, session_id, action_id)
        ):
            raise ValueError("invalid browser input identity")
        request = Request(
            f"{self._origin}/api/projects/{project_id}/browser/sessions/{session_id}/actions/{action_id}/input",
            data=b"",
            headers={"Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=5
        ) as response:
            data = response.read(16_385)
        if len(data) > 16_384:
            raise ValueError("browser input exceeds limit")
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) - {"value", "key"}:
            raise ValueError("invalid browser input envelope")
        return value

    async def __call__(
        self, project_id: str, session_id: str, action_id: str
    ) -> dict[str, Any]:
        task = asyncio.create_task(
            asyncio.to_thread(self._consume, project_id, session_id, action_id)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
