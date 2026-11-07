"""Worker-owned browser runtime for durable intents; no side-effect replay."""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from dataclasses import asdict, replace
from typing import Any, Protocol

from .contracts import BrowserLimits, BrowserExecution, BrowserObservation
from .docker import BrowserRuntime, InspectorHandle, SubprocessDockerClient
from .http import browser_service_from_environment
from .objects import BrowserEvidenceSink
from .service import BrowserService
from .store import now


class Runtime(Protocol):
    async def start_inspector(
        self,
        task_id: str,
        allowed_origins: tuple[str, ...],
        deadline: int,
        *,
        session_id: str,
    ) -> InspectorHandle: ...
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
    ) -> None:
        self.service = service
        self.runtime = runtime
        self.project_id = project_id
        self.owner = owner or uuid.uuid4().hex
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._handles: dict[str, InspectorHandle] = {}
        self._attempted_sessions: set[str] = set()
        self._canceling: set[str] = set()

    async def tick(self) -> None:
        store = self.service.store
        store.expire_lost(self.project_id)
        for row in store.cancel_requests("execution", self.project_id, self.owner):
            if row["id"] not in self._canceling:
                self._canceling.add(row["id"])
                await self._cancel_active(row)
        for row in store.cancel_requests("session", self.project_id, self.owner):
            if row["id"] not in self._canceling:
                self._canceling.add(row["id"])
                await self._cancel_session(row)
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
            handle = await runtime.start_inspector(
                row["task_id"],
                tuple(row["request"]["allowed_origins"]),
                row["request"]["deadline_seconds"],
                session_id=row["id"],
            )
            self._handles[row["id"]] = handle
            if not self.service.store.publish_owned(
                "session",
                self.project_id,
                row["id"],
                self.owner,
                cancel_requested=False,
                status="active",
            ):
                current = self.service.store.get("session", self.project_id, row["id"])
                if current and current["cancel_requested"]:
                    await self._cancel_session(current)
                else:
                    await self.runtime.cancel(handle)
                    self._handles.pop(row["id"], None)
        except BaseException:
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
        try:
            handle = self._handles[row["session_id"]]
            observation = await self.runtime.observe(handle, row["request"])
            observation = replace(
                observation, session_id=row["session_id"], sequence=row["sequence"]
            )
            if observation.screenshot_ref is not None:
                self.service.verify_inspector_evidence(
                    self.project_id,
                    handle.task_id,
                    row["session_id"],
                    observation.screenshot_ref,
                )
            self.service.store.publish_action(
                self.project_id,
                row,
                self.owner,
                status="completed",
                observation=asdict(observation),
            )
        except BaseException:
            self.service.store.publish_action(
                self.project_id, row, self.owner, status="unknown"
            )

    async def _cancel_session(self, row: dict[str, Any]) -> None:
        session_id = row["id"]
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


def main() -> None:
    service, principal, _ = browser_service_from_environment()
    if service is None or principal is None:
        raise RuntimeError("TAP browser configuration required")
    image = os.getenv("TAP_BROWSER_IMAGE")
    if not image:
        raise RuntimeError("TAP_BROWSER_IMAGE required")
    sink = BrowserEvidenceSink(service.store, service.objects)
    runtime = BrowserRuntime(
        SubprocessDockerClient(),
        image,
        evidence_sink=sink,
        project_id=principal.project_id,
    )
    worker = BrowserWorker(service, runtime, principal.project_id)

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
