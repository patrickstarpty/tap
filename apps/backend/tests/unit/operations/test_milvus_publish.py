from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

import pytest

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusQueryRequest,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.operations.milvus import async_call as milvus_async_call
from tap.operations.milvus import client as milvus_client
from tap.operations.milvus.activation import CorpusActivationState, LocalCorpusActivator
from tap.operations.milvus.contracts import (
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusPublishClients,
    MilvusScopedGrant,
)
from tap.operations.milvus.fixtures import load_doc_fixture, manifest_sha256
from tap.operations.milvus.publish import (
    PublishRejected,
    finalize_old_physical,
    publish_fixture,
    tighten_fixture_acl,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "milvus" / "doc-fixture-v1.json"
pytestmark = pytest.mark.asyncio


class RecordingProvisioner:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.alias_target: str | None = None
        self.collections: set[str] = set()
        self.grants: dict[tuple[str, str], set[str]] = {}
        self.fail_after: str | None = None
        self.fail_alias_restore = False
        self.describe_alias_calls = 0

    async def collection_exists(self, name: str) -> bool:
        return name in self.collections

    async def has_collection_grant(self, name: str, role_name: str) -> bool:
        return bool(self.grants.get((name, role_name)))

    async def collection_grants(
        self,
        name: str,
        role_name: str,
    ) -> frozenset[MilvusScopedGrant]:
        return frozenset(
            MilvusScopedGrant(
                role_name=role_name,
                object_type="Collection",
                db_name="default",
                object_name=name,
                privilege=privilege,
            )
            for privilege in self.grants.get((name, role_name), set())
        )

    async def create_collection(self, name: str, schema: dict[str, object]) -> None:
        self.events.append("create_collection")
        if name in self.collections:
            raise RuntimeError("collection already exists")
        self.collections.add(name)
        self._fail_after("create_collection")

    async def create_indexes(self, name: str) -> None:
        self.events.append("create_indexes")

    async def grant_collection(self, name: str, role_name: str) -> None:
        self.events.append("grant_writer" if role_name == "tap_writer" else "grant_reader")
        expected = WRITER_PRIVILEGES if role_name == "tap_writer" else READER_TARGET_PRIVILEGES
        self.grants[(name, role_name)] = set(expected)
        self._fail_after("grant_writer" if role_name == "tap_writer" else "grant_reader")

    async def revoke_collection(self, name: str, role_name: str) -> None:
        self.events.append("revoke_writer" if role_name == "tap_writer" else "revoke_reader")
        self.grants.pop((name, role_name), None)
        self._fail_after("revoke_writer" if role_name == "tap_writer" else "revoke_reader")

    async def create_alias(self, alias: str, collection_name: str) -> None:
        self.alias_target = collection_name

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        self.events.append("alter_alias")
        if self.fail_alias_restore and collection_name.endswith("_old"):
            raise TimeoutError("alias restoration unavailable")
        self.alias_target = collection_name
        self._fail_after("alter_alias")

    async def describe_alias(self, alias: str) -> str | None:
        self.describe_alias_calls += 1
        if self.alias_target is not None and self.events and self.events[-1] == "alter_alias":
            self.events.append("verify_alias")
        return self.alias_target

    async def drop_alias(self, alias: str) -> None:
        self.alias_target = None

    async def drop_collection(self, name: str) -> None:
        self.events.append("drop_collection")
        self.collections.discard(name)

    def _fail_after(self, event: str) -> None:
        if self.fail_after == event:
            self.fail_after = None
            raise TimeoutError("partial side effect")


class DelayedAliasProvisioner(RecordingProvisioner):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.alias_started = threading.Event()
        self.alias_release = threading.Event()
        self.alias_finished = threading.Event()

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        if collection_name.endswith("_old"):
            await super().alter_alias(alias, collection_name)
            return
        self.events.append("alter_alias")

        def mutate_alias() -> None:
            self.alias_started.set()
            self.alias_release.wait()
            self.alias_target = collection_name
            self.alias_finished.set()

        await milvus_client._call(mutate_alias)


class DelayedCollectionInventoryProvisioner(RecordingProvisioner):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.inventory_calls = 0
        self.inventory_started = asyncio.Event()
        self.inventory_release = asyncio.Event()
        self.inventory_finished = False

    async def collection_exists(self, name: str) -> bool:
        self.inventory_calls += 1
        if self.inventory_calls > 1:
            self.inventory_started.set()
            await self.inventory_release.wait()
            self.inventory_finished = True
        return await super().collection_exists(name)


class ExternalAliasBeforeRollbackProvisioner(RecordingProvisioner):
    async def create_indexes(self, name: str) -> None:
        await super().create_indexes(name)
        self.alias_target = name


class ExternalAliasAfterObservationProvisioner(RecordingProvisioner):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.observed_for_rollback = False

    async def describe_alias(self, alias: str) -> str | None:
        value = await super().describe_alias(alias)
        if self.describe_alias_calls == 3:
            self.observed_for_rollback = True
            asyncio.get_running_loop().call_soon(
                setattr,
                self,
                "alias_target",
                "kb_doc_v1_corpus_fixture_v1",
            )
        return value

    async def collection_exists(self, name: str) -> bool:
        if self.observed_for_rollback:
            await asyncio.sleep(0)
        return await super().collection_exists(name)


class RecordingWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.rows: tuple[dict[str, object], ...] = ()

    async def insert(self, name: str, rows: tuple[dict[str, object], ...]) -> None:
        self.events.append("insert")
        self.rows = rows

    async def upsert(self, name: str, rows: tuple[dict[str, object], ...]) -> None:
        self.events.append("upsert")
        self.rows = rows

    async def delete(self, name: str, chunk_ids: tuple[str, ...]) -> None:
        self.events.append("delete")

    async def flush(self, name: str) -> None:
        self.events.append("flush")


class RecordingReader:
    def __init__(
        self,
        events: list[str],
        writer: RecordingWriter,
        *,
        fail_negative_probe: bool = False,
    ) -> None:
        self.events = events
        self.writer = writer
        self.fail_negative_probe = fail_negative_probe
        self.manifest = load_doc_fixture(FIXTURE)
        self.corrupt_field: str | None = None
        self.row_overrides: dict[str, object] = {}
        self.widen_rows = False
        self.revoked_rows: tuple[dict[str, object], ...] = ()
        self.last_revoked_filter: str | None = None

    async def describe_alias(self, alias: str) -> str:
        raise AssertionError("publisher verifies aliases with the provisioner identity")

    async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
        self.events.append("reconcile")
        manifest = self.manifest
        return MilvusCollectionDescriptor(
            collection_name=collection_name,
            family=SourceFamily.DOC,
            schema_version=manifest.schema_version,
            schema_sha256=manifest.schema_sha256,
            corpus_version=manifest.corpus_version,
            embedding_model_version=manifest.embedding_model_version,
            vector_dimension=manifest.vector_dimension,
            dynamic_fields_enabled=False,
            consistency_level="Strong",
        )

    async def hybrid_search(self, request: object) -> tuple[dict[str, object], ...]:
        raise AssertionError("publisher safety probes are strong scalar queries")

    async def query(self, request: MilvusQueryRequest) -> tuple[dict[str, object], ...]:
        if " != " in request.filter_expression:
            self.events.append("negative_probe")
            return ({"chunk_id": "h_" + "f" * 64},) if self.fail_negative_probe else ()
        if "revoked-subject" in request.filter_expression:
            self.events.append("revoked_probe")
            return ()
        self.events.append("positive_probe")
        return tuple({"chunk_id": chunk.chunk_id} for chunk in self.manifest.chunks)

    async def query_persisted_rows(
        self,
        collection_name: str,
        filter_expression: str,
        output_fields: tuple[str, ...],
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        if " != " in filter_expression:
            self.events.append("negative_probe")
            return ({"chunk_id": "h_" + "f" * 64},) if self.fail_negative_probe else ()
        if output_fields == ("chunk_id",):
            self.events.append("revoked_probe")
            self.last_revoked_filter = filter_expression
            return self.revoked_rows
        self.events.append("positive_probe")
        result = [{field: row[field] for field in output_fields} for row in self.writer.rows]
        if self.corrupt_field is not None:
            result[0][self.corrupt_field] = _corrupt_value(
                self.corrupt_field,
                result[0][self.corrupt_field],
            )
        result[0].update(self.row_overrides)
        if self.widen_rows:
            result[0]["unexpected"] = "widened"
        return tuple(result)

    async def close(self) -> None:
        return None


@dataclass
class RecordingActivator:
    events: list[str]
    state: CorpusActivationState | None = None
    fail_after_activate: bool = False

    async def snapshot(self) -> CorpusActivationState | None:
        return self.state

    async def activate(
        self, corpus_version: str, physical_collection: str, manifest_sha256: str
    ) -> str:
        self.events.append("activate_corpus")
        payload = json.dumps(
            {
                "corpusVersion": corpus_version,
                "manifestSha256": manifest_sha256,
                "physicalCollection": physical_collection,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        activation_id = "h_" + hashlib.sha256(payload).hexdigest()
        self.state = CorpusActivationState(
            activation_id,
            corpus_version,
            physical_collection,
            manifest_sha256,
        )
        if self.fail_after_activate:
            raise TimeoutError("activation fsync completed before timeout")
        return activation_id

    async def restore(self, state: CorpusActivationState | None) -> None:
        self.events.append("restore_corpus")
        self.state = state


@dataclass
class BlockingActivator(RecordingActivator):
    entered: asyncio.Event = field(init=False)

    def __post_init__(self) -> None:
        self.entered = asyncio.Event()

    async def activate(
        self, corpus_version: str, physical_collection: str, manifest_sha256: str
    ) -> str:
        activation_id = await super().activate(
            corpus_version,
            physical_collection,
            manifest_sha256,
        )
        self.entered.set()
        await asyncio.Future()
        return activation_id


@dataclass
class DelayedRestoreActivator(BlockingActivator):
    restore_started: asyncio.Event = field(init=False)
    restore_release: asyncio.Event = field(init=False)
    restore_finished: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        self.restore_started = asyncio.Event()
        self.restore_release = asyncio.Event()

    async def restore(self, state: CorpusActivationState | None) -> None:
        self.restore_started.set()
        await self.restore_release.wait()
        await super().restore(state)
        self.restore_finished = True


@dataclass
class RecordingCleanupSink:
    issues: list[tuple[str, ...]]

    def record_cleanup(self, issues: tuple[str, ...]) -> None:
        self.issues.append(issues)


class CancellingCleanupSink:
    def record_cleanup(self, issues: tuple[str, ...]) -> None:
        raise asyncio.CancelledError


def clients(events: list[str], *, fail_negative_probe: bool = False) -> MilvusPublishClients:
    provisioner = RecordingProvisioner(events)
    return clients_with_provisioner(
        events,
        provisioner,
        fail_negative_probe=fail_negative_probe,
    )


def clients_with_provisioner(
    events: list[str],
    provisioner: RecordingProvisioner,
    *,
    fail_negative_probe: bool = False,
) -> MilvusPublishClients:
    writer = RecordingWriter(events)
    return MilvusPublishClients(
        provisioner=cast(object, provisioner),
        writer=cast(object, writer),
        reader=RecordingReader(events, writer, fail_negative_probe=fail_negative_probe),
    )


def _corrupt_value(field: str, value: object) -> object:
    if field == "dense_vector":
        return [1.0, 2.0]
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, list):
        return ["corrupted"]
    if value is None:
        return "corrupted"
    return "corrupted"


def unit_vectors() -> dict[str, tuple[float, ...]]:
    manifest = load_doc_fixture(FIXTURE)
    return {chunk.chunk_id: (0.0,) * manifest.vector_dimension for chunk in manifest.chunks}


async def test_publish_uses_fixed_fail_closed_order() -> None:
    events: list[str] = []
    manifest = load_doc_fixture(FIXTURE)
    receipt = await publish_fixture(
        clients(events), manifest, unit_vectors(), RecordingActivator(events)
    )

    assert events == [
        "create_collection",
        "create_indexes",
        "grant_writer",
        "grant_reader",
        "insert",
        "flush",
        "reconcile",
        "positive_probe",
        "negative_probe",
        "alter_alias",
        "verify_alias",
        "activate_corpus",
        "revoke_writer",
    ]
    assert receipt.row_count == 12
    assert receipt.physical_collection == manifest.physical_collection
    assert receipt.manifest_sha256 == manifest_sha256(manifest)


async def test_negative_probe_failure_never_switches_alias_and_retains_new_target() -> None:
    events: list[str] = []
    manifest = load_doc_fixture(FIXTURE)
    publish_clients = clients(events, fail_negative_probe=True)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    sink = RecordingCleanupSink([])

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
            cleanup_status_sink=sink,
        )

    assert "alter_alias" not in events
    assert "drop_collection" not in events
    assert manifest.physical_collection in provisioner.collections
    assert sink.issues == [("collection",)]


@pytest.mark.parametrize(
    "partial_event",
    ("create_collection", "grant_writer", "grant_reader"),
)
async def test_partial_side_effect_then_timeout_retains_created_target_for_explicit_cleanup(
    partial_event: str,
) -> None:
    events: list[str] = []
    publish_clients = clients(events)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.fail_after = partial_event
    activator = RecordingActivator(events)
    manifest = load_doc_fixture(FIXTURE)
    sink = RecordingCleanupSink([])

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            activator,
            cleanup_status_sink=sink,
        )

    assert provisioner.alias_target is None
    assert manifest.physical_collection in provisioner.collections
    assert not {grant for grant in provisioner.grants if grant[0] == manifest.physical_collection}
    assert activator.state is None
    assert sink.issues == [("collection",)]
    assert "drop_collection" not in events


async def test_external_alias_to_new_target_before_rollback_never_triggers_drop() -> None:
    events: list[str] = []
    provisioner = ExternalAliasBeforeRollbackProvisioner(events)
    publish_clients = clients_with_provisioner(
        events,
        provisioner,
        fail_negative_probe=True,
    )
    manifest = load_doc_fixture(FIXTURE)
    sink = RecordingCleanupSink([])

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
            cleanup_status_sink=sink,
        )

    assert provisioner.alias_target == manifest.physical_collection
    assert manifest.physical_collection in provisioner.collections
    assert sink.issues == [("alias", "collection")]
    assert "alter_alias" not in events
    assert "drop_collection" not in events


