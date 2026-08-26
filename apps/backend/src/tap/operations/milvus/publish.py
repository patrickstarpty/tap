"""Fail-closed publication orchestration for the sanitized Milvus fixture."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Protocol, cast

from tap.modules.knowledge.adapters.milvus.transport import MilvusQueryRequest
from tap.operations.milvus.contracts import MilvusPublishClients
from tap.operations.milvus.fixtures import (
    DocFixtureChunk,
    DocFixtureManifest,
    build_collection_schema,
    fixture_rows,
    manifest_sha256,
    validate_collection_descriptor,
)

_SAFE_PHYSICAL = re.compile(r"kb_doc_v1_[A-Za-z0-9_]{1,245}\Z")


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    physical_collection: str
    alias: str
    row_count: int
    manifest_sha256: str
    corpus_version: str
    activation_id: str


@dataclass(frozen=True, slots=True)
class AclTighteningReceipt:
    physical_collection: str
    chunk_id: str
    revoked_subject_hits: int


class PublishRejected(Exception):
    """Fixture validation, reconciliation, or safety probes rejected publication."""


class CorpusActivator(Protocol):
    async def activate(
        self,
        corpus_version: str,
        physical_collection: str,
        manifest_sha256: str,
    ) -> str: ...


async def publish_fixture(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
    vectors_by_chunk_id: Mapping[str, tuple[float, ...]],
    activator: CorpusActivator,
) -> PublishReceipt:
    """Publish only after reconciliation and positive/negative probes pass."""

    try:
        schema = build_collection_schema(manifest)
        rows = fixture_rows(manifest, vectors_by_chunk_id)
        digest = manifest_sha256(manifest)
    except (TypeError, ValueError) as error:
        raise PublishRejected("Milvus fixture validation rejected publication") from error

    physical = manifest.physical_collection
    previous_target = await _current_alias_target(clients, manifest.alias)
    created = False
    writer_granted = False
    reader_granted = False
    alias_switched = False
    try:
        await clients.provisioner.create_collection(physical, schema)
        created = True
        await clients.provisioner.create_indexes(physical)
        await clients.provisioner.grant_collection(physical, "tap_writer")
        writer_granted = True
        await clients.provisioner.grant_collection(physical, "tap_reader")
        reader_granted = True
        await clients.writer.insert(physical, rows)
        await clients.writer.flush(physical)

        descriptor = await clients.reader.describe_collection(physical)
        validate_collection_descriptor(manifest, descriptor)
        await _positive_probe(clients, manifest)
        await _negative_probe(clients, manifest)

        await clients.provisioner.alter_alias(manifest.alias, physical)
        alias_switched = True
        resolved = await clients.provisioner.describe_alias(manifest.alias)
        if resolved != physical:
            raise PublishRejected("Milvus alias verification rejected publication")
        activation_id = await activator.activate(manifest.corpus_version, physical, digest)
        await clients.provisioner.revoke_collection(physical, "tap_writer")
        writer_granted = False
    except asyncio.CancelledError:
        await _rollback(
            clients,
            manifest,
            previous_target=previous_target,
            created=created,
            writer_granted=writer_granted,
            reader_granted=reader_granted,
            alias_switched=alias_switched,
        )
        raise
    except Exception as error:
        await _rollback(
            clients,
            manifest,
            previous_target=previous_target,
            created=created,
            writer_granted=writer_granted,
            reader_granted=reader_granted,
            alias_switched=alias_switched,
        )
        if isinstance(error, PublishRejected):
            raise error from None
        raise PublishRejected("Milvus fixture publication failed closed") from None

    return PublishReceipt(
        physical_collection=physical,
        alias=manifest.alias,
        row_count=len(rows),
        manifest_sha256=digest,
        corpus_version=manifest.corpus_version,
        activation_id=activation_id,
    )


async def tighten_fixture_acl(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
    tightened: DocFixtureChunk,
    vector: tuple[float, ...],
    *,
    revoked_filter: str,
) -> AclTighteningReceipt:
    """Make restrictive metadata authoritative before optional physical deletion."""

    original = next(
        (chunk for chunk in manifest.chunks if chunk.chunk_id == tightened.chunk_id),
        None,
    )
    if original is None or not _same_immutable_chunk(original, tightened):
        raise PublishRejected("ACL tightening changed immutable fixture provenance")
    if (
        not isinstance(tightened.allowed_group_ids, tuple)
        or len(tightened.allowed_group_ids) > 128
        or len(set(tightened.allowed_group_ids)) != len(tightened.allowed_group_ids)
        or any(not isinstance(group, str) or not group for group in tightened.allowed_group_ids)
        or type(tightened.deleted) is not bool
        or not isinstance(vector, tuple)
        or len(vector) != manifest.vector_dimension
    ):
        raise PublishRejected("ACL tightening metadata is malformed")
    row = {
        **asdict(tightened),
        "allowed_group_ids": list(tightened.allowed_group_ids),
        "index_family": "doc",
        "physical_collection": manifest.physical_collection,
        "corpus_version": manifest.corpus_version,
        "schema_version": manifest.schema_version,
        "embedding_model_version": manifest.embedding_model_version,
        "source_type": "doc",
        "revision_kind": "blob_version",
        "derived_from_chunk_ids": [],
        "dense_vector": list(vector),
    }
    try:
        request = MilvusQueryRequest(
            collection_name=manifest.physical_collection,
            filter_expression=revoked_filter,
            output_fields=("chunk_id",),
            limit=1,
        )
        await clients.writer.upsert(manifest.physical_collection, (row,))
        await clients.writer.flush(manifest.physical_collection)
        revoked_rows = await clients.reader.query(request)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise PublishRejected("Milvus ACL tightening verification failed closed") from None
    if revoked_rows:
        raise PublishRejected("revoked subject retained Milvus access")
    return AclTighteningReceipt(
        physical_collection=manifest.physical_collection,
        chunk_id=tightened.chunk_id,
        revoked_subject_hits=0,
    )


async def finalize_old_physical(
    clients: MilvusPublishClients,
    old_physical: str,
    *,
    alias: str = "kb_doc_active",
) -> None:
    """Remove one explicit rollback target; ordinary publish/down never call this."""

    if not isinstance(old_physical, str) or _SAFE_PHYSICAL.fullmatch(old_physical) is None:
        raise PublishRejected("old physical collection name is not exact")
    current = await _current_alias_target(clients, alias)
    if current == old_physical:
        raise PublishRejected("active physical collection cannot be finalized")
    try:
        await clients.provisioner.revoke_collection(old_physical, "tap_reader")
        await clients.provisioner.drop_collection(old_physical)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise PublishRejected("old physical collection finalization failed") from None


async def _positive_probe(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
) -> None:
    expected = {chunk.chunk_id for chunk in manifest.chunks}
    corpus = json.dumps(manifest.corpus_version)
    physical = json.dumps(manifest.physical_collection)
    rows = await clients.reader.query(
        MilvusQueryRequest(
            collection_name=manifest.physical_collection,
            filter_expression=(f"corpus_version == {corpus} and physical_collection == {physical}"),
            output_fields=("chunk_id",),
            limit=len(expected) + 1,
        )
    )
    if (
        len(rows) != len(expected)
        or any(not isinstance(row, Mapping) or set(row) != {"chunk_id"} for row in rows)
        or {cast(str, row["chunk_id"]) for row in rows} != expected
    ):
        raise PublishRejected("Milvus positive reconciliation probe failed")


async def _negative_probe(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
) -> None:
    corpus = json.dumps(manifest.corpus_version)
    physical = json.dumps(manifest.physical_collection)
    rows = await clients.reader.query(
        MilvusQueryRequest(
            collection_name=manifest.physical_collection,
            filter_expression=(f"corpus_version != {corpus} or physical_collection != {physical}"),
            output_fields=("chunk_id",),
            limit=1,
        )
    )
    if rows:
        raise PublishRejected("Milvus negative reconciliation probe failed")


async def _current_alias_target(clients: MilvusPublishClients, alias: str) -> str | None:
    value = await clients.provisioner.describe_alias(alias)
    if value is None:
        return None
    if not isinstance(value, str) or _SAFE_PHYSICAL.fullmatch(value) is None:
        raise PublishRejected("Milvus alias target is malformed")
    return value


async def _rollback(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
    *,
    previous_target: str | None,
    created: bool,
    writer_granted: bool,
    reader_granted: bool,
    alias_switched: bool,
) -> None:
    if alias_switched:
        try:
            if previous_target is None:
                await clients.provisioner.drop_alias(manifest.alias)
            else:
                await clients.provisioner.alter_alias(manifest.alias, previous_target)
        except Exception:
            pass
    if writer_granted:
        try:
            await clients.provisioner.revoke_collection(
                manifest.physical_collection,
                "tap_writer",
            )
        except Exception:
            pass
    if reader_granted:
        try:
            await clients.provisioner.revoke_collection(
                manifest.physical_collection,
                "tap_reader",
            )
        except Exception:
            pass
    if created:
        try:
            await clients.provisioner.drop_collection(manifest.physical_collection)
        except Exception:
            pass


def _same_immutable_chunk(original: DocFixtureChunk, tightened: DocFixtureChunk) -> bool:
    mutable = {"allowed_group_ids", "classification_rank", "environment", "deleted"}
    original_values = asdict(original)
    tightened_values = asdict(tightened)
    return all(
        original_values[name] == tightened_values[name]
        for name in original_values
        if name not in mutable
    )
