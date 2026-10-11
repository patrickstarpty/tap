"""`knowledge reparse` CLI: give ready PDF documents parsed by an earlier parser a new
revision that the current parser and chunker rebuild. Scope and project id are never
caller-supplied identity -- only the fixed Validation Project is operated on, matching
`knowledge_operator.py` and `graph_operator.py`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tap.entrypoints import tapper_runtime
from tap.entrypoints.tapper_runtime import OwnedResources, TapperSettings
from tap.modules.access.adapters.validation import VALIDATION_SCOPE, ValidationScopeProvider
from tap.modules.access.application.scope import RequestFacts
from tap.modules.knowledge.domain.documents import MediaType

_MIN_LIMIT = 1
_MAX_LIMIT = 500
# Only PDF parsing changed with the current parser version.
REPARSED_MEDIA_TYPES = frozenset({MediaType.PDF.value})


@dataclass(frozen=True, slots=True)
class ReparseOperation:
    project_id: str
    limit: int


def parse_arguments(arguments: Sequence[str] | None = None) -> ReparseOperation:
    parser = argparse.ArgumentParser(
        description=(
            "Re-parse the current local Validation Project's ready PDF documents whose "
            "current revision came from an earlier parser version. Each document gets a "
            "new revision of the same stored original and is queued for parsing, "
            "chunking, embedding and indexing by the running worker; the earlier "
            "revision and its artifacts are kept, so existing citations still resolve. "
            "Documents with managed chunks keep their edited chunks and are skipped. "
            "This opens a degraded window: a document is not searchable from the moment "
            "it is queued until its new revision is ready, and its graph is extracted "
            "again for the new revision. Re-running is safe; already re-parsed "
            "documents are not selected again."
        )
    )
    parser.add_argument("resource", choices=["knowledge"])
    parser.add_argument("command", choices=["reparse"])
    parser.add_argument("--project", required=True, choices=[VALIDATION_SCOPE.project_id])
    parser.add_argument("--limit", type=int, default=100)
    values = parser.parse_args(arguments)
    if not _MIN_LIMIT <= values.limit <= _MAX_LIMIT:
        parser.error(f"--limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}")
    return ReparseOperation(project_id=values.project, limit=values.limit)


async def run(*, settings: TapperSettings, operation: ReparseOperation) -> dict[str, int]:
    if type(settings) is not TapperSettings or type(operation) is not ReparseOperation:
        raise TypeError("knowledge reparse requires validated settings and operation")
    resources = OwnedResources()
    try:
        engine, sessions = tapper_runtime._open_database(settings)
        resources.push(engine)
        scope = await ValidationScopeProvider().current(RequestFacts())
        if scope.project_id != operation.project_id:
            raise ValueError("knowledge reparse scope differs from the Validation Project")
        documents = tapper_runtime._build_document_repository(
            sessions, scope=scope, default_chat_model=settings.default_chat_model
        )
        requeued, skipped = await documents.reparse_stale_revisions(
            media_types=REPARSED_MEDIA_TYPES, limit=operation.limit
        )
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("knowledge reparse settlement unexpectedly returned")
    await resources.aclose()
    return {"requeuedCount": requeued, "skippedCount": skipped}


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
            "Knowledge reparse failed; verify local configuration and retry.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
