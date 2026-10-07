"""`graph rebuild` CLI: requeue graph extraction for every published revision
under a rate-limited loop. Scope and project id are never caller-supplied
identity -- only the fixed Validation Project is ever operated on, matching
`knowledge_operator.py`'s one-command-at-a-time style."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from tap.entrypoints import tapper_runtime
from tap.entrypoints.graph_project_knowledge import MysqlCurrentRevisions
from tap.entrypoints.tapper_runtime import OwnedResources, TapperSettings
from tap.modules.access.adapters.validation import VALIDATION_SCOPE, ValidationScopeProvider
from tap.modules.access.application.scope import RequestFacts
from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_PROFILE_DIGEST
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.adapters.mysql_merge import MysqlProjectMergeQueue
from tap.modules.knowledge.adapters.mysql_documents import knowledge_document_revision
from tap.platform.db.project_scope import scope_predicates

_MIN_LIMIT = 1
_MAX_LIMIT = 500


@dataclass(frozen=True, slots=True)
class GraphOperation:
    command: str
    project_id: str
    limit: int
    interval_seconds: float


def parse_arguments(arguments: Sequence[str] | None = None) -> GraphOperation:
    parser = argparse.ArgumentParser(
        description="Operate on the current local Validation Project's merged graph"
    )
    parser.add_argument("resource", choices=["graph"])
    parser.add_argument("command", choices=["rebuild"])
    parser.add_argument("--project", required=True, choices=[VALIDATION_SCOPE.project_id])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    values = parser.parse_args(arguments)
    if not _MIN_LIMIT <= values.limit <= _MAX_LIMIT:
        parser.error(f"--limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}")
    if values.interval_seconds <= 0:
        parser.error("--interval-seconds must be positive")
    return GraphOperation(
        command=values.command,
        project_id=values.project,
        limit=values.limit,
        interval_seconds=values.interval_seconds,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def run(*, settings: TapperSettings, operation: GraphOperation) -> dict[str, int]:
    if type(settings) is not TapperSettings or type(operation) is not GraphOperation:
        raise TypeError("graph operator requires validated settings and operation")
    resources = OwnedResources()
    requeued_count = 0
    skipped_count = 0
    try:
        engine, sessions = tapper_runtime._open_database(settings)
        resources.push(engine)
        scope = await ValidationScopeProvider().current(RequestFacts())
        if scope.project_id != operation.project_id:
            raise ValueError("graph operator scope differs from the Validation Project")
        current_revisions = MysqlCurrentRevisions(sessions, scope=scope)
        revision_ids = sorted(await current_revisions.current_revision_ids(scope))[
            : operation.limit
        ]
        async with sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            knowledge_document_revision.c.revision_id,
                            knowledge_document_revision.c.chunks_blob_locator,
                        ).where(
                            *scope_predicates(knowledge_document_revision, scope),
                            knowledge_document_revision.c.revision_id.in_(revision_ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
        locator_by_revision = {row["revision_id"]: row["chunks_blob_locator"] for row in rows}
        jobs = MysqlGraphJobStore(sessions)
        for revision_id in revision_ids:
            if not locator_by_revision.get(revision_id):
                skipped_count += 1
                continue
            try:
                await jobs.reset_for_profile(
                    scope,
                    revision_id,
                    extraction_profile_digest=GRAPH_EXTRACTION_PROFILE_DIGEST,
                    model_alias=settings.default_chat_model,
                    now=_now(),
                )
                requeued_count += 1
            except ValueError:
                skipped_count += 1
            await asyncio.sleep(operation.interval_seconds)
        if requeued_count:
            await MysqlProjectMergeQueue(sessions).request(
                scope, reason="rebuild", now=_now()
            )
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("graph operator settlement unexpectedly returned")
    await resources.aclose()
    return {"requeuedCount": requeued_count, "skippedCount": skipped_count}


def cli(
    arguments: Sequence[str] | None = None, environment: Mapping[str, str] | None = None
) -> int:
    try:
        operation = parse_arguments(arguments)
        settings = TapperSettings.from_mapping(
            dict(os.environ) if environment is None else environment
        )
        result = asyncio.run(run(settings=settings, operation=operation))
        print(json.dumps(result, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(
            "Graph operation failed; verify local configuration and retry.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
