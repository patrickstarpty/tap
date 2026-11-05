#!/usr/bin/env python3
"""Six independent, bounded, and redacted local Tapper dependency checks."""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy import text

from tap.entrypoints.tapper_runtime import (
    TapperSettings,
    OwnedResources,
    _create_blob,
    _create_database,
    _create_embeddings,
    _create_redis,
    _discover_alembic_head,
    _push_if_owned,
)
from tap.modules.knowledge.adapters.milvus.config import (
    MilvusIndexTarget,
    MilvusSearchConfig,
)
from tap.modules.knowledge.adapters.milvus.targets import bind_target
from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusQueryRequest,
    PyMilvusReader,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.operations.milvus.doc_schema import doc_schema_sha256
from tap.operations.milvus.client import suppress_pymilvus_rpc_logging

_ORDER = ("mysql", "redis", "blob", "milvus", "models", "langfuse")
_REMEDIATION = {
    "mysql": "start-mysql",
    "redis": "start-redis",
    "blob": "start-blob",
    "milvus": "start-milvus",
    "models": "configure-models",
    "langfuse": "start-langfuse",
}
_LOW_DISK_HEADROOM_KB = 5 * 1024 * 1024
_PROVIDER_SETTINGS: tuple[str, ...] = ("DASHSCOPE_API_KEY",)
_REPO_ROOT = Path(__file__).resolve().parent.parent

Probe = Callable[[TapperSettings, Mapping[str, str]], Awaitable[bool]]


async def _check_mysql(settings: TapperSettings, _values: Mapping[str, str]) -> bool:
    engine, _repository = await _create_database(settings)
    try:
        async with engine.connect() as connection:
            ping = (await connection.execute(text("SELECT 1"))).scalar_one()
            version = (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one()
        return ping == 1 and version == _discover_alembic_head()
    finally:
        await engine.dispose()


async def _check_redis(settings: TapperSettings, _values: Mapping[str, str]) -> bool:
    client = _create_redis(settings)
    try:
        return await client.ping() is True
    finally:
        await client.aclose()


async def _blob_canary(settings: TapperSettings) -> bool:
    artifacts = _create_blob(settings)
    try:
        from tap.platform.storage.objects import PutObjectRequest

        if not await artifacts.is_private():
            return False
        payload = secrets.token_bytes(32)

        async def content():
            yield payload

        staged = await artifacts.objects.put_staged(
            PutObjectRequest(content(), 32, "application/octet-stream")
        )
        try:
            return (await artifacts.objects.open_verified(staged.ref)).data == payload
        finally:
            await artifacts.objects.delete(staged.ref)
    finally:
        await artifacts.aclose()


async def _check_blob(settings: TapperSettings, _values: Mapping[str, str]) -> bool:
    return await _blob_canary(settings)


def _milvus_reader(
    settings: TapperSettings,
) -> tuple[PyMilvusReader, MilvusIndexTarget]:
    target = MilvusIndexTarget(
        family=SourceFamily.DOC,
        alias=settings.alias,
        physical_name_prefix=settings.collection,
        schema_version=settings.schema_version,
        schema_sha256=doc_schema_sha256(settings.schema_version),
        corpus_version=settings.corpus_version,
        embedding_model_version=settings.embedding_model,
        vector_dimension=settings.embedding_dimension,
    )
    config = MilvusSearchConfig(
        uri=settings.milvus_uri,
        database=settings.milvus_database,
        username=settings.milvus_reader_username,
        password=SecretStr(settings.milvus_reader_password),
        targets={SourceFamily.DOC: target},
        timeout_seconds=settings.milvus_timeout_seconds,
    )
    return PyMilvusReader(config), target


async def _check_milvus(settings: TapperSettings, _values: Mapping[str, str]) -> bool:
    reader, target = _milvus_reader(settings)
    try:
        bound = await bind_target(reader, target)
        rows = await reader.query(
            MilvusQueryRequest(
                collection_name=bound.physical_collection,
                filter_expression=(
                    'chunk_id == "__tapper_readiness_reserved_never_persisted__"'
                ),
                output_fields=("chunk_id",),
                limit=1,
            )
        )
        return rows == ()
    finally:
        await reader.close()


async def _check_models(settings: TapperSettings, values: Mapping[str, str]) -> bool:
    if settings.e2e_mode:
        model = _create_embeddings(settings)
        embedding = await model.embed("Tapper deterministic readiness")
        vector = embedding.vector
        return (
            embedding.model_id == settings.embedding_model
            and isinstance(vector, tuple)
            and len(vector) == settings.embedding_dimension
            and all(type(value) is float and math.isfinite(value) for value in vector)
            and math.isclose(
                math.sqrt(sum(value * value for value in vector)),
                1.0,
                rel_tol=1e-12,
            )
        )
    if any(not values.get(name, "").strip() for name in _PROVIDER_SETTINGS):
        return False
    resources = OwnedResources()
    try:
        embeddings = _create_embeddings(settings)
        _push_if_owned(resources, embeddings)
        health_problems = getattr(embeddings.gateway, "health_problems", None)
        healthy = health_problems is not None and not await health_problems()
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("model check settlement unexpectedly returned")
    await resources.aclose()
    return healthy


async def _check_langfuse(settings: TapperSettings, _values: Mapping[str, str]) -> bool:
    if settings.langfuse_host is None:
        return False
    host = settings.langfuse_host.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.ready_timeout_seconds) as client:
        # An authenticated endpoint, so wrong LANGFUSE_* keys fail the check
        # (the Agent team view needs a working key pair, not just a live host).
        response = await client.get(
            f"{host}/api/public/projects",
            auth=(
                settings.langfuse_public_key or "",
                settings.langfuse_secret_key or "",
            ),
        )
        return response.status_code == 200


