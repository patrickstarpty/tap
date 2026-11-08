"""Project-scoped HTTP client for the durable browser worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class BrowserApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"browser API {status}: {detail[:300]}")
        self.status = status


class BrowserApiClient:
    def __init__(
        self, origin: str, project_id: str, token: str, *, timeout_seconds: float = 10.0
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not _ID.fullmatch(project_id)
            or len(token) < 16
            or timeout_seconds <= 0
        ):
            raise ValueError("browser API requires a loopback origin and project credential")
        self._base = f"http://127.0.0.1:{parsed.port}/api/projects/{project_id}/browser"
        self.project_id = project_id
        self._token = token
        self._timeout = timeout_seconds

    @staticmethod
    def _id(value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("invalid browser resource identity")
        return value

    def _sync_request(
        self, method: str, path: str, body: object | None, key: str | None
    ) -> dict[str, Any]:
        data = None if body is None else canonical_json_bytes(body)
        if data is not None and len(data) > 1_300_000:
            raise ValueError("browser request exceeds client cap")
        headers = {"Authorization": f"Bearer {self._token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = self._id(key)
        request = Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw = response.read(1_300_001)
                if len(raw) > 1_300_000:
                    raise ValueError("browser response exceeds client cap")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("invalid browser response")
                return result
        except HTTPError as error:
            raw = error.read(1024)
            try:
                detail = str(json.loads(raw).get("detail", "request rejected"))
            except (ValueError, AttributeError):
                detail = "request rejected"
            raise BrowserApiError(error.code, detail) from None

    async def request(
        self, method: str, path: str, body: object | None = None, key: str | None = None
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync_request, method, path, body, key)

    def _sync_evidence(self, evidence_id: str) -> tuple[bytes, str]:
        request = Request(
            self._base + f"/evidence/{self._id(evidence_id)}",
            headers={"Authorization": f"Bearer {self._token}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                data = response.read(10_000_001)
                if len(data) > 10_000_000:
                    raise ValueError("browser evidence exceeds client cap")
                return data, response.headers.get_content_type()
        except HTTPError as error:
            raise BrowserApiError(error.code, "evidence unavailable") from None

    async def read_evidence(self, evidence_id: str) -> tuple[bytes, str]:
        return await asyncio.to_thread(self._sync_evidence, evidence_id)

    async def poll(self, path: str, statuses: frozenset[str], *, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            receipt = await self.request("GET", path)
            if receipt.get("status") in statuses:
                return receipt
            await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        raise TimeoutError("browser API receipt deadline")

    async def _create(
        self, path: str, body: dict[str, Any], key: str
    ) -> tuple[dict[str, Any], bool]:
        # A canceled to_thread await leaves the HTTP POST running. Drain it, then
        # reconcile only this frozen request and key through server idempotency.
        pending = asyncio.create_task(self.request("POST", path, body, key))
        interrupted = False
        try:
            return await asyncio.shield(pending), False
        except asyncio.CancelledError:
            interrupted = True
        except BrowserApiError as error:
            if error.status < 500:
                raise
        except Exception:
            pass
        if interrupted:
            try:
                return await pending, True
            except (asyncio.CancelledError, Exception):
                pass
        try:
            return await self.request("POST", path, body, key), interrupted
        except (asyncio.CancelledError, Exception) as error:
            return {
                "status": "unknown",
                "failure_code": "create_unconfirmed",
                "primary_error_type": type(error).__name__,
            }, interrupted

    @staticmethod
    async def _finish_stop(work: Coroutine[Any, Any, dict[str, Any]]) -> dict[str, Any]:
        task = asyncio.create_task(work)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            return await task

    async def session(
        self, task_id: str, origins: list[str], deadline_seconds: int, key: str
    ) -> dict[str, Any]:
        receipt, interrupted = await self._create(
            "/sessions",
            {
                "task_id": self._id(task_id),
                "allowed_origins": origins,
                "network_mode": "public_web",
                "deadline_seconds": deadline_seconds,
            },
            key,
        )
        if receipt.get("status") == "unknown":
            return receipt
        session_id = self._id(str(receipt["session_id"]))
        if not interrupted:
            try:
                return await self.poll(
                    f"/sessions/{session_id}",
                    frozenset({"active", "unknown", "canceled"}),
                    deadline=time.monotonic() + deadline_seconds,
                )
            except (asyncio.CancelledError, Exception):
                pass
        try:
            return await self._finish_stop(self.cancel_session(session_id))
        except (asyncio.CancelledError, Exception):
            return {
                "session_id": session_id,
                "status": "unknown",
                "failure_code": "stop_unconfirmed",
            }

    async def action(
        self, session_id: str, action: dict[str, Any], key: str, *, deadline_seconds: int = 30
    ) -> dict[str, Any]:
        session_id = self._id(session_id)
        receipt = await self.request("POST", f"/sessions/{session_id}/actions", action, key)
        action_id = self._id(str(receipt["action_id"]))
        return await self.poll(
            f"/sessions/{session_id}/actions/{action_id}",
            frozenset({"completed", "failed", "unknown"}),
            deadline=time.monotonic() + deadline_seconds,
        )

    async def cancel_session(
        self, session_id: str, *, deadline_seconds: int = 30
    ) -> dict[str, Any]:
        session_id = self._id(session_id)
        await self.request("POST", f"/sessions/{session_id}/cancel")
        return await self.poll(
            f"/sessions/{session_id}",
            frozenset({"canceled", "unknown"}),
            deadline=time.monotonic() + deadline_seconds,
        )

    async def bundle(
        self, task_id: str, script: str, input_data: dict[str, Any], origins: list[str], key: str
    ) -> dict[str, Any]:
        script_bytes = script.encode()
        input_digest = hashlib.sha256(canonical_json_bytes(input_data)).hexdigest()
        return await self.request(
            "POST",
            "/bundles",
            {
                "task_id": self._id(task_id),
                "entry_name": "flow.spec.ts",
                "script": script,
                "sha256": hashlib.sha256(script_bytes).hexdigest(),
                "input_data": input_data,
                "input_sha256": input_digest,
                "allowed_origins": origins,
                "network_mode": "public_web",
            },
            key,
        )

    async def execute(
        self,
        task_id: str,
        bundle_id: str,
        input_digest: str,
        limits: dict[str, Any],
        key: str,
        *,
        deadline_seconds: int,
        stop_requested: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        receipt, interrupted = await self._create(
            "/executions",
            {
                "task_id": self._id(task_id),
                "bundle_id": self._id(bundle_id),
                "input_sha256": input_digest,
                "limits": limits,
            },
            key,
        )
        if receipt.get("status") == "unknown":
            return receipt
        execution_id = self._id(str(receipt["execution_id"]))
        if interrupted:
            return await self._stop_execution(execution_id, asyncio.CancelledError())
        return await self._wait_execution(execution_id, deadline_seconds, stop_requested)

    async def _stop_execution(self, execution_id: str, error: BaseException) -> dict[str, Any]:
        try:
            return await self._finish_stop(self.cancel_execution(execution_id))
        except (asyncio.CancelledError, Exception) as stop_error:
            return {
                "execution_id": execution_id,
                "status": "unknown",
                "failure_code": "stop_unconfirmed",
                "primary_error_type": type(error).__name__,
                "stop_error_type": type(stop_error).__name__,
            }

    async def _wait_execution(
        self,
        execution_id: str,
        deadline_seconds: int,
        stop_requested: Callable[[], Awaitable[bool]] | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + deadline_seconds
        try:
            while time.monotonic() < deadline:
                if stop_requested is not None and await stop_requested():
                    raise asyncio.CancelledError("workflow cancel requested")
                observed = await self.request("GET", f"/executions/{execution_id}")
                if observed.get("status") in {"passed", "failed", "canceled", "unknown"}:
                    return observed
                await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            raise TimeoutError("browser execution receipt deadline")
        except (asyncio.CancelledError, Exception) as error:
            return await self._stop_execution(execution_id, error)

    async def replay(
        self,
        task_id: str,
        execution_id: str,
        key: str,
        *,
        deadline_seconds: int,
        stop_requested: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        receipt, interrupted = await self._create(
            "/executions",
            {"task_id": self._id(task_id), "rerun_of": self._id(execution_id)},
            key,
        )
        if receipt.get("status") == "unknown":
            return receipt
        new_id = self._id(str(receipt["execution_id"]))
        if interrupted:
            return await self._stop_execution(new_id, asyncio.CancelledError())
        return await self._wait_execution(new_id, deadline_seconds, stop_requested)

    async def cancel_execution(
        self, execution_id: str, *, deadline_seconds: int = 30
    ) -> dict[str, Any]:
        execution_id = self._id(execution_id)
        await self.request("POST", f"/executions/{execution_id}/cancel")
        return await self.poll(
            f"/executions/{execution_id}",
            frozenset({"passed", "failed", "canceled", "unknown"}),
            deadline=time.monotonic() + deadline_seconds,
        )
