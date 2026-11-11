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

from tap.modules.workflows.ports.browser import BrowserReadError

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class BrowserApiError(BrowserReadError):
    def __init__(self, status: int, detail: object) -> None:
        super().__init__(f"browser API {status}: {str(detail)[:300]}")
        self.status = status
        self.detail = detail


class BrowserApiClient:
    def __init__(
        self,
        origin: str,
        project_id: str,
        token: str,
        *,
        timeout_seconds: float = 10.0,
        gateway_token: str | None = None,
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
        self._gateway_token = gateway_token

    @property
    def gateway_available(self) -> bool:
        return bool(
            self._gateway_token
            and len(self._gateway_token) >= 32
            and self._gateway_token != self._token
        )

    @staticmethod
    def _id(value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("invalid browser resource identity")
        return value

    def _sync_request(
        self,
        method: str,
        path: str,
        body: object | None,
        key: str | None,
        controller_id: str | None = None,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        data = None if body is None else canonical_json_bytes(body)
        if data is not None and len(data) > 1_300_000:
            raise ValueError("browser request exceeds client cap")
        headers = {"Authorization": f"Bearer {self._token}"}
        if controller_id is not None:
            if (
                not self._gateway_token
                or len(self._gateway_token) < 32
                or self._gateway_token == self._token
            ):
                raise ValueError("browser gateway credential unavailable")
            if not re.fullmatch(r"[a-f0-9]{32}", controller_id):
                raise ValueError("invalid browser controller")
            if expires_at is None or not time.time() < expires_at <= time.time() + 301:
                raise PermissionError("browser capability expired")
            headers = {
                "Authorization": f"Bearer {self._gateway_token}",
                "X-Tap-Controller": controller_id,
                "X-Tap-Controller-Expires": str(expires_at),
            }
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
                detail = json.loads(raw).get("detail", "request rejected")
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
                data = response.read(80_000_001)
                if len(data) > 80_000_000:
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

    async def _human_post(
        self,
        session_id: str,
        controller_id: str,
        action: dict[str, Any],
        key: str,
        *,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._sync_request,
            "POST",
            f"/sessions/{self._id(session_id)}/human-actions",
            action,
            key,
            controller_id,
            expires_at,
        )

    async def human_action(
        self,
        session_id: str,
        controller_id: str,
        action: dict[str, Any],
        expected_epoch: int,
        key: str,
        *,
        expires_at: float | None = None,
        before_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "type",
            "selector",
            "x",
            "y",
            "value",
            "delta_x",
            "delta_y",
            "url",
            "frame_sequence",
            "viewport",
        }
        if set(action) - allowed or action.get("type") not in {
            "click",
            "fill",
            "scroll",
            "select",
            "navigate",
        }:
            raise ValueError("unsupported human action")
        body = {**action, "expected_epoch": expected_epoch}
        deadline = time.monotonic() + 90
        if action["type"] in {"fill", "select"}:
            probe = {k: v for k, v in body.items() if k not in {"value", "type"}}
            probe.update(type="preflight", operation=action["type"])
            probe_key = (
                "preflight-"
                + hashlib.sha256((key + json.dumps(probe, sort_keys=True)).encode()).hexdigest()
            )
            if before_dispatch is not None:
                await before_dispatch()
            created = await self._human_post(
                session_id, controller_id, probe, probe_key, expires_at=expires_at
            )
            proof = await self.poll(
                f"/sessions/{self._id(session_id)}/actions/{self._id(str(created['action_id']))}",
                frozenset({"completed", "failed", "unknown", "rejected"}),
                deadline=deadline,
            )
            if (
                proof.get("status") != "completed"
                or proof.get("observation", {})
                .get("extracted", {})
                .get("preflight", {})
                .get("safe")
                is not True
            ):
                raise BrowserApiError(409, "browser input preflight refused")
            body["preflight_action_id"] = self._id(str(proof["action_id"]))
        if before_dispatch is not None:
            await before_dispatch()
        created = await self._human_post(
            session_id, controller_id, body, key, expires_at=expires_at
        )
        return await self.poll(
            f"/sessions/{self._id(session_id)}/actions/{self._id(str(created['action_id']))}",
            frozenset({"completed", "failed", "unknown", "rejected"}),
            deadline=deadline,
        )

    async def human_control(
        self,
        session_id: str,
        controller_id: str,
        operation: str,
        expected_epoch: int,
        key: str,
        *,
        expires_at: float | None = None,
        before_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        if operation not in {"takeover", "handback"}:
            raise ValueError("invalid browser control operation")
        if before_dispatch is not None:
            await before_dispatch()
        return await asyncio.to_thread(
            self._sync_request,
            "POST",
            f"/sessions/{self._id(session_id)}/control",
            {"operation": operation, "expected_epoch": expected_epoch},
            key,
            controller_id,
            expires_at,
        )

    async def reserve_model(
        self, session_id: str, expected_epoch: int, request_id: str
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/sessions/{self._id(session_id)}/model-reservations",
            {"expected_epoch": expected_epoch, "request_id": self._id(request_id)},
        )

    async def settle_model(
        self, session_id: str, request_id: str, *, outcome: str = "completed"
    ) -> dict[str, Any]:
        if outcome not in {"completed", "unknown"}:
            raise ValueError("invalid model settlement")
        return await self.request(
            "POST",
            f"/sessions/{self._id(session_id)}/model-reservations/{self._id(request_id)}/settle",
            {"outcome": outcome},
        )

    async def get_control(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/sessions/{self._id(session_id)}/control")

    async def session(
        self,
        task_id: str,
        origins: list[str],
        deadline_seconds: int,
        key: str,
        *,
        control_version: str | None = None,
    ) -> dict[str, Any]:
        if control_version not in (None, "browser-control-v1"):
            raise ValueError("unsupported browser control version")
        receipt, interrupted = await self._create(
            "/sessions",
            {
                "task_id": self._id(task_id),
                "allowed_origins": origins,
                "network_mode": "public_web",
                "deadline_seconds": deadline_seconds,
                **({"control_version": control_version} if control_version else {}),
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
        self, session_id: str, action: dict[str, Any], key: str, *, deadline_seconds: int = 105
    ) -> dict[str, Any]:
        session_id = self._id(session_id)
        if (
            "expected_epoch" in action
            and action.get("type") in {"fill", "select", "press"}
            and "preflight_action_id" not in action
        ):
            preflight = {
                "type": "preflight",
                "operation": action["type"],
                "selector": action.get("selector"),
                "expected_epoch": action["expected_epoch"],
            }
            preflight_key = (
                "preflight-"
                + hashlib.sha256((key + json.dumps(preflight, sort_keys=True)).encode()).hexdigest()
            )
            proof = await self.action(
                session_id, preflight, preflight_key, deadline_seconds=deadline_seconds
            )
            if (
                proof.get("status") != "completed"
                or proof.get("observation", {})
                .get("extracted", {})
                .get("preflight", {})
                .get("safe")
                is not True
            ):
                raise BrowserApiError(409, "browser input preflight refused")
            action = {**action, "preflight_action_id": self._id(str(proof["action_id"]))}
        receipt = await self.request("POST", f"/sessions/{session_id}/actions", action, key)
        action_id = self._id(str(receipt["action_id"]))
        return await self.poll(
            f"/sessions/{session_id}/actions/{action_id}",
            frozenset({"completed", "failed", "unknown", "rejected"}),
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