async def test_external_alias_switch_after_rollback_observation_never_triggers_drop() -> None:
    events: list[str] = []
    provisioner = ExternalAliasAfterObservationProvisioner(events)
    publish_clients = clients_with_provisioner(
        events,
        provisioner,
        fail_negative_probe=True,
    )
    manifest = load_doc_fixture(FIXTURE)
    sink = RecordingCleanupSink([])

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
            cleanup_status_sink=sink,
        )

    assert provisioner.observed_for_rollback
    assert provisioner.alias_target == manifest.physical_collection
    assert manifest.physical_collection in provisioner.collections
    assert sink.issues == [("collection",)]
    assert "alter_alias" not in events
    assert "drop_collection" not in events


async def test_failed_alias_attempt_restores_alias_but_preserves_new_collection() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    previous = "kb_doc_v1_corpus_fixture_old"
    provisioner.alias_target = previous
    provisioner.collections.add(previous)
    provisioner.fail_after = "alter_alias"
    sink = RecordingCleanupSink([])
    manifest = load_doc_fixture(FIXTURE)

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
            cleanup_status_sink=sink,
        )

    assert provisioner.alias_target == previous
    assert manifest.physical_collection in provisioner.collections
    assert sink.issues == [("collection",)]
    assert "drop_collection" not in events


