"""Shared Turn -> Agent key resolution (spec 2.3), used by both
`TurnTableRuntimeMetrics` and `MysqlAgentTeamStore` so this mapping exists in
exactly one place.

A Turn's Agent attribution is frozen at the time it ran:
`turn_input_snapshot.snapshot["agent_revision_id"]`, resolved in Python to
`ai_agent_revision.agent_id`. An empty/missing revision id means no Agent was
selected (`TAPPER_DEFAULT_AGENT_KEY`); a revision id that resolves to no row
(the Agent or revision no longer exists) means `UNKNOWN_AGENT_KEY`.

`ai_agent_revision` is imported lazily inside `resolve_agent_keys` rather than
at module scope: `tap.modules.ai.adapters.mysql` imports
`tap.modules.agent_team.adapters.mysql` (for `agent_maintenance_event`) at its
own module scope, so an eager import here would deadlock that cycle the first
time either module loads before the other has finished executing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tap.modules.agent_team.domain.models import TAPPER_DEFAULT_AGENT_KEY, UNKNOWN_AGENT_KEY


async def resolve_agent_keys(
    session: AsyncSession,
    project_id: str,
    snapshots: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, str]:
    """Maps each key in `snapshots` (typically a `turn_id` or `feedback_id`)
    to the `agent_key` frozen by its `turn_input_snapshot`-shaped value
    (`{"agent_revision_id": ...}` or `None`)."""
    from sqlalchemy import select

    from tap.modules.ai.adapters.mysql import ai_agent_revision

    revision_ids = {
        snapshot.get("agent_revision_id")
        for snapshot in snapshots.values()
        if snapshot and snapshot.get("agent_revision_id")
    }
    agent_by_revision: dict[str, str] = {}
    if revision_ids:
        found = (
            await session.execute(
                select(ai_agent_revision.c.revision_id, ai_agent_revision.c.agent_id).where(
                    ai_agent_revision.c.project_id == project_id,
                    ai_agent_revision.c.revision_id.in_(revision_ids),
                )
            )
        ).all()
        agent_by_revision = {row.revision_id: row.agent_id for row in found}
    result: dict[str, str] = {}
    for key, snapshot in snapshots.items():
        revision_id = snapshot.get("agent_revision_id") if snapshot else None
        if not revision_id:
            result[key] = TAPPER_DEFAULT_AGENT_KEY
        else:
            result[key] = agent_by_revision.get(revision_id, UNKNOWN_AGENT_KEY)
    return result
