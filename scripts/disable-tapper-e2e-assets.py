"""Disable the owned deterministic AI assets between E2E restart phases."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    if (
        os.environ.get("TAP_DEMO_MODE") != "e2e"
        or os.environ.get("TAP_TAPPER_COMPOSE_PROJECT") != "tap-tapper-e2e"
    ):
        raise SystemExit("refusing to mutate non-owned AI assets")
    database_url = os.environ["TAP_DATABASE_URL"]
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for table, revision_id in (
                ("ai_agent_revision", "validation-knowledge-agent-v2"),
                ("skill_revision", "validation-citation-skill-v2"),
            ):
                result = await connection.execute(
                    text(
                        f"UPDATE {table} SET status='disabled' "
                        "WHERE enterprise_id='local' AND project_id='tapper-demo' "
                        "AND revision_id=:revision_id AND status='enabled'"
                    ),
                    {"revision_id": revision_id},
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"owned {table} revision was not enabled")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
