from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import ContextManager

import pytest
from pydantic import SecretStr

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusHybridRequest,
    MilvusQueryRequest,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.operations.milvus import client as milvus_client
from tap.operations.milvus.contracts import (
    MilvusDeniedProbe,
    MilvusGrant,
    MilvusHealthReport,
    MilvusProbeClients,
)
from tap.operations.milvus.health import run_health_probe

pytestmark = pytest.mark.asyncio


def _provider_log_scope() -> ContextManager[None]:
    factory = getattr(milvus_client, "suppress_pymilvus_rpc_logging", None)
    if factory is None:
        return nullcontext()
    return factory()


async def test_provider_log_scope_restores_filters_after_cancellation() -> None:
    provider_logger = logging.getLogger("pymilvus.decorators")
    original_filters = tuple(provider_logger.filters)
    entered = asyncio.Event()

    async def wait_forever() -> None:
        with _provider_log_scope():
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(wait_forever())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert tuple(provider_logger.filters) == original_filters


async def test_health_cli_suppresses_provider_rpc_details_before_generic_success() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import time
import scripts.milvus_health_probe as cli
from pymilvus.decorators import _log_rpc_error

async def pass_health(settings):
    details = (
        "SYNTHETIC_CREDENTIAL_MARKER SYNTHETIC_FILTER_MARKER "
        "SYNTHETIC_GROUP_MARKER SYNTHETIC_VECTOR_MARKER"
    )
    try:
        raise RuntimeError(details)
    except RuntimeError:
        _log_rpc_error("synthetic_call", "RPC error", details, time.monotonic())

cli._run_health = pass_health
raise SystemExit(cli.main())
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == "Milvus health probe passed.\n"
    assert completed.stderr == ""


async def test_health_cli_retries_a_transient_behavioral_readiness_failure() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import scripts.milvus_health_probe as cli

attempts = 0

async def transient_health(settings):
    global attempts
    attempts += 1
    if attempts < 3:
        raise RuntimeError("provider details must remain hidden")

cli._run_health = transient_health
cli._HEALTH_RETRY_DELAY_SECONDS = 0
raise SystemExit(cli.main())
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == "Milvus health probe passed.\n"
    assert completed.stderr == ""


class RecordingAdmin:
    def __init__(self) -> None:
        self.dropped_collections: list[str] = []

    async def ensure_user(self, username: str, password: SecretStr) -> None:
        raise AssertionError("health must not bootstrap users")

    async def ensure_role(self, role_name: str) -> None:
        raise AssertionError("health must not bootstrap roles")

    async def replace_user_roles(
        self,
        username: str,
        role_names: frozenset[str],
    ) -> None:
        raise AssertionError("health must not replace user roles")

    async def replace_role_grants(
        self,
        role_name: str,
        grants: frozenset[MilvusGrant],
    ) -> None:
        raise AssertionError("health must not replace role grants")

    async def rotate_root_password(self, password: SecretStr) -> None:
        raise AssertionError("health must not rotate root")


class RecordingProvisioner:
    def __init__(self, admin: RecordingAdmin) -> None:
        self.admin = admin
        self.calls: list[tuple[object, ...]] = []
        self.fail_drop_alias = False
        self.fail_grant_role: str | None = None

    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None:
        self.calls.append(("create_collection", name, dict(schema)))

    async def create_indexes(self, name: str) -> None:
        self.calls.append(("create_indexes", name))

    async def grant_collection(self, name: str, role_name: str) -> None:
        self.calls.append(("grant_collection", name, role_name))
        if role_name == self.fail_grant_role:
            raise RuntimeError("collection grant failed")

    async def revoke_collection(self, name: str, role_name: str) -> None:
        self.calls.append(("revoke_collection", name, role_name))

    async def create_alias(self, alias: str, collection_name: str) -> None:
        self.calls.append(("create_alias", alias, collection_name))

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        self.calls.append(("alter_alias", alias, collection_name))

    async def describe_alias(self, alias: str) -> str:
        self.calls.append(("describe_alias", alias))
        return "tap_health_probe_probe_20260824_001"

    async def drop_alias(self, alias: str) -> None:
        self.calls.append(("drop_alias", alias))
        if self.fail_drop_alias:
            raise RuntimeError("alias cleanup failed")

    async def drop_collection(self, name: str) -> None:
        self.calls.append(("drop_collection", name))
        self.admin.dropped_collections.append(name)


class RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def insert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None:
        self.calls.append(("insert", name, rows))

    async def upsert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None:
        self.calls.append(("upsert", name, rows))

    async def delete(self, name: str, chunk_ids: tuple[str, ...]) -> None:
        self.calls.append(("delete", name, chunk_ids))

    async def flush(self, name: str) -> None:
        self.calls.append(("flush", name))


class RecordingReader:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def describe_alias(self, alias: str) -> str:
        self.calls.append(("describe_alias", alias))
        return "tap_health_probe_probe_20260824_001"

    async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
        self.calls.append(("describe_collection", collection_name))
        return MilvusCollectionDescriptor(
            collection_name=collection_name,
            family=SourceFamily.DOC,
            schema_version="health-v1",
            schema_sha256="sha256:" + "a" * 64,
            corpus_version="health-v1",
            embedding_model_version="health-v1",
            vector_dimension=2,
            dynamic_fields_enabled=False,
            consistency_level="Strong",
        )

    async def hybrid_search(
        self,
        request: MilvusHybridRequest,
    ) -> tuple[Mapping[str, object], ...]:
        self.calls.append(("hybrid_search", request))
        return ({"chunk_id": "h_" + "1" * 64},)

    async def query(
        self,
        request: MilvusQueryRequest,
    ) -> tuple[Mapping[str, object], ...]:
        self.calls.append(("query", request))
        return ()

    async def close(self) -> None:
        self.calls.append(("close",))


class RecordingDeniedProbe(MilvusDeniedProbe):
    def __init__(self) -> None:
        self.collections: list[str] = []

    async def verify(self, collection_name: str) -> None:
        self.collections.append(collection_name)


def probe_clients() -> tuple[
    MilvusProbeClients,
    RecordingAdmin,
    RecordingProvisioner,
    RecordingWriter,
    RecordingReader,
    RecordingDeniedProbe,
]:
    admin = RecordingAdmin()
    provisioner = RecordingProvisioner(admin)
    writer = RecordingWriter()
    reader = RecordingReader()
    denied_probe = RecordingDeniedProbe()
    return (
        MilvusProbeClients(
            admin=admin,
            provisioner=provisioner,
            writer=writer,
            reader=reader,
            denied_probe=denied_probe,
        ),
        admin,
        provisioner,
        writer,
        reader,
        denied_probe,
    )


async def test_health_probe_exercises_roles_and_cleans_only_its_collection() -> None:
    clients, admin, provisioner, writer, reader, denied_probe = probe_clients()

    report = await run_health_probe(clients, "probe_20260824_001")

    assert report == MilvusHealthReport(
        probe_id="probe_20260824_001",
        allowed_hits=1,
        denied_hits=0,
        cleanup_complete=True,
    )
    assert all(name.startswith("tap_health_probe_") for name in admin.dropped_collections)
    assert len(admin.dropped_collections) == 1
    assert denied_probe.collections == ["tap_health_probe_probe_20260824_001"]
    create_alias = (
        "create_alias",
        "tap_health_alias_probe_20260824_001",
        "tap_health_probe_probe_20260824_001",
    )
    alter_alias = (
        "alter_alias",
        "tap_health_alias_probe_20260824_001",
        "tap_health_probe_probe_20260824_001",
    )
    assert provisioner.calls.index(create_alias) < provisioner.calls.index(alter_alias)
    assert ("describe_alias", "tap_health_alias_probe_20260824_001") in provisioner.calls
    assert [call[0] for call in writer.calls] == [
        "insert",
        "upsert",
        "flush",
        "delete",
        "flush",
    ]
    assert [call[0] for call in reader.calls] == [
        "describe_alias",
        "describe_collection",
        "hybrid_search",
        "query",
    ]
    assert [call[2] for call in provisioner.calls if call[0] == "grant_collection"] == [
        "tap_reader",
        "tap_writer",
    ]
    assert [call[0] for call in provisioner.calls][-4:] == [
        "drop_alias",
        "revoke_collection",
        "revoke_collection",
        "drop_collection",
    ]
    assert [call[2] for call in provisioner.calls if call[0] == "revoke_collection"] == [
        "tap_reader",
        "tap_writer",
    ]