def _docker_disk_headroom_hint(values: Mapping[str, str]) -> str | None:
    """Best-effort: free space on the Docker host's own filesystem (e.g.
    `docker info`'s `DockerRootDir`) is meaningless on Docker Desktop/macOS,
    since that path lives inside the Linux VM, not the host. Measure free
    space inside the VM instead, through a container already started by
    `make demo-up`/`langfuse-up` (the insights ClickHouse). Any failure
    (project not up, docker not queryable, unexpected output) silently
    produces no hint rather than fail the already-passing langfuse check."""

    insights_project = values.get("TAP_INSIGHTS_COMPOSE_PROJECT", "").strip() or (
        "tap-insights-local"
    )
    try:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                insights_project,
                "-f",
                str(_REPO_ROOT / "compose.yaml"),
                "exec",
                "-T",
                "clickhouse",
                "df",
                "-Pk",
                "/var/lib/clickhouse",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        lines = completed.stdout.strip().splitlines()
        if len(lines) < 2:
            return None
        available_kb = int(lines[1].split()[3])
    except Exception:
        return None
    if available_kb >= _LOW_DISK_HEADROOM_KB:
        return None
    free_gb = available_kb / (1024**2)
    return f"docker disk headroom low ({free_gb:.1f} GB free); langfuse/clickhouse may fail to start"


async def _safe_probe(
    probe: Probe,
    settings: TapperSettings,
    values: Mapping[str, str],
) -> bool:
    try:
        return await asyncio.wait_for(
            probe(settings, values),
            timeout=settings.ready_timeout_seconds,
        )
    except Exception:
        return False


async def checks(
    settings: TapperSettings,
    values: Mapping[str, str],
) -> dict[str, bool]:
    """Run all real probes concurrently so one broken constructor cannot hide the others."""

    probes: tuple[Probe, ...] = (
        _check_mysql,
        _check_redis,
        _check_blob,
        _check_milvus,
        _check_models,
        _check_langfuse,
    )
    results = await asyncio.gather(
        *(_safe_probe(probe, settings, values) for probe in probes)
    )
    return dict(zip(_ORDER, results, strict=True))


def main(environment: Mapping[str, str] | None = None) -> int:
    values = dict(os.environ) if environment is None else dict(environment)
    states = {name: False for name in _ORDER}
    try:
        settings = TapperSettings.from_mapping(values)
        with suppress_pymilvus_rpc_logging():
            states = asyncio.run(checks(settings, values))
    except Exception:
        pass
    for name in _ORDER:
        if states.get(name) is True:
            print(f"{name} ok")
            if name == "langfuse":
                hint = _docker_disk_headroom_hint(values)
                if hint is not None:
                    print(hint)
        else:
            print(f"{name} failed {_REMEDIATION[name]}")
    return 0 if all(states.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