async def test_marker_and_alias_restore_after_activation_or_writer_revoke_failure() -> None:
    for failure in ("activation", "revoke_writer"):
        events: list[str] = []
        publish_clients = clients(events)
        provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
        provisioner.alias_target = "kb_doc_v1_corpus_fixture_old"
        provisioner.collections.add("kb_doc_v1_corpus_fixture_old")
        previous = CorpusActivationState(
            "h_" + "b" * 64,
            "corpus-fixture-old",
            "kb_doc_v1_corpus_fixture_old",
            "sha256:" + "b" * 64,
        )
        activator = RecordingActivator(events, state=previous)
        if failure == "activation":
            activator.fail_after_activate = True
        else:
            provisioner.fail_after = "revoke_writer"

        with pytest.raises(PublishRejected):
            await publish_fixture(
                publish_clients,
                load_doc_fixture(FIXTURE),
                unit_vectors(),
                activator,
            )

        assert provisioner.alias_target == previous.physical_collection
        assert activator.state == previous
        assert "restore_corpus" in events


async def test_alias_restore_failure_preserves_new_physical_and_reports_incomplete() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.alias_target = "kb_doc_v1_corpus_fixture_old"
    provisioner.collections.add("kb_doc_v1_corpus_fixture_old")
    provisioner.fail_after = "revoke_writer"
    provisioner.fail_alias_restore = True
    manifest = load_doc_fixture(FIXTURE)

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
        )

    assert provisioner.alias_target == manifest.physical_collection
    assert manifest.physical_collection in provisioner.collections
    assert "drop_collection" not in events


