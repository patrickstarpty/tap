"""Worker-owned browser runtime for durable intents; no side-effect replay."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from dataclasses import asdict, replace
from typing import Any, Protocol
from collections.abc import Awaitable, Callable

from .contracts import BrowserLimits, BrowserExecution, BrowserObservation
from .docker import (
    BrowserRuntime,
    EvidenceSink,
    InspectorHandle,
    SubprocessDockerClient,
)
from .http import browser_service_from_environment
from .objects import BrowserEvidenceSink
from .service import BrowserService
from .store import now


def _pre_actuation_failure(
    request: dict[str, Any], observation: BrowserObservation, session_id: str
) -> bool:
    """Accept only the inspector's narrow, fixed target-resolution failure proof."""
    if (
        observation.session_id != session_id
        or request.get("type") not in {"click", "fill", "select", "press"}
        or not isinstance(request.get("selector"), str)
        or not request["selector"]
        or observation.write_started is not False
        or not isinstance(observation.extracted, dict)
    ):
        return False
    proof = observation.extracted.get("pre_actuation_failure")
    if not isinstance(proof, dict) or set(proof) != {"phase", "category"}:
        return False
    errors = {
        "target_missing": "controlled target missing before actuation",
        "target_ambiguous": "controlled target ambiguous before actuation",
    }
    category = proof.get("category")
    return (
        proof.get("phase") == "target_resolution"
        and isinstance(category, str)
        and category in errors
        and observation.error == errors[category]
    )


class Runtime(Protocol):
    async def start_inspector(
        self,
        task_id: str,
        allowed_origins: tuple[str, ...],
        deadline: int,
        *,
        session_id: str,
        network_mode: str = "declared_origins",
        control_enabled: bool = False,
    ) -> InspectorHandle: ...
    async def read_frame(self, handle: InspectorHandle) -> dict[str, Any]: ...
    async def synchronize_control(
        self, handle: InspectorHandle, controller_kind: str, epoch: int
    ) -> BrowserObservation: ...
    async def observe(
        self, handle: InspectorHandle, action: dict[str, Any]
    ) -> BrowserObservation: ...
    async def execute(
        self,
        bundle: Any,
        inputs: dict[str, Any],
        limits: BrowserLimits,
        *,
        execution_id: str,
    ) -> BrowserExecution: ...
    async def cancel_execution(
        self, execution_id: str, task_id: str
    ) -> dict[str, Any]: ...
    async def cancel(self, handle: InspectorHandle) -> dict[str, Any]: ...
    async def cancel_inspector(
        self, task_id: str, *, session_id: str
    ) -> dict[str, Any]: ...


