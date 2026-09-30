"""Chat-owned usage statistics for prompt suggestions: which knowledge
sources the current actor and other actors relied on in their completed
turns. Lives in chat (not entrypoints) because conversation/turn tables are
chat's own, not a Knowledge internal."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import turn_input_snapshot
from tap.platform.db.project_scope import require_project_scope, scope_predicates

# JSON_TABLE has no SQLAlchemy Core representation (no function-table
# construct in this SQLAlchemy version), so popular_sources below stays raw
# SQL; personal() has no such constraint and uses the existing chat_turn /
# turn_input_snapshot Table objects instead, which also gets the JSON
# "snapshot" column decoded by the driver instead of returned as a raw string.

# Other actors' data enters this query only as (source_id, count): the
# question text (ct.message / tis.snapshot->>'$.message') is never selected,
# so it can never reach a prompt suggestion model input or API response.
_POPULAR_SOURCES_QUERY = text(
    "SELECT rr.source_id AS source_id, COUNT(*) AS usage_count "
    "FROM chat_turn ct "
    "JOIN turn_input_snapshot tis "
    "  ON tis.project_id = ct.project_id AND tis.turn_id = ct.turn_id "
    "JOIN JSON_TABLE("
    "  tis.snapshot, '$.resolved_resources[*]' "
    "  COLUMNS (source_id VARCHAR(255) PATH '$.source_id')"
    ") AS rr ON TRUE "
    "WHERE ct.enterprise_id = :enterprise_id AND ct.project_id = :project_id "
    "  AND ct.actor_id != :excluding_actor_id AND ct.state = 'completed' "
    "GROUP BY rr.source_id "
    "ORDER BY usage_count DESC, rr.source_id "
    "LIMIT :row_limit"
)


class MysqlSuggestionUsage:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def personal(
        self, actor_id: str, *, limit: int
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(turn_input_snapshot.c.snapshot)
                        .select_from(
                            chat_turn.join(
                                turn_input_snapshot,
                                (turn_input_snapshot.c.project_id == chat_turn.c.project_id)
                                & (turn_input_snapshot.c.turn_id == chat_turn.c.turn_id),
                            )
                        )
                        .where(
                            *scope_predicates(chat_turn, self._scope),
                            chat_turn.c.actor_id == actor_id,
                            chat_turn.c.state == "completed",
                        )
                        .order_by(chat_turn.c.created_at.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        questions: list[str] = []
        source_ids: list[str] = []
        seen_sources: set[str] = set()
        for row in rows:
            snapshot = row["snapshot"]
            message = snapshot.get("message")
            if isinstance(message, str) and message:
                questions.append(message)
            for resource in snapshot.get("resolved_resources") or ():
                source_id = resource.get("source_id")
                if source_id and source_id not in seen_sources:
                    seen_sources.add(source_id)
                    source_ids.append(source_id)
        return tuple(questions), tuple(source_ids)

    async def popular_sources(
        self, *, excluding_actor_id: str, limit: int
    ) -> tuple[tuple[str, int], ...]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        _POPULAR_SOURCES_QUERY,
                        {
                            "enterprise_id": self._scope.enterprise_id,
                            "project_id": self._scope.project_id,
                            "excluding_actor_id": excluding_actor_id,
                            "row_limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return tuple((cast(str, row["source_id"]), int(row["usage_count"])) for row in rows)