async def test_retained_created_target_never_deletes_unrelated_collection() -> None:
    events: list[str] = []
    publish_clients = clients(events, fail_negative_probe=True)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.collections.add("kb_doc_v1_unrelated")

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            load_doc_fixture(FIXTURE),
            unit_vectors(),
            RecordingActivator(events),
        )

    assert "kb_doc_v1_unrelated" in provisioner.collections
    assert "kb_doc_v1_corpus_fixture_v1" in provisioner.collections


async def test_same_manifest_second_publish_converges_without_recreate_or_reinsert() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    activator = RecordingActivator(events)
    first = await publish_fixture(publish_clients, manifest, unit_vectors(), activator)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    alias_calls = provisioner.describe_alias_calls
    events.clear()

    second = await publish_fixture(publish_clients, manifest, unit_vectors(), activator)

    assert second == first
    assert "create_collection" not in events
    assert "create_indexes" not in events
    assert "grant_writer" not in events
    assert "insert" not in events
    assert events == ["reconcile", "positive_probe", "negative_probe"]
    assert provisioner.describe_alias_calls == alias_calls + 2


async def test_new_publish_target_reader_grants_are_only_search_and_query() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)

    await publish_fixture(
        publish_clients,
        manifest,
        unit_vectors(),
        RecordingActivator(events),
    )

    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    reader_grants = await provisioner.collection_grants(
        manifest.physical_collection,
        "tap_reader",
    )
    assert {grant.privilege for grant in reader_grants} == {"Query", "Search"}


async def test_preexisting_exact_target_converges_reader_and_partial_writer_grants() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    activator = RecordingActivator(events)
    await publish_fixture(publish_clients, manifest, unit_vectors(), activator)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.grants[(manifest.physical_collection, "tap_reader")] = {
        "Query",
    }
    provisioner.grants[(manifest.physical_collection, "tap_writer")] = {
        "Insert",
        "Flush",
    }
    events.clear()

    await publish_fixture(publish_clients, manifest, unit_vectors(), activator)

    reader_grants = await provisioner.collection_grants(
        manifest.physical_collection,
        "tap_reader",
    )
    writer_grants = await provisioner.collection_grants(
        manifest.physical_collection,
        "tap_writer",
    )
    assert {grant.privilege for grant in reader_grants} == READER_TARGET_PRIVILEGES
    assert writer_grants == frozenset()
    assert events == [
        "reconcile",
        "positive_probe",
        "negative_probe",
        "grant_reader",
        "revoke_writer",
    ]


@pytest.mark.parametrize(
    ("role_name", "unexpected"),
    (("tap_reader", "Insert"), ("tap_writer", "Search")),
)
async def test_preexisting_target_rejects_unexpected_scoped_privilege(
    role_name: str,
    unexpected: str,
) -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    activator = RecordingActivator(events)
    await publish_fixture(publish_clients, manifest, unit_vectors(), activator)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.grants.setdefault((manifest.physical_collection, role_name), set()).add(unexpected)
    events.clear()

    with pytest.raises(PublishRejected, match="scoped grant"):
        await publish_fixture(publish_clients, manifest, unit_vectors(), activator)

    assert "grant_reader" not in events
    assert "revoke_writer" not in events
    assert manifest.physical_collection in provisioner.collections


async def test_mismatched_preexisting_target_fails_without_dropping_active_data() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.collections.update({manifest.physical_collection, "kb_doc_v1_corpus_fixture_old"})
    provisioner.alias_target = "kb_doc_v1_corpus_fixture_old"

    with pytest.raises(PublishRejected):
        await publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
        )

    assert provisioner.collections == {
        manifest.physical_collection,
        "kb_doc_v1_corpus_fixture_old",
    }
    assert provisioner.alias_target == "kb_doc_v1_corpus_fixture_old"
    assert "drop_collection" not in events
    assert "alter_alias" not in events


async def test_descriptor_mismatch_never_switches_alias() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    reader = cast(RecordingReader, publish_clients.reader)
    wrong = replace(reader.manifest, schema_sha256="sha256:" + "a" * 64)
    reader.manifest = wrong

    with pytest.raises(PublishRejected):
        await publish_fixture(
            publish_clients, load_doc_fixture(FIXTURE), unit_vectors(), RecordingActivator(events)
        )
    assert "alter_alias" not in events


@pytest.mark.parametrize(
    "field",
    (
        "chunk_id",
        "logical_chunk_id",
        "root_id",
        "parent_id",
        "title",
        "content",
        "content_role",
        "tenant_id",
        "project_id",
        "allowed_group_ids",
        "classification_rank",
        "environment",
        "deleted",
        "source_id",
        "source_revision",
        "source_content_hash",
        "chunk_content_hash",
        "anchor_json",
        "index_family",
        "physical_collection",
        "corpus_version",
        "schema_version",
        "embedding_model_version",
        "source_type",
        "revision_kind",
        "derived_from_chunk_ids",
        "dense_vector",
    ),
)
async def test_every_persisted_row_field_mismatch_rejects_before_alias(field: str) -> None:
    events: list[str] = []
    publish_clients = clients(events)
    cast(RecordingReader, publish_clients.reader).corrupt_field = field

    with pytest.raises(PublishRejected):
        await publish_fixture(
            publish_clients,
            load_doc_fixture(FIXTURE),
            unit_vectors(),
            RecordingActivator(events),
        )

    assert "alter_alias" not in events


