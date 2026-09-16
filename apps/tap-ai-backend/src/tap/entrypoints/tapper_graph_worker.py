"""Process entrypoint for durable Knowledge Graph extraction."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping

from tap.entrypoints.tapper_ingestion_worker import run


def main(environment: Mapping[str, str] | None = None) -> None:
    from tap.entrypoints.tapper_runtime import TapperSettings, create_graph_worker_runtime

    values = dict(os.environ) if environment is None else dict(environment)
    settings = TapperSettings.from_mapping(values)
    asyncio.run(run(runtime_factory=create_graph_worker_runtime, settings=settings))


def cli(environment: Mapping[str, str] | None = None) -> int:
    try:
        main(environment)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print(
            "Tapper graph worker failed; check local provider configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
