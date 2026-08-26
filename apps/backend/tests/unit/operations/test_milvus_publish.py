from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusQueryRequest,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.operations.milvus.activation import LocalCorpusActivator
from tap.operations.milvus.contracts import MilvusPublishClients
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

    async def create_collection(self, name: str, schema: dict[str, object]) -> None:
        self.events.append("create_collection")

    async def create_indexes(self, name: str) -> None:
        self.events.append("create_indexes")

    async def grant_collection(self, name: str, role_name: str) -> None:
        self.events.append("grant_writer" if role_name == "tap_writer" else "grant_reader")

    async def revoke_collection(self, name: str, role_name: str) -> None:
        self.events.append("revoke_writer" if role_name == "tap_writer" else "revoke_reader")

    async def create_alias(self, alias: str, collection_name: str) -> None:
        self.alias_target = collection_name

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        self.events.append("alter_alias")
        self.alias_target = collection_name

    async def describe_alias(self, alias: str) -> str | None:
        if self.alias_target is not None and self.events and self.events[-1] == "alter_alias":
            self.events.append("verify_alias")
        return self.alias_target

    async def drop_alias(self, alias: str) -> None:
        self.alias_target = None

    async def drop_collection(self, name: str) -> None:
        self.events.append("drop_collection")


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
    def __init__(self, events: list[str], *, fail_negative_probe: bool = False) -> None:
        self.events = events
        self.fail_negative_probe = fail_negative_probe
        self.manifest = load_doc_fixture(FIXTURE)

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

    async def close(self) -> None:
        return None


@dataclass
class RecordingActivator:
    events: list[str]

    async def activate(
        self, corpus_version: str, physical_collection: str, manifest_sha256: str
    ) -> str:
        self.events.append("activate_corpus")
        return "activation-fixture-v1"


def clients(events: list[str], *, fail_negative_probe: bool = False) -> MilvusPublishClients:
    return MilvusPublishClients(
        provisioner=cast(object, RecordingProvisioner(events)),
        writer=cast(object, RecordingWriter(events)),
        reader=RecordingReader(events, fail_negative_probe=fail_negative_probe),
    )


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


async def test_negative_probe_failure_never_switches_alias_and_cleans_new_target() -> None:
    events: list[str] = []
    manifest = load_doc_fixture(FIXTURE)
    with pytest.raises(PublishRejected):
        await publish_fixture(
            clients(events, fail_negative_probe=True),
            manifest,
            unit_vectors(),
            RecordingActivator(events),
        )

    assert "alter_alias" not in events
    assert events[-3:] == ["revoke_writer", "revoke_reader", "drop_collection"]


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
        revoked_filter='chunk_id == "revoked-subject"',
    )

    assert receipt.chunk_id == original.chunk_id
    assert events == ["upsert", "flush", "revoked_probe"]
    assert "delete" not in events


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