async def test_widened_persisted_row_rejects_before_alias() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    cast(RecordingReader, publish_clients.reader).widen_rows = True

    with pytest.raises(PublishRejected):
        await publish_fixture(
            publish_clients,
            load_doc_fixture(FIXTURE),
            unit_vectors(),
            RecordingActivator(events),
        )

    assert "alter_alias" not in events


@pytest.mark.parametrize(
    "mutation",
    (
        "deleted-int",
        "classification-bool",
        "classification-float",
        "classification-range",
        "groups-tuple",
        "groups-non-string",
        "vector-int",
        "vector-bool",
        "vector-non-finite",
        "anchor-object",
        "derived-tuple",
        "derived-non-string",
    ),
)
async def test_persisted_rows_reject_type_coercions_and_nested_shape_drift(
    mutation: str,
) -> None:
    events: list[str] = []
    publish_clients = clients(events)
    reader = cast(RecordingReader, publish_clients.reader)
    dimension = load_doc_fixture(FIXTURE).vector_dimension
    reader.row_overrides = {
        "deleted-int": {"deleted": 0},
        "classification-bool": {"classification_rank": True},
        "classification-float": {"classification_rank": 1.0},
        "classification-range": {"classification_rank": 4},
        "groups-tuple": {"allowed_group_ids": ("group-payments",)},
        "groups-non-string": {"allowed_group_ids": [1]},
        "vector-int": {"dense_vector": [0] * dimension},
        "vector-bool": {"dense_vector": [False] * dimension},
        "vector-non-finite": {"dense_vector": [float("nan")] * dimension},
        "anchor-object": {"anchor_json": {"page": 1}},
        "derived-tuple": {"derived_from_chunk_ids": ()},
        "derived-non-string": {"derived_from_chunk_ids": [1]},
    }[mutation]

    with pytest.raises(PublishRejected):
        await publish_fixture(
            publish_clients,
            load_doc_fixture(FIXTURE),
            unit_vectors(),
            RecordingActivator(events),
        )

    assert "alter_alias" not in events


async def test_persisted_float_vector_accepts_tuple_provider_shape() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    reader = cast(RecordingReader, publish_clients.reader)
    dimension = load_doc_fixture(FIXTURE).vector_dimension
    reader.row_overrides = {"dense_vector": (0.0,) * dimension}

    receipt = await publish_fixture(
        publish_clients,
        load_doc_fixture(FIXTURE),
        unit_vectors(),
        RecordingActivator(events),
    )

    assert receipt.row_count == 12


async def test_acl_tightening_upserts_and_proves_revoked_subject_zero_before_receipt() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    original = manifest.chunks[0]
    tightened = replace(original, allowed_group_ids=(), deleted=True)

    receipt = await tighten_fixture_acl(
        publish_clients,
        manifest,
        tightened,
        unit_vectors()[original.chunk_id],
    )

    assert receipt.chunk_id == original.chunk_id
    assert receipt.proof_kind == "deleted"
    assert events == ["upsert", "flush", "revoked_probe"]
    assert "delete" not in events
    proof = cast(RecordingReader, publish_clients.reader).last_revoked_filter
    assert proof is not None
    assert original.chunk_id in proof
    assert "deleted == false" in proof


@pytest.mark.parametrize(
    "change",
    (
        {"allowed_group_ids": ("group-payments", "group-added")},
        {"classification_rank": 0},
        {"environment": "staging"},
    ),
)
async def test_acl_tightening_rejects_any_authorization_widening(
    change: dict[str, object],
) -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    original = manifest.chunks[0]

    with pytest.raises(PublishRejected, match="monotonic"):
        await tighten_fixture_acl(
            publish_clients,
            manifest,
            replace(original, **change),
            unit_vectors()[original.chunk_id],
        )
    assert events == []


async def test_acl_tightening_rejects_deleted_to_live_and_immutable_changes() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    deleted = next(chunk for chunk in manifest.chunks if chunk.deleted)
    live = manifest.chunks[0]

    for changed in (
        replace(deleted, deleted=False),
        replace(live, source_revision="fixture-blob-v2"),
    ):
        with pytest.raises(PublishRejected):
            await tighten_fixture_acl(
                publish_clients,
                manifest,
                changed,
                unit_vectors()[changed.chunk_id],
            )
    assert events == []


async def test_acl_tightening_derives_removed_group_proof_and_rejects_caller_filter() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    original = next(chunk for chunk in manifest.chunks if len(chunk.allowed_group_ids) == 2)
    tightened = replace(original, allowed_group_ids=("group-payments",))

    receipt = await tighten_fixture_acl(
        publish_clients,
        manifest,
        tightened,
        unit_vectors()[original.chunk_id],
    )
    assert receipt.proof_kind == "removed_group"
    proof = cast(RecordingReader, publish_clients.reader).last_revoked_filter
    assert proof is not None
    assert "group-audit" in proof

    with pytest.raises(PublishRejected, match="caller-supplied"):
        await tighten_fixture_acl(
            clients([]),
            manifest,
            tightened,
            unit_vectors()[original.chunk_id],
            revoked_filter='chunk_id == "anything"',
        )


