"""Dedicated polling worker for the enabled local native workflow slice."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Mapping

from tap.entrypoints.tapper_ingestion_worker import run
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime, WorkflowProjection
from tap.modules.workflows.application.recovery import RecoveryService
from tap.modules.workflows.ports.store import WorkflowStore

logger = logging.getLogger(__name__)


class WorkflowWorker:
    def __init__(
        self,
        *,
        scope: ProjectScopeContext,
        store: WorkflowStore,
        admission: WorkflowAdmissionCoordinator,
        runtime: NativeWorkflowRuntime,
        projection: WorkflowProjection,
    ) -> None:
        self._scope = scope
        self._store = store
        self._admission = admission
        self._runtime = runtime
        self._projection = projection
        self._recovery = RecoveryService(store, runtime)

    async def run_once(self, limit: int) -> int:
        await self._admission.repair(limit=limit)
        candidates = await self._store.scan_work(self._scope, limit)
        for task_id, status in candidates:
            try:
                if status in ("running", "pausing", "canceling", "recovering"):
                    await self._recovery.reconcile(self._scope, task_id)
                else:
                    await self._runtime.start(self._scope, task_id)
            except Exception:
                logger.exception("native workflow task pass failed: %s", task_id)
        await self._projection.repair(self._scope, limit)
        return len(candidates)


def main(environment: Mapping[str, str] | None = None) -> None:
    from tap.entrypoints.tapper_runtime import TapperSettings, create_workflow_worker_runtime
    from tap.entrypoints.tracing_setup import start_tracing

    settings = TapperSettings.from_mapping(
        dict(os.environ) if environment is None else dict(environment)
    )
    if not settings.workflow_enabled:
        raise ValueError("native workflow worker is disabled")
    start_tracing("tap-ai-worker-workflows", settings)
    asyncio.run(run(runtime_factory=create_workflow_worker_runtime, settings=settings))


def cli(environment: Mapping[str, str] | None = None) -> int:
    try:
        main(environment)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print("Tapper workflow worker failed; check local runtime configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
