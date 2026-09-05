"""Loopback-only Uvicorn entrypoint with lazy Tapper runtime lifespan ownership."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from enum import Enum
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request

from tap.entrypoints.tapper_runtime import TapperSettings
from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices


class ApiRuntime(Protocol):
    http_services: HttpServices

    async def aclose(self) -> None: ...


RuntimeFactory = Callable[[TapperSettings], Awaitable[ApiRuntime]]


class _ApiLifecycleFailure(Enum):
    SHUTDOWN = "shutdown"


def build_runtime_app(
    settings: TapperSettings,
    *,
    runtime_factory: RuntimeFactory | None = None,
) -> FastAPI:
    if not isinstance(settings, TapperSettings):
        raise TypeError("Tapper API requires validated settings")

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        factory = runtime_factory
        if factory is None:
            from tap.entrypoints.tapper_runtime import create_api_runtime

            factory = create_api_runtime
        try:
            runtime = await factory(settings)
        except Exception:
            raise RuntimeError("Tapper API runtime startup failed.") from None
        app.state.http_services = runtime.http_services
        app.state.failure_controller = getattr(runtime, "failure_controller", None)
        try:
            yield
        finally:
            app.state.failure_controller = None
            try:
                await runtime.aclose()
            except BaseException:
                app.state._tapper_lifecycle_failure = _ApiLifecycleFailure.SHUTDOWN
                raise RuntimeError("Tapper API runtime shutdown failed.") from None

    runtime_app = create_app(lifespan=lifespan)
    runtime_app.state._tapper_lifecycle_failure = None
    if settings.e2e_mode:
        _register_e2e_failure_route(runtime_app)
    return runtime_app


def _register_e2e_failure_route(runtime_app: FastAPI) -> None:
    @runtime_app.post(
        "/__e2e/fail-next/{stage}",
        include_in_schema=False,
    )
    async def fail_next_stage(stage: str, request: Request) -> dict[str, str]:
        if stage not in {"parsing", "embedding", "publishing"}:
            raise HTTPException(status_code=404, detail="unknown E2E failure stage")
        if await request.body():
            raise HTTPException(status_code=422, detail="E2E failure control accepts no body")
        controller = getattr(request.app.state, "failure_controller", None)
        arm = getattr(controller, "arm", None)
        if not callable(arm):
            raise HTTPException(status_code=503, detail="E2E failure control is unavailable")
        status = await arm(stage)
        if status not in {"armed", "already-armed"}:
            raise HTTPException(status_code=503, detail="E2E failure control is unavailable")
        return {"stage": stage, "status": status}


app = create_app()


def main(environment: Mapping[str, str] | None = None) -> None:
    import uvicorn

    from tap.operations.milvus.client import suppress_pymilvus_rpc_logging

    values = os.environ if environment is None else environment
    settings = TapperSettings.from_mapping(values)
    with suppress_pymilvus_rpc_logging():
        runtime_app = build_runtime_app(settings)
        uvicorn.run(
            runtime_app,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,
            access_log=False,
        )
        if (
            getattr(runtime_app.state, "_tapper_lifecycle_failure", None)
            is _ApiLifecycleFailure.SHUTDOWN
        ):
            raise RuntimeError("Tapper API runtime shutdown failed.") from None


class _UvicornFailureFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            record.msg = "Tapper API server error suppressed."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


@contextmanager
def _suppress_uvicorn_failure_details() -> Iterator[None]:
    logger = logging.getLogger("uvicorn.error")
    failure_filter = _UvicornFailureFilter()
    logger.addFilter(failure_filter)
    try:
        yield
    finally:
        logger.removeFilter(failure_filter)


def cli(environment: Mapping[str, str] | None = None) -> int:
    try:
        with _suppress_uvicorn_failure_details():
            main(environment)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print(
            "Tapper API failed; check local provider configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