async def test_acl_tightening_proof_covers_every_removed_group_with_safe_escaping() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    fixture_original = manifest.chunks[0]
    original = replace(
        fixture_original,
        allowed_group_ids=("group-retained", 'group-removed"quote', "group-removed\\slash"),
    )
    manifest = replace(
        manifest,
        chunks=(original, *manifest.chunks[1:]),
    )
    tightened = replace(original, allowed_group_ids=("group-retained",))

    receipt = await tighten_fixture_acl(
        publish_clients,
        manifest,
        tightened,
        unit_vectors()[original.chunk_id],
    )

    assert receipt.proof_kind == "removed_group"
    proof = cast(RecordingReader, publish_clients.reader).last_revoked_filter
    assert proof is not None
    assert "ARRAY_CONTAINS_ANY" in proof
    assert (
        json.dumps(
            ['group-removed"quote', "group-removed\\slash"],
            separators=(",", ":"),
        )
        in proof
    )
    assert "group-retained" not in proof


@pytest.mark.parametrize(
    ("side", "classification_rank"),
    (("original", True), ("original", 4), ("tightened", True), ("tightened", 4)),
)
async def test_acl_tightening_rejects_non_integer_or_out_of_range_classification(
    side: str,
    classification_rank: object,
) -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    original = manifest.chunks[0]
    tightened = replace(original, deleted=True)
    if side == "original":
        original = replace(original, classification_rank=classification_rank)
        manifest = replace(manifest, chunks=(original, *manifest.chunks[1:]))
        tightened = replace(original, deleted=True)
    else:
        tightened = replace(tightened, classification_rank=classification_rank)

    with pytest.raises(PublishRejected, match="classification"):
        await tighten_fixture_acl(
            publish_clients,
            manifest,
            tightened,
            unit_vectors()[original.chunk_id],
        )
    assert events == []


async def test_acl_tightening_rejects_integer_vector_before_write() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    manifest = load_doc_fixture(FIXTURE)
    original = manifest.chunks[0]

    with pytest.raises(PublishRejected, match="metadata is malformed"):
        await tighten_fixture_acl(
            publish_clients,
            manifest,
            replace(original, deleted=True),
            cast(tuple[float, ...], (0,) * manifest.vector_dimension),
        )

    assert events == []


async def test_deadline_then_settle_waits_for_worker_before_timeout_is_observable() -> None:
    started = threading.Event()
    release = threading.Event()
    effects: list[str] = []

    def delayed_effect() -> None:
        started.set()
        release.wait()
        effects.append("finished")

    task = asyncio.create_task(
        milvus_async_call.deadline_then_settle_blocking_call(
            delayed_effect,
            timeout_seconds=0.01,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1.0)
        await asyncio.sleep(0.03)
        returned_before_worker = task.done()
    finally:
        release.set()

    with pytest.raises(TimeoutError):
        await task
    effects_at_return = list(effects)
    await asyncio.sleep(0.01)

    assert not returned_before_worker
    assert effects_at_return == ["finished"]
    assert effects == effects_at_return


async def test_task_cancellation_stays_cancelled_when_rollback_is_incomplete() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.alias_target = "kb_doc_v1_corpus_fixture_old"
    provisioner.collections.add("kb_doc_v1_corpus_fixture_old")
    provisioner.fail_alias_restore = True
    activator = BlockingActivator(events)
    sink = RecordingCleanupSink([])
    manifest = load_doc_fixture(FIXTURE)
    task = asyncio.create_task(
        publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            activator,
            cleanup_status_sink=sink,
        )
    )
    await activator.entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert sink.issues == [("alias", "collection")]
    assert provisioner.alias_target == manifest.physical_collection
    assert manifest.physical_collection in provisioner.collections
    assert "drop_collection" not in events


async def test_cancelled_delayed_alias_settles_then_preserves_new_target() -> None:
    events: list[str] = []
    provisioner = DelayedAliasProvisioner(events)
    publish_clients = clients_with_provisioner(events, provisioner)
    provisioner.alias_target = "kb_doc_v1_corpus_fixture_old"
    provisioner.collections.add("kb_doc_v1_corpus_fixture_old")
    manifest = load_doc_fixture(FIXTURE)
    sink = RecordingCleanupSink([])
    task = asyncio.create_task(
        publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
            cleanup_status_sink=sink,
        )
    )
    assert await asyncio.to_thread(provisioner.alias_started.wait, 1.0)

    task.cancel()
    await asyncio.sleep(0.01)
    returned_before_provider = task.done()
    provisioner.alias_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(provisioner.alias_finished.wait, 1.0)

    assert not returned_before_provider
    assert task.cancelled()
    assert provisioner.alias_target == "kb_doc_v1_corpus_fixture_old"
    assert manifest.physical_collection in provisioner.collections
    assert sink.issues == [("collection",)]
    assert "drop_collection" not in events


async def test_repeated_cancellation_waits_for_rollback_terminal_result() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    provisioner = cast(RecordingProvisioner, publish_clients.provisioner)
    provisioner.alias_target = "kb_doc_v1_corpus_fixture_old"
    provisioner.collections.add("kb_doc_v1_corpus_fixture_old")
    activator = DelayedRestoreActivator(events)
    sink = RecordingCleanupSink([])
    task = asyncio.create_task(
        publish_fixture(
            publish_clients,
            load_doc_fixture(FIXTURE),
            unit_vectors(),
            activator,
            cleanup_status_sink=sink,
        )
    )
    await activator.entered.wait()
    task.cancel()
    await activator.restore_started.wait()

    task.cancel()
    task.cancel()
    await asyncio.sleep(0)
    returned_before_rollback = task.done()
    activator.restore_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not returned_before_rollback
    assert task.cancelled()
    assert task.cancelling() == 3
    assert activator.restore_finished
    assert sink.issues == [("collection",)]