async def test_health_probe_cleans_its_collection_when_reader_fails() -> None:
    clients, admin, _, _, reader, _ = probe_clients()

    async def fail_query(request: MilvusQueryRequest) -> tuple[Mapping[str, object], ...]:
        raise RuntimeError("provider detail must not be returned")

    reader.query = fail_query  # type: ignore[method-assign]

    try:
        await run_health_probe(clients, "probe_20260824_001")
    except RuntimeError as error:
        assert str(error) == "provider detail must not be returned"
    else:
        raise AssertionError("expected the injected reader failure")

    assert admin.dropped_collections == ["tap_health_probe_probe_20260824_001"]


async def test_health_cleanup_still_drops_collection_after_alias_cleanup_failure() -> None:
    clients, admin, provisioner, _, _, _ = probe_clients()
    provisioner.fail_drop_alias = True

    with pytest.raises(RuntimeError, match="Milvus health cleanup failed"):
        await run_health_probe(clients, "probe_20260824_001")

    assert admin.dropped_collections == ["tap_health_probe_probe_20260824_001"]


async def test_health_cleanup_tracks_a_role_before_a_partial_grant_failure() -> None:
    clients, admin, provisioner, _, _, denied_probe = probe_clients()
    provisioner.fail_grant_role = "tap_writer"

    with pytest.raises(RuntimeError, match="collection grant failed"):
        await run_health_probe(clients, "probe_20260824_001")

    revoked_roles = [call[2] for call in provisioner.calls if call[0] == "revoke_collection"]
    assert revoked_roles == ["tap_reader", "tap_writer"]
    assert admin.dropped_collections == ["tap_health_probe_probe_20260824_001"]
    assert denied_probe.collections == []


async def test_rendered_compose_supplies_the_same_minio_credentials_to_milvus() -> None:
    repository = Path(__file__).resolve().parents[5]
    environment = {
        **os.environ,
        "MILVUS_MINIO_ROOT_USER": "rendered-minio-user",
        "MILVUS_MINIO_ROOT_PASSWORD": "rendered-minio-password",
    }

    completed = subprocess.run(
        ["docker", "compose", "--profile", "milvus", "config", "--format", "json"],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    services = json.loads(completed.stdout)["services"]

    assert services["milvus-minio"]["environment"]["MINIO_ROOT_USER"] == "rendered-minio-user"
    assert (
        services["milvus-minio"]["environment"]["MINIO_ROOT_PASSWORD"] == "rendered-minio-password"
    )
    assert services["milvus"]["environment"]["MINIO_ACCESS_KEY_ID"] == "rendered-minio-user"
    assert services["milvus"]["environment"]["MINIO_SECRET_ACCESS_KEY"] == "rendered-minio-password"


async def test_mounted_milvus_config_retains_pinned_standalone_component_ports() -> None:
    repository = Path(__file__).resolve().parents[5]
    lines = (
        (repository / "deploy/local/milvus/milvus.yaml").read_text(encoding="utf-8").splitlines()
    )
    settings: dict[str, dict[str, str]] = {}
    section = ""
    for line in lines:
        if line and not line.startswith(" "):
            section = line[:-1] if line.endswith(":") else ""
            if section:
                settings.setdefault(section, {})
            continue
        if section and line.startswith("  ") and not line.startswith("    "):
            key, separator, value = line.strip().partition(":")
            if separator:
                settings[section][key] = value.partition(" #")[0].strip()

    assert "mixCoord" in settings
    assert {
        section: settings[section].get("port")
        for section in (
            "rootCoord",
            "proxy",
            "queryCoord",
            "queryNode",
            "dataCoord",
            "dataNode",
            "streamingNode",
        )
    } == {
        "rootCoord": "22125",
        "proxy": "19530",
        "queryCoord": "19531",
        "queryNode": "21123",
        "dataCoord": "13333",
        "dataNode": "21124",
        "streamingNode": "22222",
    }
    assert settings["proxy"].get("internalPort") == "19529"


async def test_mounted_milvus_config_differs_from_pinned_default_only_by_authorization() -> None:
    repository = Path(__file__).resolve().parents[5]
    config = (repository / "deploy/local/milvus/milvus.yaml").read_text(encoding="utf-8")
    enabled = "    authorizationEnabled: true"
    disabled = "    authorizationEnabled: false"

    assert config.count(enabled) == 1
    assert disabled not in config
    canonical_config = "\n".join(line.rstrip() for line in config.splitlines()) + "\n"
    assert config == canonical_config
    reconstructed_upstream = config.replace(enabled, disabled)
    assert hashlib.sha256(reconstructed_upstream.encode()).hexdigest() == (
        "e5cc17cec69d037881e4b638641b3bf9bc8dda48e12e71a798ed022ed1596bd7"
    )
