"""Process entrypoint for durable prompt suggestion refresh."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping

from tap.entrypoints.tapper_ingestion_worker import run
from tap.platform.messaging.redis_dispatch import DispatchWakeup


class IdleWakeups:
    """No dedicated Redis stream drives suggestion refresh; publishes, turn
    completions, and reads each request a refresh row directly in MySQL, and
    the worker loop discovers due rows by polling on a plain timer."""

    async def wait(self, *, max_wait_seconds: float) -> DispatchWakeup | None:
        await asyncio.sleep(max_wait_seconds)
        return None

    async def ack(self, wakeup: DispatchWakeup) -> None:
        return None


def main(environment: Mapping[str, str] | None = None) -> None:
    from tap.entrypoints.tapper_runtime import TapperSettings, create_suggestion_worker_runtime
    from tap.entrypoints.tracing_setup import start_tracing

    values = dict(os.environ) if environment is None else dict(environment)
    settings = TapperSettings.from_mapping(values)
    start_tracing("tap-ai-worker-suggestions", settings)
    asyncio.run(run(runtime_factory=create_suggestion_worker_runtime, settings=settings))


def cli(environment: Mapping[str, str] | None = None) -> int:
    try:
        main(environment)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print(
            "Tapper suggestion worker failed; check local provider configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