class BrowserWorker:
    def __init__(
        self,
        service: BrowserService,
        runtime: Runtime,
        project_id: str,
        owner: str | None = None,
        *,
        input_fetcher: Callable[[str, str, str], Awaitable[dict[str, Any]]]
        | None = None,
    ) -> None:
        self.service = service
        self.runtime = runtime
        self.project_id = project_id
        self.owner = owner or uuid.uuid4().hex
        self.input_fetcher = input_fetcher
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._handles: dict[str, InspectorHandle] = {}
        self._attempted_sessions: set[str] = set()
        self._canceling: set[str] = set()
        self._frame_capture_at: dict[str, tuple[int, float]] = {}

    async def tick(self) -> None:
        store = self.service.store
        store.expire_lost(self.project_id)
        self.service.inputs.purge_expired()
        for row in store.cancel_requests("execution", self.project_id, self.owner):
            if row["id"] not in self._canceling:
                self._canceling.add(row["id"])
                await self._cancel_active(row)
        for row in store.cancel_requests("session", self.project_id, self.owner):
            if row["id"] not in self._canceling:
                self._canceling.add(row["id"])
                await self._cancel_session(row)
        for session_id in tuple(self._handles):
            cleanup = store.claim_recording_cleanup(
                self.project_id, session_id, self.owner
            )
            if cleanup is not None and session_id not in self._canceling:
                self._canceling.add(session_id)
                await self._cancel_session(cleanup)
        for session_id, handle in tuple(self._handles.items()):
            pending = store.pending_control(self.project_id, session_id, self.owner)
            if pending is not None:
                kind = (
                    "human"
                    if pending["control_state"] == "takeover_pending"
                    else "agent"
                )
                try:
                    observation = await self.runtime.synchronize_control(
                        handle, kind, pending["control_epoch"] + 1
                    )
                    if observation.error:
                        raise RuntimeError("browser control synchronization failed")
                    store.complete_control(
                        self.project_id,
                        session_id,
                        self.owner,
                        write_channel_revoked=True,
                        expected_epoch=pending["control_epoch"],
                        observation=asdict(observation),
                    )
                except BaseException:
                    # Revocation/observation outcome is uncertain: never silently retry.
                    store.update(
                        "session", self.project_id, session_id, control_state="blocked"
                    )
        await self._refresh_frames()
        session = store.claim("session", self.project_id, self.owner, 630)
        if session is not None:
            task = asyncio.create_task(self._start_session(session))
            self._tasks[session["id"]] = task
            task.add_done_callback(self._on_done(session["id"]))
        action = store.claim("action", self.project_id, self.owner, 600)
        if action is not None:
            task = asyncio.create_task(self._observe(action))
            self._tasks[action["id"]] = task
            task.add_done_callback(self._on_done(action["id"]))
        execution = store.claim("execution", self.project_id, self.owner, 630)
        if execution is not None:
            task = asyncio.create_task(self._run_execution(execution))
            self._tasks[execution["id"]] = task
            task.add_done_callback(self._on_done(execution["id"]))

    async def _refresh_frames(self) -> None:
        frames = getattr(self.service, "frames", None)
        if frames is None:
            return
        frames.purge_expired()
        for session_id, handle in tuple(self._handles.items()):
            row = self.service.store.get("session", self.project_id, session_id)
            if (
                row is None
                or row.get("control_version") != "browser-control-v1"
                or row["status"] != "active"
                or row["cancel_requested"]
                or row["control_state"] in {"blocked", "closed"}
                or (row.get("deadline_at") or "") <= now()
                or (row.get("observer_until") or "") <= now()
            ):
                frames.delete(session_id)
                self._frame_capture_at.pop(session_id, None)
                continue
            previous = self._frame_capture_at.get(session_id)
            if (
                previous
                and previous[0] == row["control_epoch"]
                and time.monotonic() - previous[1] < 1
            ):
                continue
            try:
                frame = await self.runtime.read_frame(handle)
                current = self.service.store.get("session", self.project_id, session_id)
                if (
                    current
                    and not current["cancel_requested"]
                    and current["status"] == "active"
                    and current["control_state"] not in {"blocked", "closed"}
                    and (current.get("deadline_at") or "") > now()
                    and current["control_epoch"] == row["control_epoch"]
                    and frame.get("control_epoch") == row["control_epoch"]
                ):
                    frames.publish(
                        session_id, frame, control_epoch=row["control_epoch"]
                    )
                    self._frame_capture_at[session_id] = (
                        row["control_epoch"],
                        time.monotonic(),
                    )
                else:
                    frames.delete(session_id)
                    self._frame_capture_at.pop(session_id, None)
            except Exception:
                frames.delete(session_id)
                self._frame_capture_at.pop(session_id, None)

    def _on_done(self, resource_id: str) -> Any:
        def remove(_: asyncio.Task[None]) -> None:
            self._tasks.pop(resource_id, None)

        return remove

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)

    async def _start_session(self, row: dict[str, Any]) -> None:
        # Set before the runtime can perform its first side effect or await.
        self._attempted_sessions.add(row["id"])
        try:
            runtime = self.runtime
            options: dict[str, Any] = {}
            if row.get("control_version") == "browser-control-v1":
                options["control_enabled"] = True
            if row["request"].get("network_mode") == "public_web":
                options["network_mode"] = "public_web"
            handle = await runtime.start_inspector(
                row["task_id"],
                tuple(row["request"]["allowed_origins"]),
                row["request"]["deadline_seconds"],
                session_id=row["id"],
                **options,
            )
            self._handles[row["id"]] = handle
            if not self.service.store.publish_owned(
                "session",
                self.project_id,
                row["id"],
                self.owner,
                cancel_requested=False,
                status="active",
                observation={"cdp_url": handle.cdp_url},
            ):
                current = self.service.store.get("session", self.project_id, row["id"])
                if current and current["cancel_requested"]:
                    await self._cancel_session(current)
                else:
                    await self.runtime.cancel(handle)
                    self._handles.pop(row["id"], None)
        except BaseException:
            logging.getLogger(__name__).exception(
                "Browser session start failed: %s", row["id"]
            )
            current = self.service.store.get("session", self.project_id, row["id"])
            if current and not current["cancel_requested"]:
                self.service.store.publish_owned(
                    "session",
                    self.project_id,
                    row["id"],
                    self.owner,
                    cancel_requested=False,
                    status="unknown",
                )

    async def _observe(self, row: dict[str, Any]) -> None:
        reserved = self.service.store.reserve_dispatch(
            self.project_id, row["id"], self.owner
        )
        if reserved is None:
            return
        row = reserved
        phase = "resolve_handle"
        try:
            handle = self._handles[row["session_id"]]
            phase = "prepare_request"
            request = dict(row["request"])
            session = self.service.store.get(
                "session", self.project_id, row["session_id"]
            )
            if session and session.get("control_version") == "browser-control-v1":
                request.update(
                    actor_kind=row["actor_kind"],
                    control_epoch=row["control_epoch"],
                    actor_expires_at=row.get("actor_expires_at"),
                )
            if "input_ref" in request:
                probe = {
                    key: request[key]
                    for key in (
                        "selector",
                        "x",
                        "y",
                        "frame_sequence",
                        "viewport",
                        "actor_kind",
                        "control_epoch",
                        "actor_expires_at",
                    )
                    if key in request
                }
                probe.update(type="preflight", operation=request["type"])
                phase = "controlled_preflight"
                checked = await self.runtime.observe(handle, probe)
                safe = (checked.extracted or {}).get("preflight") or {}
                if checked.error or safe.get("safe") is not True:
                    self.service.inputs.discard(request["input_ref"])
                    self.service.store.publish_action(
                        self.project_id, row, self.owner, status="failed"
                    )
                    return
                phase = "controlled_input_fetch"
                if self.input_fetcher is None:
                    payload = self.service.consume_input_for_dispatch(
                        self.project_id, row["session_id"], row["id"]
                    )
                else:
                    payload = await self.input_fetcher(
                        self.project_id, row["session_id"], row["id"]
                    )
                input_key = "key" if request["type"] == "press" else "value"
                if set(payload) != {input_key} or not isinstance(
                    payload[input_key], str
                ):
                    raise ValueError("invalid ephemeral input payload")
                request.pop("input_ref")
                request.update(payload)
            phase = "confirm_dispatch"
            if not self.service.store.confirm_dispatch(
                self.project_id, row["session_id"], row["id"], self.owner
            ):
                return
            phase = "runtime_observe"
            observation = await self.runtime.observe(handle, request)
            phase = "validate_observation"
            no_actuation = handle.session_id == row[
                "session_id"
            ] and _pre_actuation_failure(request, observation, row["session_id"])
            if request.get("type") != "preflight":
                self._frame_capture_at.pop(row["session_id"], None)
                self.service.frames.delete(row["session_id"])
            if row["request"].get("input_ref") and observation.error:
                # A failed write never records supplied values or runtime diagnostics.
                observation = replace(
                    observation,
                    error="controlled input outcome unavailable",
                    bounded_snapshot="",
                    fields=(),
                    extracted=None,
                    result=None,
                    evidence=(),
                    screenshot_ref=None,
                )
            observation = replace(
                observation,
                session_id=row["session_id"],
                sequence=observation.sequence
                if session and session.get("control_version") == "browser-control-v1"
                else row["sequence"],
            )
            if observation.screenshot_ref is not None:
                self.service.verify_inspector_evidence(
                    self.project_id,
                    handle.task_id,
                    row["session_id"],
                    observation.screenshot_ref,
                )
            for item in observation.evidence:
                self.service.verify_inspector_evidence(
                    self.project_id,
                    handle.task_id,
                    row["session_id"],
                    item["object_ref"],
                )
            phase = "publish_observation"
            self.service.store.publish_action(
                self.project_id,
                row,
                self.owner,
                status=(
                    "unknown"
                    if row["request"].get("type")
                    not in {"snapshot", "read", "screenshot", "wait", "preflight"}
                    and not (
                        no_actuation
                        or (
                            observation.write_started is False
                            and not (
                                session
                                and session.get("control_version")
                                == "browser-control-v1"
                            )
                        )
                    )
                    else "failed"
                )
                if observation.error
                else "completed",
                observation=asdict(observation),
            )
        except BaseException as error:
            # Exception text/traceback may include protected inputs or credentials.
            # Fixed phases and type names diagnose version/transport failures safely.
            logging.getLogger(__name__).warning(
                "browser action unknown phase=%s type=%s", phase, type(error).__name__
            )
            self.service.store.publish_action(
                self.project_id, row, self.owner, status="unknown"
            )

    async def _cancel_session(self, row: dict[str, Any]) -> None:
        session_id = row["id"]
        self._frame_capture_at.pop(session_id, None)
        self.service.frames.delete(session_id)
        self.service.inputs.discard_session(self.project_id, session_id)
        handle = self._handles.get(session_id)
        interrupted = False
        try:
            if handle is not None:
                receipt = await self.runtime.cancel(handle)
            elif session_id not in self._attempted_sessions:
                start_task = self._tasks.get(session_id)
                if start_task is None:
                    raise RuntimeError("unowned session start")
                start_task.cancel()
                await asyncio.gather(start_task, return_exceptions=True)
                if session_id in self._attempted_sessions:
                    receipt = await self.runtime.cancel_inspector(
                        row["task_id"], session_id=session_id
                    )
                else:
                    receipt = {
                        "task_id": row["task_id"],
                        "stopped": True,
                        "status": "canceled",
                        "reason": "never_started",
                    }
            else:
                receipt = await self.runtime.cancel_inspector(
                    row["task_id"], session_id=session_id
                )
        except asyncio.CancelledError:
            interrupted = True
            receipt = {"task_id": row["task_id"], "stopped": False, "status": "unknown"}
        except BaseException:
            receipt = {"task_id": row["task_id"], "stopped": False, "status": "unknown"}
        if self.service.store.settle_session(
            self.project_id, session_id, self.owner, receipt
        ):
            if receipt.get("stopped") is True and receipt.get("status") == "canceled":
                self._handles.pop(session_id, None)
        if interrupted:
            raise asyncio.CancelledError

    async def _run_execution(self, row: dict[str, Any]) -> None:
        store = self.service.store
        try:
            bundle, inputs = self.service.frozen_bundle(
                self.project_id, row["bundle_id"]
            )
            limits = BrowserLimits(**row["request"]["limits"])
            if not store.publish_owned(
                "execution",
                self.project_id,
                row["id"],
                self.owner,
                cancel_requested=False,
                started_at=now(),
            ):
                return
            receipt = await self.runtime.execute(
                bundle,
                {"task_id": row["task_id"], "input_data": inputs},
                limits,
                execution_id=row["id"],
            )
            refs = [asdict(item) for item in receipt.evidence]
            if receipt.status == "passed":
                self.service.verify_evidence(
                    self.project_id,
                    refs,
                    task_id=row["task_id"],
                    execution_id=row["id"],
                )
            values: dict[str, Any] = dict(
                status=receipt.status,
                started_at=receipt.started_at,
                finished_at=receipt.finished_at,
                result=receipt.result,
                steps=list(receipt.steps),
                evidence=refs,
                failure_code=receipt.failure_code,
            )
            current = store.get("execution", self.project_id, row["id"])
            if current is None or current["status"] != "running":
                return
            if store.publish_owned(
                "execution",
                self.project_id,
                row["id"],
                self.owner,
                cancel_requested=bool(current["cancel_requested"]),
                **values,
            ):
                return
            # Cancellation may have changed between read and publish. A real
            # completed runtime receipt remains authoritative if it wins the CAS.
            latest = store.get("execution", self.project_id, row["id"])
            if (
                latest
                and latest["status"] == "running"
                and latest["cancel_requested"] != current["cancel_requested"]
            ):
                store.publish_owned(
                    "execution",
                    self.project_id,
                    row["id"],
                    self.owner,
                    cancel_requested=bool(latest["cancel_requested"]),
                    **values,
                )
        except BaseException:
            current = store.get("execution", self.project_id, row["id"])
            if current and current["status"] == "running":
                if current["cancel_requested"]:
                    await self._cancel_active(current)
                else:
                    store.publish_owned(
                        "execution",
                        self.project_id,
                        row["id"],
                        self.owner,
                        cancel_requested=False,
                        status="unknown",
                        finished_at=now(),
                        failure_code="runtime_unconfirmed",
                    )

    async def _cancel_active(self, row: dict[str, Any]) -> None:
        store = self.service.store
        if store.settle_unstarted_execution(self.project_id, row["id"], self.owner):
            return
        interrupted = False
        try:
            receipt = await self.runtime.cancel_execution(row["id"], row["task_id"])
            status = (
                "canceled"
                if receipt.get("stopped") is True
                and receipt.get("status") == "canceled"
                else "unknown"
            )
        except asyncio.CancelledError:
            interrupted = True
            status = "unknown"
        except BaseException:
            status = "unknown"
        store.publish_owned(
            "execution",
            self.project_id,
            row["id"],
            self.owner,
            cancel_requested=True,
            status=status,
            finished_at=now(),
            failure_code="canceled" if status == "canceled" else "stop_unconfirmed",
        )
        if interrupted:
            raise asyncio.CancelledError

    async def run(self, poll_seconds: float = 0.2) -> None:
        try:
            while True:
                await self.tick()
                await asyncio.sleep(poll_seconds)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self.service.store.request_owned_shutdown(self.project_id, self.owner)
        cleanup = [
            self._cancel_session(row)
            for row in self.service.store.cancel_requests(
                "session", self.project_id, self.owner
            )
        ] + [
            self._cancel_active(row)
            for row in self.service.store.cancel_requests(
                "execution", self.project_id, self.owner
            )
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*cleanup), timeout=35)
        except TimeoutError:
            pass  # Canceled cleanup coroutines settle unknown, never canceled.

        async def retry_stop(session_id: str, handle: InspectorHandle) -> None:
            try:
                await self.runtime.cancel(handle)
            except BaseException:
                pass
            self._handles.pop(session_id, None)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(
                        retry_stop(session_id, handle)
                        for session_id, handle in tuple(self._handles.items())
                    )
                ),
                timeout=30,
            )
        except TimeoutError:
            pass
        try:
            await asyncio.wait_for(self.drain(), timeout=5)
        except TimeoutError:
            for task in self._tasks.values():
                task.cancel()