async def test_ordinary_failure_then_repeated_cancellation_finishes_rollback() -> None:
    events: list[str] = []
    provisioner = DelayedCollectionInventoryProvisioner(events)
    publish_clients = clients_with_provisioner(
        events,
        provisioner,
        fail_negative_probe=True,
    )
    sink = RecordingCleanupSink([])
    manifest = load_doc_fixture(FIXTURE)
    task = asyncio.create_task(
        publish_fixture(
            publish_clients,
            manifest,
            unit_vectors(),
            RecordingActivator(events),
            cleanup_status_sink=sink,
        )
    )
    await provisioner.inventory_started.wait()

    task.cancel()
    task.cancel()
    await asyncio.sleep(0)
    returned_before_rollback = task.done()
    provisioner.inventory_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not returned_before_rollback
    assert task.cancelled()
    assert task.cancelling() == 2
    assert provisioner.inventory_finished
    assert manifest.physical_collection in provisioner.collections
    assert sink.issues == [("collection",)]
    assert "drop_collection" not in events


async def test_cleanup_sink_cancellation_never_replaces_ordinary_failure() -> None:
    events: list[str] = []
    publish_clients = clients(events, fail_negative_probe=True)

    with pytest.raises(PublishRejected, match="cleanup incomplete"):
        await publish_fixture(
            publish_clients,
            load_doc_fixture(FIXTURE),
            unit_vectors(),
            RecordingActivator(events),
            cleanup_status_sink=CancellingCleanupSink(),
        )


async def test_local_activation_uses_replace_and_writes_closed_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".local" / "milvus-active-corpus.json"
    replacements: list[tuple[Path, Path]] = []
    from tap.operations.milvus import activation as activation_module

    real_replace = activation_module.os.replace

    def record_replace(source: str | Path, target: str | Path) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(activation_module.os, "replace", record_replace)
    activation_id = await LocalCorpusActivator(path).activate(
        "corpus-fixture-v1",
        "kb_doc_v1_corpus_fixture_v1",
        "sha256:" + "a" * 64,
    )

    assert len(replacements) == 1
    assert replacements[0][1] == path
    marker = json.loads(path.read_text())
    assert set(marker) == {"activationId", "corpusVersion", "manifestSha256", "physicalCollection"}
    assert marker["activationId"] == activation_id


async def test_local_activation_snapshot_restores_previous_marker_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".local" / "milvus-active-corpus.json"
    activator = LocalCorpusActivator(path)
    previous_id = await activator.activate(
        "corpus-fixture-old",
        "kb_doc_v1_corpus_fixture_old",
        "sha256:" + "b" * 64,
    )
    previous = await activator.snapshot()
    await activator.activate(
        "corpus-fixture-v1",
        "kb_doc_v1_corpus_fixture_v1",
        "sha256:" + "a" * 64,
    )

    replacements: list[tuple[Path, Path]] = []
    from tap.operations.milvus import activation as activation_module

    real_replace = activation_module.os.replace

    def record_replace(source: str | Path, target: str | Path) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(activation_module.os, "replace", record_replace)
    await activator.restore(previous)

    assert len(replacements) == 1
    assert json.loads(path.read_text())["activationId"] == previous_id


async def test_finalize_requires_exact_old_physical_and_revokes_reader_before_drop() -> None:
    events: list[str] = []
    publish_clients = clients(events)
    await finalize_old_physical(publish_clients, "kb_doc_v1_corpus_fixture_old")
    assert events == ["revoke_reader", "drop_collection"]

    with pytest.raises(PublishRejected):
        await finalize_old_physical(publish_clients, "*")


async def test_fixture_cli_sanitizes_provider_failure_output() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import scripts.milvus_fixture as cli
async def fail(args):
    raise RuntimeError('PRIVATE_FILTER PRIVATE_GROUP PRIVATE_VECTOR PRIVATE_SECRET')
cli._run = fail
raise SystemExit(cli.main(['publish']))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "Milvus fixture operation failed.\n"


async def test_fixture_cli_rejects_duplicate_or_root_principals_before_connections() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import argparse
import asyncio
import os
from pathlib import Path
import scripts.milvus_fixture as cli

def forbidden(*args, **kwargs):
    raise AssertionError('connection attempted before principal validation')

cli._connect = forbidden
args = argparse.Namespace(
    command='publish',
    fixture=Path('apps/backend/tests/fixtures/milvus/doc-fixture-v1.json'),
    active_marker=Path('.local/milvus-active-corpus.json'),
)
for values in (
    ('Tap_Reader', 'tap_reader', 'tap_provisioner'),
    ('root', 'tap_writer', 'tap_provisioner'),
):
    os.environ['MILVUS_READER_USERNAME'] = values[0]
    os.environ['MILVUS_WRITER_USERNAME'] = values[1]
    os.environ['MILVUS_PROVISIONER_USERNAME'] = values[2]
    try:
        asyncio.run(cli._run(args))
    except ValueError:
        continue
    raise AssertionError('invalid principals were accepted')
print('validated-before-connect')
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "validated-before-connect\n"
    assert completed.stderr == ""


async def test_fixture_provisioner_reports_exact_scoped_privilege_inventory() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import asyncio
import scripts.milvus_fixture as cli

class GrantClient:
    requested = None
    def describe_role(self, role_name, **kwargs):
        self.requested = (role_name, kwargs.get('db_name'))
        return {
            'role': 'tap_reader',
            'privileges': [
                {
                    'object_type': 'Global', 'object_name': '*', 'db_name': 'default',
                    'role_name': 'tap_reader', 'privilege': 'DescribeAlias',
                },
                {
                    'object_type': 'Global', 'object_name': '*', 'db_name': 'default',
                    'role_name': 'tap_reader', 'privilege': 'DescribeCollection',
                },
                {
                    'object_type': 'Collection',
                    'object_name': 'kb_doc_v1_corpus_fixture_v1',
                    'db_name': 'default', 'role_name': 'tap_reader', 'privilege': 'Search',
                },
                {
                    'object_type': 'Collection',
                    'object_name': 'kb_doc_v1_corpus_fixture_v1',
                    'db_name': 'default', 'role_name': 'tap_reader', 'privilege': 'Query',
                },
            ]
        }

