from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusHybridRequest,
    MilvusQueryRequest,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.operations.milvus.contracts import (
    MilvusGrant,
    MilvusHealthReport,
    MilvusProbeClients,
)
from tap.operations.milvus.health import run_health_probe

pytestmark = pytest.mark.asyncio


class RecordingAdmin:
    def __init__(self) -> None:
        self.dropped_collections: list[str] = []
        self.denied_collections: list[str] = []

    async def ensure_user(self, username: str, password: SecretStr) -> None:
        raise AssertionError("health must not bootstrap users")

    async def ensure_role(self, role_name: str) -> None:
        raise AssertionError("health must not bootstrap roles")

    async def replace_role_grants(
        self,
        role_name: str,
        grants: frozenset[MilvusGrant],
    ) -> None:
        raise AssertionError("health must not replace role grants")

    async def rotate_root_password(self, password: SecretStr) -> None:
        raise AssertionError("health must not rotate root")

    async def verify(self, collection_name: str) -> None:
        self.denied_collections.append(collection_name)


class RecordingProvisioner:
    def __init__(self, admin: RecordingAdmin) -> None:
        self.admin = admin
        self.calls: list[tuple[object, ...]] = []
        self.fail_drop_alias = False

    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None:
        self.calls.append(("create_collection", name, dict(schema)))

    async def create_indexes(self, name: str) -> None:
        self.calls.append(("create_indexes", name))

    async def grant_collection(self, name: str, role_name: str) -> None:
        self.calls.append(("grant_collection", name, role_name))

    async def revoke_collection(self, name: str, role_name: str) -> None:
        self.calls.append(("revoke_collection", name, role_name))

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


def probe_clients() -> tuple[
    MilvusProbeClients,
    RecordingAdmin,
    RecordingProvisioner,
    RecordingWriter,
    RecordingReader,
]:
    admin = RecordingAdmin()
    provisioner = RecordingProvisioner(admin)
    writer = RecordingWriter()
    reader = RecordingReader()
    return (
        MilvusProbeClients(
            admin=admin,
            provisioner=provisioner,
            writer=writer,
            reader=reader,
        ),
        admin,
        provisioner,
        writer,
        reader,
    )


async def test_health_probe_exercises_roles_and_cleans_only_its_collection() -> None:
    clients, admin, provisioner, writer, reader = probe_clients()

    report = await run_health_probe(clients, "probe_20260824_001")

    assert report == MilvusHealthReport(
        probe_id="probe_20260824_001",
        allowed_hits=1,
        denied_hits=0,
        cleanup_complete=True,
    )
    assert all(name.startswith("tap_health_probe_") for name in admin.dropped_collections)
    assert len(admin.dropped_collections) == 1
    assert admin.denied_collections == ["tap_health_probe_probe_20260824_001"]
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
    assert [call[0] for call in provisioner.calls][-5:] == [
        "drop_alias",
        "revoke_collection",
        "revoke_collection",
        "revoke_collection",
        "drop_collection",
    ]


async def test_health_probe_cleans_its_collection_when_reader_fails() -> None:
    clients, admin, _, _, reader = probe_clients()

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
    clients, admin, provisioner, _, _ = probe_clients()
    provisioner.fail_drop_alias = True

    with pytest.raises(RuntimeError, match="Milvus health cleanup failed"):
        await run_health_probe(clients, "probe_20260824_001")

    assert admin.dropped_collections == ["tap_health_probe_probe_20260824_001"]