def runtime_from_environment(sink: EvidenceSink | None, project_id: str) -> Runtime:
    mode = os.getenv("TAP_BROWSER_EXECUTION_MODE", "docker")
    if mode == "local":
        from .local import LocalBrowserRuntime

        return LocalBrowserRuntime(
            evidence_sink=sink,
            project_id=project_id,
            node_binary=os.getenv("TAP_BROWSER_LOCAL_NODE_BINARY", "node"),
            test_target=os.getenv("TAP_BROWSER_LOCAL_TEST_TARGET"),
            upstream_proxy=os.getenv("TAP_BROWSER_LOCAL_UPSTREAM_PROXY"),
            executable_path=os.getenv("TAP_BROWSER_LOCAL_EXECUTABLE_PATH"),
            headless=os.getenv("TAP_BROWSER_LOCAL_HEADLESS", "true").lower() != "false",
        )
    if mode != "docker":
        raise ValueError("invalid browser execution mode")
    image = os.getenv("TAP_BROWSER_IMAGE")
    if not image:
        raise RuntimeError("TAP_BROWSER_IMAGE required")
    return BrowserRuntime(
        SubprocessDockerClient(),
        image,
        evidence_sink=sink,
        project_id=project_id,
    )


def input_fetcher_from_environment() -> (
    Callable[[str, str, str], Awaitable[dict[str, Any]]] | None
):
    if os.getenv("TAP_BROWSER_CONTROL_ENABLED", "false").lower() != "true":
        return None
    from .input_transport import WorkerInputClient

    worker_token = os.getenv("TAP_BROWSER_WORKER_TOKEN", "")
    if worker_token and worker_token in {
        os.getenv("TAP_BROWSER_ACCESS_TOKEN"),
        os.getenv("TAP_BROWSER_GATEWAY_TOKEN"),
    }:
        raise ValueError("browser worker credential must be distinct")
    return WorkerInputClient(
        os.getenv("TAP_BROWSER_API_ORIGIN", ""),
        os.getenv("TAP_BROWSER_WORKER_TOKEN", ""),
    )


def main() -> None:
    service, principal, _ = browser_service_from_environment()
    if service is None or principal is None:
        raise RuntimeError("TAP browser configuration required")
    sink = BrowserEvidenceSink(service.store, service.objects)
    runtime = runtime_from_environment(sink, principal.project_id)
    worker = BrowserWorker(
        service,
        runtime,
        principal.project_id,
        input_fetcher=input_fetcher_from_environment(),
    )

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        running = asyncio.create_task(
            worker.run(float(os.getenv("TAP_BROWSER_WORKER_POLL_SECONDS", "0.2")))
        )
        loop.add_signal_handler(signal.SIGTERM, running.cancel)
        try:
            await running
        finally:
            loop.remove_signal_handler(signal.SIGTERM)

    asyncio.run(serve())


if __name__ == "__main__":
    main()
