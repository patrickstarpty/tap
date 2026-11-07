"""Worker-owned browser runtime for durable intents; no side-effect replay."""

from __future__ import annotations

import asyncio
import os
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
        self, task_id: str, allowed_origins: tuple[str, ...], deadline: int
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
        self._canceling: set[str] = set()

    async def tick(self) -> None:
        store = self.service.store
        store.expire_lost(self.project_id)
        for execution_id in tuple(self._tasks):
            row = store.get("execution", self.project_id, execution_id)
            if (
                row
                and row["cancel_requested"]
                and row["status"] == "running"
                and execution_id not in self._canceling
            ):
                self._canceling.add(execution_id)
                await self._cancel_active(row)
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
        try:
            runtime = self.runtime
            handle = await runtime.start_inspector(
                row["task_id"],
                tuple(row["request"]["allowed_origins"]),
                row["request"]["deadline_seconds"],
            )
            self._handles[row["id"]] = handle
            self.service.store.update(
                "session", self.project_id, row["id"], status="active"
            )
        except BaseException:
            self.service.store.update(
                "session", self.project_id, row["id"], status="unknown"
            )

    async def _observe(self, row: dict[str, Any]) -> None:
        try:
            handle = self._handles[row["session_id"]]
            observation = await self.runtime.observe(handle, row["request"])
            observation = replace(
                observation, session_id=row["session_id"], sequence=row["sequence"]
            )
            self.service.store.update(
                "action",
                self.project_id,
                row["id"],
                status="completed",
                observation=asdict(observation),
            )
        except BaseException:
            self.service.store.update(
                "action", self.project_id, row["id"], status="unknown"
            )

    async def _run_execution(self, row: dict[str, Any]) -> None:
        store = self.service.store
        try:
            bundle, inputs = self.service.frozen_bundle(
                self.project_id, row["bundle_id"]
            )
            limits = BrowserLimits(**row["request"]["limits"])
            store.update("execution", self.project_id, row["id"], started_at=now())
            receipt = await self.runtime.execute(
                bundle,
                {"task_id": row["task_id"], "input_data": inputs},
                limits,
                execution_id=row["id"],
            )
            current = store.get("execution", self.project_id, row["id"])
            if (
                current is None
                or current["status"] != "running"
                or current["cancel_requested"]
            ):
                return
            refs = [asdict(item) for item in receipt.evidence]
            if receipt.status == "passed":
                self.service.verify_evidence(
                    self.project_id,
                    refs,
                    task_id=row["task_id"],
                    execution_id=row["id"],
                )
            store.update(
                "execution",
                self.project_id,
                row["id"],
                status=receipt.status,
                started_at=receipt.started_at,
                finished_at=receipt.finished_at,
                result=receipt.result,
                steps=list(receipt.steps),
                evidence=refs,
                failure_code=receipt.failure_code,
            )
        except BaseException:
            current = store.get("execution", self.project_id, row["id"])
            if (
                current
                and current["status"] == "running"
                and not current["cancel_requested"]
            ):
                store.update(
                    "execution",
                    self.project_id,
                    row["id"],
                    status="unknown",
                    finished_at=now(),
                    failure_code="runtime_unconfirmed",
                )

    async def _cancel_active(self, row: dict[str, Any]) -> None:
        try:
            receipt = await self.runtime.cancel_execution(row["id"], row["task_id"])
            status = (
                "canceled"
                if receipt.get("stopped") is True
                and receipt.get("status") == "canceled"
                else "unknown"
            )
        except BaseException:
            status = "unknown"
        current = self.service.store.get("execution", self.project_id, row["id"])
        if (
            current
            and current["status"] == "running"
            and current["owner"] == self.owner
        ):
            self.service.store.update(
                "execution",
                self.project_id,
                row["id"],
                status=status,
                finished_at=now(),
                failure_code="canceled" if status == "canceled" else "stop_unconfirmed",
            )

    async def run(self, poll_seconds: float = 0.2) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(poll_seconds)


def main() -> None:
    service, principal, _ = browser_service_from_environment()
    if service is None or principal is None:
        raise RuntimeError("TAP browser configuration required")
    image = os.getenv("TAP_BROWSER_IMAGE")
    if not image:
        raise RuntimeError("TAP_BROWSER_IMAGE required")
    runtime = BrowserRuntime(
        SubprocessDockerClient(),
        image,
        evidence_sink=BrowserEvidenceSink(service.store, service.objects),
        project_id=principal.project_id,
    )
    worker = BrowserWorker(service, runtime, principal.project_id)
    asyncio.run(worker.run(float(os.getenv("TAP_BROWSER_WORKER_POLL_SECONDS", "0.2"))))


if __name__ == "__main__":
    main()
