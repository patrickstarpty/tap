#!/usr/bin/env python3
"""Ensure only Tapper's two Blob containers and exact Milvus target."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence

from tap.entrypoints.tapper_runtime import (
    TapperSettings,
    OwnedResources,
    _create_blob,
    _create_database,
    _create_document_index,
    _is_private_blob_container,
)
from tap.modules.knowledge.adapters.blob_artifacts import (
    ARTIFACTS_CONTAINER,
    ORIGINALS_CONTAINER,
)
from tap.modules.knowledge.adapters.milvus_documents import (
    IndexTargetProvisioningFailed,
)
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
        for container in (ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER):
            properties = await artifacts.container_properties(container)
            if not _is_private_blob_container(properties):
                raise RuntimeError("Tapper Blob container is not private")
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
    parser.add_argument("action", choices=("ensure",))
    arguments = parser.parse_args(argv)
    if arguments.action != "ensure":
        raise AssertionError("argparse accepted an unknown action")
    values = dict(os.environ) if environment is None else dict(environment)
    stage = _EnsureStage()
    try:
        settings = TapperSettings.from_mapping(values)
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