client = GrantClient()
provisioner = cli._FixtureProvisioner(client, 'default')
grants = asyncio.run(provisioner.collection_grants(
    'kb_doc_v1_corpus_fixture_v1',
    'tap_reader',
))
print(client.requested)
for grant in sorted(grants, key=lambda item: item.privilege):
    print((grant.role_name, grant.object_type, grant.resource_level,
           grant.db_name, grant.database_name, grant.object_name,
           grant.resource_name, grant.privilege))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == (
        "('tap_reader', 'default')\n"
        "('tap_reader', 'Collection', 'collection', 'default', 'default', "
        "'kb_doc_v1_corpus_fixture_v1', 'kb_doc_v1_corpus_fixture_v1', "
        "'Query')\n"
        "('tap_reader', 'Collection', 'collection', 'default', 'default', "
        "'kb_doc_v1_corpus_fixture_v1', 'kb_doc_v1_corpus_fixture_v1', 'Search')\n"
    )


async def test_fixture_provisioner_rejects_conflicting_same_name_grant_dimensions() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import asyncio
import scripts.milvus_fixture as cli

target = 'kb_doc_v1_corpus_fixture_v1'
base = {
    'object_type': 'Collection', 'object_name': target, 'db_name': 'default',
    'role_name': 'tap_reader', 'privilege': 'Search',
}
valid_base = [
    {
        'object_type': 'Global', 'object_name': '*', 'db_name': 'default',
        'role_name': 'tap_reader', 'privilege': privilege,
    }
    for privilege in ('DescribeAlias', 'DescribeCollection')
]
conflicts = (
    {**base, 'db_name': 'other'},
    {**base, 'object_type': 'Global'},
    {**base, 'role_name': 'tap_writer'},
)

class GrantClient:
    def __init__(self, item, top_role='tap_reader'):
        self.item = item
        self.top_role = top_role
    def describe_role(self, role_name, **kwargs):
        return {'role': self.top_role, 'privileges': valid_base + [self.item]}

cases = [(item, 'tap_reader') for item in conflicts] + [(base, 'tap_writer')]
for item, top_role in cases:
    try:
        provisioner = cli._FixtureProvisioner(GrantClient(item, top_role), 'default')
        asyncio.run(provisioner.collection_grants(target, 'tap_reader'))
    except RuntimeError:
        print('rejected')
        continue
    raise AssertionError('ambiguous grant dimension accepted')
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "rejected\n" * 4


async def test_fixture_provisioner_requires_exact_global_reader_base_inventory() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import asyncio
import scripts.milvus_fixture as cli

target = 'kb_doc_v1_corpus_fixture_v1'
target_grants = [
    {
        'object_type': 'Collection', 'object_name': target, 'db_name': 'default',
        'role_name': 'tap_reader', 'privilege': privilege,
    }
    for privilege in ('Query', 'Search')
]
valid_base = [
    {
        'object_type': 'Global', 'object_name': '*', 'db_name': 'default',
        'role_name': 'tap_reader', 'privilege': privilege,
    }
    for privilege in ('DescribeAlias', 'DescribeCollection')
]
cases = (
    valid_base[:1],
    valid_base + [{**valid_base[0], 'privilege': 'Query'}],
    valid_base + [dict(valid_base[0])],
    [{**item, 'object_type': 'Collection'} for item in valid_base],
    [{**item, 'db_name': 'other'} for item in valid_base],
)

class GrantClient:
    def __init__(self, base):
        self.base = base
    def describe_role(self, role_name, **kwargs):
        return {'role': role_name, 'privileges': self.base + target_grants}

for base in cases:
    try:
        provisioner = cli._FixtureProvisioner(GrantClient(base), 'default')
        asyncio.run(provisioner.collection_grants(target, 'tap_reader'))
    except RuntimeError:
        print('rejected')
        continue
    raise AssertionError('invalid reader base inventory accepted')
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "rejected\n" * 5


async def test_fixture_provisioner_mutates_grants_in_its_exact_database_scope() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import asyncio
import scripts.milvus_fixture as cli

class GrantClient:
    def __init__(self):
        self.calls = []
    def grant_privilege_v2(
        self, role_name, privilege, collection_name, db_name=None, timeout=None,
    ):
        self.calls.append(('grant', role_name, privilege, collection_name, db_name))
    def revoke_privilege_v2(
        self, role_name, privilege, collection_name, db_name=None, timeout=None,
    ):
        self.calls.append(('revoke', role_name, privilege, collection_name, db_name))

client = GrantClient()
provisioner = cli._FixtureProvisioner(client, 'fixture_database')
asyncio.run(provisioner.grant_collection('kb_doc_v1_corpus_fixture_v1', 'tap_reader'))
asyncio.run(provisioner.revoke_collection('kb_doc_v1_corpus_fixture_v1', 'tap_reader'))
print(len(client.calls))
print(sorted({(operation, role, collection, database)
              for operation, role, _privilege, collection, database in client.calls}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == (
        "4\n"
        "[('grant', 'tap_reader', 'kb_doc_v1_corpus_fixture_v1', "
        "'fixture_database'), ('revoke', 'tap_reader', "
        "'kb_doc_v1_corpus_fixture_v1', 'fixture_database')]\n"
    )
