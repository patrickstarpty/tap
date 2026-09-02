#!/usr/bin/env python3
"""Five independent, bounded, and redacted local Athena dependency checks."""

from __future__ import annotations

import asyncio
import math
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping

from pydantic import SecretStr
from sqlalchemy import text

from tap.entrypoints.athena_runtime import (
    AthenaSettings,
    OwnedResources,
    _create_answer_backend,
    _create_blob,
    _create_database,
    _create_embeddings,
    _create_models_probe_client,
    _create_redis,
    _discover_alembic_head,
    _is_private_blob_container,
    _push_if_owned,
    _read_models_labels,
)
from tap.modules.knowledge.adapters.blob_artifacts import (
    ARTIFACTS_CONTAINER,
    ORIGINALS_CONTAINER,
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

_ORDER = ("mysql", "redis", "blob", "milvus", "models")
_REMEDIATION = {
    "mysql": "start-mysql",
    "redis": "start-redis",
    "blob": "start-blob",
    "milvus": "start-milvus",
    "models": "configure-models",
}
_PROVIDER_SETTINGS: tuple[str, ...] = ("DASHSCOPE_API_KEY",)

Probe = Callable[[AthenaSettings, Mapping[str, str]], Awaitable[bool]]


async def _check_mysql(settings: AthenaSettings, _values: Mapping[str, str]) -> bool:
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


async def _check_redis(settings: AthenaSettings, _values: Mapping[str, str]) -> bool:
    client = _create_redis(settings)
    try:
        return await client.ping() is True
    finally:
        await client.aclose()


async def _blob_canary(settings: AthenaSettings) -> bool:
    artifacts = _create_blob(settings)
    try:
        for container in (ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER):
            properties = await artifacts.container_properties(container)
            if not _is_private_blob_container(properties):
                return False
        name = f"readiness/canary-{secrets.token_hex(16)}"
        client = artifacts._service.get_blob_client(ARTIFACTS_CONTAINER, name)
        payload = secrets.token_bytes(32)
        matched = False
        try:
            await artifacts._bounded(client.upload_blob(payload, overwrite=False))
            download = await artifacts._bounded(client.download_blob())
            body = await artifacts._bounded(download.readall())
            matched = body == payload
        finally:
            await artifacts._bounded(client.delete_blob())
        return matched
    finally:
        await artifacts.aclose()


async def _check_blob(settings: AthenaSettings, _values: Mapping[str, str]) -> bool:
    return await _blob_canary(settings)


def _milvus_reader(
    settings: AthenaSettings,
) -> tuple[PyMilvusReader, MilvusIndexTarget]:
    target = MilvusIndexTarget(
        family=SourceFamily.DOC,
        alias=settings.alias,
        physical_name_prefix=settings.collection,
        schema_version=settings.schema_version,
        schema_sha256=doc_schema_sha256(),
        corpus_version=settings.corpus_version,
        embedding_model_version=settings.embedding_alias,
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


async def _check_milvus(settings: AthenaSettings, _values: Mapping[str, str]) -> bool:
    reader, target = _milvus_reader(settings)
    try:
        bound = await bind_target(reader, target)
        rows = await reader.query(
            MilvusQueryRequest(
                collection_name=bound.physical_collection,
                filter_expression=(
                    'chunk_id == "__athena_readiness_reserved_never_persisted__"'
                ),
                output_fields=("chunk_id",),
                limit=1,
            )
        )
        return rows == ()
    finally:
        await reader.close()


async def _check_models(settings: AthenaSettings, values: Mapping[str, str]) -> bool:
    if settings.e2e_mode:
        from tap.testing.deterministic_model import DeterministicAthenaModel

        model = DeterministicAthenaModel(dimension=settings.embedding_dimension)
        embedding = await model.embed("Athena deterministic readiness")
        vector = embedding.vector
        return (
            embedding.model_id == settings.embedding_alias
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
        answer_backend = _create_answer_backend(settings, embeddings=embeddings)
        _push_if_owned(resources, answer_backend.owner)
        client = _create_models_probe_client(settings)
        _push_if_owned(resources, client)
        if client is None:
            healthy = False
        else:
            labels = await _read_models_labels(client)
            required_labels = {settings.embedding_alias}
            if settings.answer_backend == "litellm":
                required_labels.add(settings.chat_alias)
            healthy = labels is not None and required_labels <= labels
            if healthy and answer_backend.readiness is not None:
                await answer_backend.readiness()
    except BaseException as error:
        await resources.aclose(error)
        raise AssertionError("model check settlement unexpectedly returned")
    await resources.aclose()
    return healthy


async def _safe_probe(
    probe: Probe,
    settings: AthenaSettings,
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
    settings: AthenaSettings,
    values: Mapping[str, str],
) -> dict[str, bool]:
    """Run all real probes concurrently so one broken constructor cannot hide the others."""

    probes: tuple[Probe, ...] = (
        _check_mysql,
        _check_redis,
        _check_blob,
        _check_milvus,
        _check_models,
    )
    results = await asyncio.gather(
        *(_safe_probe(probe, settings, values) for probe in probes)
    )
    return dict(zip(_ORDER, results, strict=True))


def main(environment: Mapping[str, str] | None = None) -> int:
    values = dict(os.environ) if environment is None else dict(environment)
    states = {name: False for name in _ORDER}
    try:
        settings = AthenaSettings.from_mapping(values)
        with suppress_pymilvus_rpc_logging():
            states = asyncio.run(checks(settings, values))
    except Exception:
        pass
    for name in _ORDER:
        if states.get(name) is True:
            print(f"{name} ok")
        else:
            print(f"{name} failed {_REMEDIATION[name]}")
    return 0 if all(states.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
