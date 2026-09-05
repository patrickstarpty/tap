"""Four fixed-Project CLI operations; caller arguments never supply identity."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from tap.entrypoints import tapper_runtime
from tap.entrypoints.tapper_runtime import OwnedResources, TapperSettings
from tap.modules.access.adapters.validation import VALIDATION_SCOPE, ValidationScopeProvider
from tap.modules.access.application.scope import RequestFacts
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_operations import MysqlOperationRepository
from tap.modules.knowledge.application.documents import DocumentService
from tap.modules.knowledge.application.operations import KnowledgeOperator
from tap.modules.knowledge.domain.operations import (
    COMMANDS,
    OperationBusy,
    OperationClaim,
    OperationRequest,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from tap.modules.knowledge.adapters.blob_artifacts import AzureBlobArtifactStore
    from tap.modules.knowledge.ports.documents import ArtifactStore


def parse_arguments(arguments: Sequence[str] | None = None) -> OperationRequest:
    parser = argparse.ArgumentParser(description="Operate on the current local Validation Project")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--project", required=True, choices=[VALIDATION_SCOPE.project_id])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--key", help="Reuse the original invocation key to read or recover its receipt"
    )
    values = parser.parse_args(arguments)
    return OperationRequest(
        command=values.command,
        limit=values.limit,
        idempotency_key=values.key if values.key is not None else str(uuid4()),
        correlation_id=str(uuid4()),
    )


class RuntimeOperationEffects:
    """Lazy provider composition, reached only after current identity authorization."""

    def __init__(
        self,
        *,
        settings: TapperSettings,
        engine: AsyncEngine,
        sessions: async_sessionmaker[AsyncSession],
        scope: ProjectScopeContext,
        resources: OwnedResources,
        repository: MysqlOperationRepository,
    ) -> None:
        if scope != VALIDATION_SCOPE or repository.scope != scope:
            raise ValueError("operator scope differs from Validation Project")
        self._scope = scope
        self._settings = settings
        self._engine = engine
        self._sessions = sessions
        self._resources = resources
        self._repository = repository
        self._artifacts: AzureBlobArtifactStore | None = None

    @property
    def scope(self) -> ProjectScopeContext:
        return self._scope

    def _blob(self) -> AzureBlobArtifactStore:
        if self._artifacts is None:
            self._artifacts = tapper_runtime._create_blob(self._settings)
            self._resources.push(self._artifacts)
        if self._artifacts.scope != self._scope:
            raise ValueError("operator artifact scope mismatch")
        return self._artifacts

    async def execute(self, command: str, *, limit: int) -> dict[str, int]:
        if command == "recover-uploads":
            documents = tapper_runtime._build_document_repository(self._sessions, scope=self._scope)
            service = DocumentService(
                repository=documents, artifacts=cast("ArtifactStore", self._blob())
            )
            count = await service.recover_uploads(
                worker_id=self._settings.worker_id,
                lease_duration=timedelta(seconds=60),
                limit=limit,
            )
            return {"recovered_count": count}
        if command == "scavenge-staging":
            pins = await self._repository.staging_pins()
            receipt = await self._blob().scavenge_staging(
                now=datetime.now(timezone.utc), visible_staging_keys=pins, limit=limit
            )
            return {"processed_count": receipt.scanned, "removed_count": len(receipt.removed)}
        if command == "rebuild-milvus":
            from tap.modules.knowledge.adapters.milvus_documents import ReadyRevisionArtifacts

            index = await tapper_runtime._create_document_index(self._settings, self._engine)
            self._resources.push(index)
            rebuilt = 0

            async def snapshot() -> tuple[ReadyRevisionArtifacts, ...]:
                nonlocal rebuilt
                work = await self._repository.ready_work(limit)
                records = []
                for item in work:
                    versions = {chunk.index_version for chunk in item.manifest}
                    if (
                        len(versions) != 1
                        or item.chunks_locator is None
                        or item.embeddings_locator is None
                    ):
                        raise ValueError("ready artifact snapshot is incomplete")
                    chunks = await self._blob().read_chunks(item.chunks_locator)
                    embeddings = await self._blob().read_embeddings(item.embeddings_locator)
                    records.append(ReadyRevisionArtifacts(item, chunks, embeddings, versions.pop()))
                rebuilt = len(records)
                return tuple(records)

            rebuild_receipt = await index.rebuild_from_snapshot(snapshot)
            return {"rebuilt_count": rebuilt, "skipped_count": len(rebuild_receipt.cleanup_facts)}
        if command == "reconcile-all":
            from tap.entrypoints.relay_reconciler import _build_relay
            from tap.platform.messaging.outbox_archive import OutboxArchive

            redis = tapper_runtime._create_redis(self._settings)
            self._resources.push(redis)
            recovery = OutboxArchive(self._sessions, scope=self._scope)
            redriven = await recovery.redrive_dead_letters(limit)
            # Use the already-validated runtime settings, never reread the environment.
            from tap.entrypoints.relay_reconciler import RelaySettings

            relay_settings = RelaySettings(
                database_url=self._settings.database_url,
                redis_url=self._settings.redis_url,
                batch_size=limit,
                poll_seconds=1,
                stream_name=self._settings.redis_stream,
                worker_id=self._settings.worker_id,
            )
            relay = _build_relay(relay_settings, self._sessions, redis, scope=self._scope)
            published = await relay.publish_pending(limit)
            archived = await recovery.archive_published(
                datetime.now(timezone.utc) - timedelta(days=7), limit
            )
            return {"processed_count": redriven + published + archived}
        raise ValueError("unknown operator command")


async def run(*, settings: TapperSettings, request: OperationRequest) -> OperationClaim:
    if type(settings) is not TapperSettings or type(request) is not OperationRequest:
        raise TypeError("operator requires validated settings and request")
    resources = OwnedResources()
    try:
        engine, sessions = tapper_runtime._open_database(settings)
        resources.push(engine)
        scope = await ValidationScopeProvider().current(RequestFacts())
        _, policy = tapper_runtime._create_validation_authority(engine)
        repository = MysqlOperationRepository(engine, scope=scope)
        effects = RuntimeOperationEffects(
            settings=settings,
            engine=engine,
            sessions=sessions,
            scope=scope,
            resources=resources,
            repository=repository,
        )
        result = await KnowledgeOperator(
            scope=scope, policy=policy, repository=repository, effects=effects
        ).run(request)
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("operator settlement unexpectedly returned")
    await resources.aclose()
    return result


def cli(
    arguments: Sequence[str] | None = None, environment: Mapping[str, str] | None = None
) -> int:
    try:
        request = parse_arguments(arguments)
        settings = TapperSettings.from_mapping(
            dict(os.environ) if environment is None else environment
        )
        print(f"Knowledge operation key: {request.idempotency_key}", file=sys.stderr)
        receipt = asyncio.run(run(settings=settings, request=request))
        if receipt.result is None:
            raise AssertionError("operator did not settle completion")
        print(
            json.dumps(
                {
                    "operationId": receipt.operation_id,
                    "key": request.idempotency_key,
                    "correlationId": receipt.correlation_id,
                    **receipt.result.to_dict(),
                },
                sort_keys=True,
            )
        )
        return 0 if receipt.result.outcome == "completed" else 1
    except OperationBusy:
        print("Knowledge operation is already running; retry with the same key.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(
            "Knowledge operation failed; verify local configuration and retry with the same key.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
