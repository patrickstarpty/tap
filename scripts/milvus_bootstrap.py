"""CLI for the opt-in local Milvus root bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from pymilvus import MilvusClient  # type: ignore[import-untyped]

from tap.operations.milvus.bootstrap import bootstrap_local_rbac
from tap.operations.milvus.client import connect_local_admin, local_role_credentials


async def _run() -> None:
    settings = dict(os.environ)
    admin = await connect_local_admin(settings, client_factory=MilvusClient)
    try:
        await bootstrap_local_rbac(admin, local_role_credentials(settings))
    finally:
        await admin.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap local Milvus RBAC")
    parser.parse_args()
    try:
        asyncio.run(_run())
    except Exception:
        print("Milvus RBAC bootstrap failed.", file=sys.stderr)
        return 1
    print("Milvus RBAC bootstrap passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
