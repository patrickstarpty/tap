#!/usr/bin/env python3
"""Ensure only Tapper's two Blob containers and exact Milvus target."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from tap.entrypoints.tapper_runtime import (
    OwnedResources,
    TapperSettings,
    _artifacts_private,
    _build_document_index,
    _create_blob,
    _create_database,
    _create_document_index,
    _create_projection_coordinator,
    _open_document_clients,
)
from tap.modules.knowledge.adapters.milvus_documents import (
    IndexTargetProvisioningFailed,
    IndexTargetReceipt,
    ReadyRevisionArtifacts,
    RebuildReceipt,
)
from tap.modules.knowledge.adapters.mysql_operations import MysqlOperationRepository
from tap.operations.milvus.client import suppress_pymilvus_rpc_logging

_TARGET_ENSURE_STAGES = (
    "authority",
    "discovery",
    "collection-create",
    "collection-schema-observe",
    "collection-schema-envelope",
    "collection-schema-properties",
    "collection-schema-aliases",
    "collection-schema-identity",
    "collection-schema-metadata",
    "collection-schema-fields",
    "collection-schema-functions",
    "collection-schema-vector",
    "collection-schema-binding",
    "indexes",
    "load",
    "grants",
    "alias",
    "migration-required",
    "authority-sync",
    "cleanup",
)
_ENSURE_STAGES = frozenset(
    {
        "configuration",
        "database",
        "blob-containers",
        "milvus-client",
        "milvus-target",
        *(f"milvus-target-{stage}" for stage in _TARGET_ENSURE_STAGES),
    }
)


def validate_migration_profile(settings, action: str, retained: str | None) -> None:
    if action == "migrate-v1-to-v2":
        if settings.schema_version != "doc-schema-v2" or retained is not None:
            raise ValueError("migration requires the explicit destination v2 profile")
    elif action == "rollback-v2-to-v1":
        if (
            settings.schema_version != "doc-schema-v1"
            or not isinstance(retained, str)
            or re.fullmatch(r"kb_doc_v1_tapper_demo(?:_[0-9a-f]{12})?", retained)
            is None
        ):
            raise ValueError(
                "rollback requires the explicit v1 profile and retained target"
            )
    else:
        raise ValueError("unknown migration action")


def _docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", *arguments], check=True, capture_output=True, text=True, timeout=15
    )
    return result.stdout


def verify_owned_migration(settings, state: Path) -> None:
    """Validate declarative receipts; never execute their commands or paths."""
    from sqlalchemy.engine import make_url

    project = settings.compose_project
    if re.fullmatch(r"tap-task6a-[0-9a-f]{12}", project) is None:
        raise ValueError("migration requires an owned disposable project")
    receipt = json.loads((state / "ownership.json").read_text())
    if receipt.get("project") != project:
        raise ValueError("owned receipt project mismatch")
    containers = []
    for kind in ("container", "volume", "network"):
        ids = json.loads((state / f"{kind}-ids.json").read_text())
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 8
            or len(ids) != len(set(ids))
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", item) is None
                for item in ids
            )
        ):
            raise ValueError("invalid owned resource inventory")
        actual = _docker(
            kind, "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"
        ).split()
        if set(actual) != set(ids):
            raise ValueError("owned resource inventory changed")
        inspected = json.loads(_docker(kind, "inspect", *ids))
        if not isinstance(inspected, list) or len(inspected) != len(ids):
            raise ValueError("invalid owned resource inspection")
        for item in inspected:
            labels = (
                item.get("Config", {}).get("Labels", {})
                if kind == "container"
                else item.get("Labels", {})
            )
            if labels.get("com.docker.compose.project") != project:
                raise ValueError("owned resource labels changed")
            if kind == "container":
                containers.append(item)

    def bound_port(service: str, port: str) -> int:
        matches = [
            item
            for item in containers
            if item["Config"]["Labels"].get("com.docker.compose.service") == service
        ]
        if len(matches) != 1:
            raise ValueError("owned service is absent or ambiguous")
        bindings = matches[0].get("NetworkSettings", {}).get("Ports", {}).get(port)
        if (
            not isinstance(bindings, list)
            or len(bindings) != 1
            or bindings[0].get("HostIp") != "127.0.0.1"
        ):
            raise ValueError("owned service is not bound exclusively to loopback")
        return int(bindings[0]["HostPort"])

    mysql_service = receipt.get("migration_mysql_service")
    if mysql_service not in {"mysql", "mysql-cli", "mysql-cli-final"}:
        raise ValueError("owned migration MySQL service must be explicit")
    mysql_port = bound_port(mysql_service, "3306/tcp")
    for value in (settings.database_url, settings.alembic_database_url):
        url = make_url(value)
        if url.host != "127.0.0.1" or url.port != mysql_port:
            raise ValueError("database endpoint is not owned")
    milvus = urlsplit(settings.milvus_uri)
    if milvus.hostname != "127.0.0.1" or milvus.port != bound_port(
        "milvus", "19530/tcp"
    ):
        raise ValueError("Milvus endpoint is not owned")
    if settings.object_store_provider != "minio" or settings.legacy_azure_enabled:
        raise ValueError("migration requires only owned Knowledge MinIO artifacts")
    artifacts = urlsplit(settings.s3_endpoint)
    if artifacts.hostname != "127.0.0.1" or artifacts.port != bound_port(
        "tap-minio", "9000/tcp"
    ):
        raise ValueError("Knowledge artifact endpoint is not owned")


async def migrate_projection(
    settings: TapperSettings, action: str, retained: str | None, limit: int
) -> dict[str, object]:
    validate_migration_profile(settings, action, retained)
    if not 1 <= limit <= 500:
        raise ValueError("migration snapshot bound is invalid")
    resources = OwnedResources()
    try:
        engine, documents = await _create_database(settings)
        resources.push(engine)
        coordinator = _create_projection_coordinator(
            settings, engine, scope=documents.scope
        )
        resources.push(coordinator)
        parts = await _open_document_clients(settings)
        for client in (parts.provisioner, parts.writer, parts.reader):
            resources.push(client)
        old_settings = replace(
            settings,
            schema_version="doc-schema-v1",
            collection="kb_doc_v1_tapper_demo",
            corpus_version="tapper-demo-v1",
        )
        new_settings = replace(
            settings,
            schema_version="doc-schema-v2",
            collection="kb_doc_v2_tapper_demo",
            corpus_version="tapper-demo-v2",
        )
        old = _build_document_index(old_settings, coordinator, parts)
        current = _build_document_index(new_settings, coordinator, parts)
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        repository = MysqlOperationRepository(engine, scope=documents.scope)

        async def snapshot():
            records = []
            for work in await repository.ready_work(limit):
                versions = {item.index_version for item in work.manifest}
                if (
                    len(versions) != 1
                    or work.chunks_locator is None
                    or work.embeddings_locator is None
                ):
                    raise ValueError("migration artifact snapshot is incomplete")
                records.append(
                    ReadyRevisionArtifacts(
                        work,
                        await artifacts.read_chunks(work.chunks_locator),
                        await artifacts.read_embeddings(work.embeddings_locator),
                        versions.pop(),
                    )
                )
            return tuple(records)

        receipt: RebuildReceipt | IndexTargetReceipt
        if action == "migrate-v1-to-v2":
            receipt = await current.migrate_from_snapshot(old, snapshot)
        else:
            if retained is None:
                raise ValueError("rollback requires an explicit retained collection")
            receipt = await current.rollback_to(old, retained, snapshot)
        result = {
            "schemaVersion": 1,
            "action": action,
            "physicalCollection": receipt.physical_collection,
            "alias": receipt.alias,
        }
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("migration settlement unexpectedly returned")
    await resources.aclose()
    return result


class _EnsureStage:
    __slots__ = ("_value",)

    def __init__(self) -> None:
        self._value = "configuration"

    @property
    def value(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        if value not in _ENSURE_STAGES:
            raise ValueError("Tapper ensure stage is outside the closed set")
        self._value = value


async def ensure(
    settings: TapperSettings,
    *,
    stage: _EnsureStage | None = None,
) -> None:
    """Create and verify only resources whose fixed identities Tapper owns."""

    if not isinstance(settings, TapperSettings):
        raise TypeError("Tapper ensure requires validated settings")
    tracker = _EnsureStage() if stage is None else stage
    if not isinstance(tracker, _EnsureStage):
        raise TypeError("Tapper ensure requires a closed stage tracker")
    resources = OwnedResources()
    try:
        tracker.set("database")
        engine, _repository = await _create_database(settings)
        resources.push(engine)
        tracker.set("blob-containers")
        artifacts = _create_blob(settings)
        resources.push(artifacts)
        await artifacts.ensure_containers()
        if not await _artifacts_private(artifacts):
            raise RuntimeError("Tapper artifact storage is not private")
        tracker.set("milvus-client")
        index = await _create_document_index(settings, engine)
        resources.push(index)
        tracker.set("milvus-target")
        receipt = await index.ensure_target()
        if (
            receipt.physical_collection != settings.collection
            or receipt.alias != settings.alias
        ):
            raise RuntimeError("Tapper Milvus target identity mismatch")
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("Tapper ensure settlement unexpectedly returned")
    await resources.aclose()


def main(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "action", choices=("ensure", "migrate-v1-to-v2", "rollback-v2-to-v1")
    )
    parser.add_argument("--owned-state", type=Path)
    parser.add_argument("--retained-collection")
    parser.add_argument("--limit", type=int, default=100)
    arguments = parser.parse_args(argv)
    values = dict(os.environ) if environment is None else dict(environment)
    stage = _EnsureStage()
    try:
        settings = TapperSettings.from_mapping(values)
        if arguments.action != "ensure":
            validate_migration_profile(
                settings, arguments.action, arguments.retained_collection
            )
            if arguments.owned_state is None:
                raise ValueError("migration requires an owned state receipt")
            verify_owned_migration(settings, arguments.owned_state)
            with suppress_pymilvus_rpc_logging():
                result = asyncio.run(
                    migrate_projection(
                        settings,
                        arguments.action,
                        arguments.retained_collection,
                        arguments.limit,
                    )
                )
            print(json.dumps(result, sort_keys=True))
            return 0
        with suppress_pymilvus_rpc_logging():
            asyncio.run(ensure(settings, stage=stage))
    except IndexTargetProvisioningFailed as error:
        stage.set(f"milvus-target-{error.stage.value}")
        print(
            f"Tapper resource ensure failed at {stage.value}; "
            "check local middleware configuration.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print(
            f"Tapper resource ensure failed at {stage.value}; "
            "check local middleware configuration.",
            file=sys.stderr,
        )
        return 1
    print("Tapper resources ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
