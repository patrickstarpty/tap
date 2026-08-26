"""Fail-closed publication orchestration for the sanitized Milvus fixture."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Coroutine, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast

from tap.operations.milvus.activation import CorpusActivationState
from tap.operations.milvus.async_call import await_task_terminal
from tap.operations.milvus.contracts import (
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusPublishClients,
    MilvusScopedGrant,
)
from tap.operations.milvus.fixtures import (
    DocFixtureChunk,
    DocFixtureManifest,
    build_collection_schema,
    fixture_rows,
    manifest_sha256,
    validate_collection_descriptor,
)

_SAFE_PHYSICAL = re.compile(r"kb_doc_v1_[A-Za-z0-9_]{1,245}\Z")
_PERSISTED_FIELDS = (
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
)
_PERSISTED_REQUIRED_TEXT_FIELDS = frozenset(
    {
        "chunk_id",
        "logical_chunk_id",
        "root_id",
        "content",
        "content_role",
        "tenant_id",
        "project_id",
        "environment",
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
    }
)


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
    proof_kind: str
    revoked_subject_hits: int


class PublishRejected(Exception):
    """Fixture validation, reconciliation, or safety probes rejected publication."""


class CorpusActivator(Protocol):
    async def snapshot(self) -> CorpusActivationState | None: ...

    async def activate(
        self,
        corpus_version: str,
        physical_collection: str,
        manifest_sha256: str,
    ) -> str: ...

    async def restore(self, state: CorpusActivationState | None) -> None: ...


class CleanupStatusSink(Protocol):
    def record_cleanup(self, issues: tuple[str, ...]) -> None: ...


async def publish_fixture(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
    vectors_by_chunk_id: Mapping[str, tuple[float, ...]],
    activator: CorpusActivator,
    *,
    cleanup_status_sink: CleanupStatusSink | None = None,
) -> PublishReceipt:
    """Publish only after reconciliation and positive/negative probes pass."""

    try:
        schema = build_collection_schema(manifest)
        rows = fixture_rows(manifest, vectors_by_chunk_id)
        digest = manifest_sha256(manifest)
    except (TypeError, ValueError) as error:
        raise PublishRejected("Milvus fixture validation rejected publication") from error

    physical = manifest.physical_collection
    try:
        previous_target = await _current_alias_target(clients, manifest.alias)
        previous_marker = await activator.snapshot()
        preexisting = await _collection_exists(clients, physical)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise PublishRejected("Milvus publication preflight failed closed") from None

    create_attempted = False
    writer_grant_attempted = False
    reader_grant_attempted = False
    alias_attempted = False
    activation_attempted = False
    try:
        if not preexisting:
            create_attempted = True
            await clients.provisioner.create_collection(physical, schema)
            await clients.provisioner.create_indexes(physical)
            writer_grant_attempted = True
            await clients.provisioner.grant_collection(physical, "tap_writer")
            reader_grant_attempted = True
            await clients.provisioner.grant_collection(physical, "tap_reader")
            await _require_exact_collection_grants(
                clients,
                physical,
                "tap_writer",
                WRITER_PRIVILEGES,
            )
            await _require_exact_collection_grants(
                clients,
                physical,
                "tap_reader",
                READER_TARGET_PRIVILEGES,
            )
            await clients.writer.insert(physical, rows)
            await clients.writer.flush(physical)

        descriptor = await clients.reader.describe_collection(physical)
        validate_collection_descriptor(manifest, descriptor)
        await _positive_probe(clients, manifest, rows)
        await _negative_probe(clients, manifest)

        if preexisting:
            reader_grants = await _collection_grants(clients, physical, "tap_reader")
            writer_grants = await _collection_grants(clients, physical, "tap_writer")
            if (
                not reader_grants <= READER_TARGET_PRIVILEGES
                or not writer_grants <= WRITER_PRIVILEGES
            ):
                raise PublishRejected("Milvus scoped grant inventory is unexpected")
            if reader_grants != READER_TARGET_PRIVILEGES:
                reader_grant_attempted = True
                await clients.provisioner.grant_collection(physical, "tap_reader")
                await _require_exact_collection_grants(
                    clients,
                    physical,
                    "tap_reader",
                    READER_TARGET_PRIVILEGES,
                )
            if writer_grants:
                await clients.provisioner.revoke_collection(physical, "tap_writer")
                await _require_exact_collection_grants(
                    clients,
                    physical,
                    "tap_writer",
                    frozenset(),
                )

        resolved = await clients.provisioner.describe_alias(manifest.alias)
        if resolved != physical:
            alias_attempted = True
            await clients.provisioner.alter_alias(manifest.alias, physical)
            resolved = await clients.provisioner.describe_alias(manifest.alias)
        if resolved != physical:
            raise PublishRejected("Milvus alias verification rejected publication")
        if _activation_matches(previous_marker, manifest, digest) and previous_target == physical:
            assert previous_marker is not None
            activation_id = previous_marker.activation_id
        else:
            activation_attempted = True
            activation_id = await activator.activate(manifest.corpus_version, physical, digest)
        writer_grants = await _collection_grants(clients, physical, "tap_writer")
        if not writer_grants <= WRITER_PRIVILEGES:
            raise PublishRejected("Milvus scoped grant inventory is unexpected")
        if writer_grants:
            await clients.provisioner.revoke_collection(physical, "tap_writer")
        await _require_exact_collection_grants(
            clients,
            physical,
            "tap_writer",
            frozenset(),
        )
        await _require_exact_collection_grants(
            clients,
            physical,
            "tap_reader",
            READER_TARGET_PRIVILEGES,
        )
    except asyncio.CancelledError as cancellation:
        await _settle_rollback(
            _rollback(
                clients,
                manifest,
                activator,
                previous_target=previous_target,
                previous_marker=previous_marker,
                preexisting=preexisting,
                create_attempted=create_attempted,
                writer_grant_attempted=writer_grant_attempted,
                reader_grant_attempted=reader_grant_attempted,
                alias_attempted=alias_attempted,
                activation_attempted=activation_attempted,
            ),
            cleanup_status_sink,
            initial_cancellations=(cancellation,),
        )
        raise cancellation
    except Exception as error:
        cleanup_issues = await _settle_rollback(
            _rollback(
                clients,
                manifest,
                activator,
                previous_target=previous_target,
                previous_marker=previous_marker,
                preexisting=preexisting,
                create_attempted=create_attempted,
                writer_grant_attempted=writer_grant_attempted,
                reader_grant_attempted=reader_grant_attempted,
                alias_attempted=alias_attempted,
                activation_attempted=activation_attempted,
            ),
            cleanup_status_sink,
        )
        if cleanup_issues:
            raise PublishRejected("Milvus publication cleanup incomplete") from None
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


def _record_cleanup_status(
    sink: CleanupStatusSink | None,
    issues: tuple[str, ...],
) -> None:
    if sink is None:
        return
    try:
        sink.record_cleanup(issues)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _settle_rollback(
    rollback: Coroutine[Any, Any, tuple[str, ...]],
    sink: CleanupStatusSink | None,
    *,
    initial_cancellations: tuple[asyncio.CancelledError, ...] = (),
) -> tuple[str, ...]:
    cleanup_task = asyncio.create_task(rollback)
    outcome = await await_task_terminal(
        cleanup_task,
        initial_cancellations=initial_cancellations,
    )
    cleanup_issues = (
        outcome.value
        if outcome.error is None and outcome.value is not None
        else ("cleanup_failed",)
    )
    _record_cleanup_status(sink, cleanup_issues)
    if outcome.cancellations:
        cancellation = outcome.cancellations[0]
        if cleanup_issues:
            cancellation.add_note("Milvus publication cleanup incomplete")
        raise cancellation
    return cleanup_issues


async def tighten_fixture_acl(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
    tightened: DocFixtureChunk,
    vector: tuple[float, ...],
    *,
    revoked_filter: str | None = None,
) -> AclTighteningReceipt:
    """Make restrictive metadata authoritative before optional physical deletion."""

    if revoked_filter is not None:
        raise PublishRejected("caller-supplied ACL proof filters are forbidden")
    original = next(
        (chunk for chunk in manifest.chunks if chunk.chunk_id == tightened.chunk_id),
        None,
    )
    if original is None or not _same_immutable_chunk(original, tightened):
        raise PublishRejected("ACL tightening changed immutable fixture provenance")
    proof = _acl_tightening_proof(original, tightened)
    if (
        not isinstance(tightened.allowed_group_ids, tuple)
        or len(tightened.allowed_group_ids) > 128
        or len(set(tightened.allowed_group_ids)) != len(tightened.allowed_group_ids)
        or any(not isinstance(group, str) or not group for group in tightened.allowed_group_ids)
        or type(tightened.deleted) is not bool
        or not isinstance(vector, tuple)
        or len(vector) != manifest.vector_dimension
        or any(type(value) is not float or not math.isfinite(value) for value in vector)
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
        await clients.writer.upsert(manifest.physical_collection, (row,))
        await clients.writer.flush(manifest.physical_collection)
        revoked_rows = await _query_persisted_rows(
            clients,
            manifest.physical_collection,
            proof[1],
            ("chunk_id",),
            1,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        raise PublishRejected("Milvus ACL tightening verification failed closed") from None
    if revoked_rows:
        raise PublishRejected("revoked subject retained Milvus access")
    return AclTighteningReceipt(
        physical_collection=manifest.physical_collection,
        chunk_id=tightened.chunk_id,
        proof_kind=proof[0],
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
    expected_rows: tuple[Mapping[str, object], ...],
) -> None:
    corpus = json.dumps(manifest.corpus_version)
    physical = json.dumps(manifest.physical_collection)
    rows = await _query_persisted_rows(
        clients,
        manifest.physical_collection,
        f"corpus_version == {corpus} and physical_collection == {physical}",
        _PERSISTED_FIELDS,
        len(expected_rows) + 1,
    )
    if len(rows) != len(expected_rows) or any(
        not isinstance(row, Mapping) or set(row) != set(_PERSISTED_FIELDS) for row in rows
    ):
        raise PublishRejected("Milvus positive reconciliation probe failed")
    expected_by_id = {
        cast(str, row["chunk_id"]): _canonical_persisted_row(row, manifest) for row in expected_rows
    }
    actual_by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        chunk_id = row.get("chunk_id")
        if not isinstance(chunk_id, str) or chunk_id in actual_by_id:
            raise PublishRejected("Milvus positive reconciliation probe failed")
        actual_by_id[chunk_id] = _canonical_persisted_row(row, manifest)
    if actual_by_id != expected_by_id:
        raise PublishRejected("Milvus positive reconciliation probe failed")


def _canonical_persisted_row(
    row: Mapping[str, object],
    manifest: DocFixtureManifest,
) -> dict[str, object]:
    if set(row) != set(_PERSISTED_FIELDS) or any(
        type(row.get(field)) is not str for field in _PERSISTED_REQUIRED_TEXT_FIELDS
    ):
        raise PublishRejected("Milvus persisted row types are malformed")
    if any(
        value is not None and type(value) is not str
        for value in (row.get("parent_id"), row.get("title"))
    ):
        raise PublishRejected("Milvus persisted row types are malformed")
    groups = row.get("allowed_group_ids")
    derived = row.get("derived_from_chunk_ids")
    vector = row.get("dense_vector")
    if (
        type(groups) is not list
        or any(type(value) is not str for value in cast(list[object], groups))
        or not _classification_rank_is_valid(row.get("classification_rank"))
        or type(row.get("deleted")) is not bool
        or type(derived) is not list
        or any(type(value) is not str for value in cast(list[object], derived))
        or not isinstance(vector, (list, tuple))
        or len(vector) != manifest.vector_dimension
        or any(type(value) is not float or not math.isfinite(value) for value in vector)
    ):
        raise PublishRejected("Milvus persisted row types are malformed")
    canonical = dict(row)
    canonical["dense_vector"] = list(vector)
    return canonical


async def _negative_probe(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
) -> None:
    corpus = json.dumps(manifest.corpus_version)
    physical = json.dumps(manifest.physical_collection)
    rows = await _query_persisted_rows(
        clients,
        manifest.physical_collection,
        f"corpus_version != {corpus} or physical_collection != {physical}",
        ("chunk_id",),
        1,
    )
    if rows:
        raise PublishRejected("Milvus negative reconciliation probe failed")


async def _query_persisted_rows(
    clients: MilvusPublishClients,
    collection_name: str,
    filter_expression: str,
    output_fields: tuple[str, ...],
    limit: int,
) -> tuple[Mapping[str, object], ...]:
    operation = getattr(clients.reader, "query_persisted_rows", None)
    if not callable(operation):
        raise PublishRejected("Milvus persisted-row reader is unavailable")
    rows = await operation(collection_name, filter_expression, output_fields, limit)
    if not isinstance(rows, tuple) or any(not isinstance(row, Mapping) for row in rows):
        raise PublishRejected("Milvus persisted-row result is malformed")
    return cast(tuple[Mapping[str, object], ...], rows)


async def _current_alias_target(clients: MilvusPublishClients, alias: str) -> str | None:
    value = await clients.provisioner.describe_alias(alias)
    if value is None:
        return None
    if not isinstance(value, str) or _SAFE_PHYSICAL.fullmatch(value) is None:
        raise PublishRejected("Milvus alias target is malformed")
    return value


async def _collection_exists(clients: MilvusPublishClients, physical: str) -> bool:
    operation = getattr(clients.provisioner, "collection_exists", None)
    if not callable(operation):
        raise PublishRejected("Milvus collection inventory is unavailable")
    result = await operation(physical)
    if type(result) is not bool:
        raise PublishRejected("Milvus collection inventory is malformed")
    return result


async def _collection_grants(
    clients: MilvusPublishClients,
    physical: str,
    role_name: str,
) -> frozenset[str]:
    operation = getattr(clients.provisioner, "collection_grants", None)
    if not callable(operation):
        raise PublishRejected("Milvus scoped-grant inventory is unavailable")
    result = await operation(physical, role_name)
    if not isinstance(result, frozenset) or any(
        not isinstance(grant, MilvusScopedGrant)
        or grant.role_name != role_name
        or grant.object_type != "Collection"
        or grant.resource_level != "collection"
        or not grant.db_name
        or grant.object_name != physical
        or not grant.privilege
        for grant in result
    ):
        raise PublishRejected("Milvus scoped-grant inventory is malformed")
    grants = cast(frozenset[MilvusScopedGrant], result)
    return frozenset(grant.privilege for grant in grants)


async def _require_exact_collection_grants(
    clients: MilvusPublishClients,
    physical: str,
    role_name: str,
    expected: frozenset[str],
) -> None:
    if await _collection_grants(clients, physical, role_name) != expected:
        raise PublishRejected("Milvus scoped grant reconciliation failed")


async def _rollback(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
    activator: CorpusActivator,
    *,
    previous_target: str | None,
    previous_marker: CorpusActivationState | None,
    preexisting: bool,
    create_attempted: bool,
    writer_grant_attempted: bool,
    reader_grant_attempted: bool,
    alias_attempted: bool,
    activation_attempted: bool,
) -> tuple[str, ...]:
    issues: list[str] = []
    try:
        current = await _current_alias_target(clients, manifest.alias)
        if (
            alias_attempted
            and current == manifest.physical_collection
            and previous_target != current
        ):
            if previous_target is None:
                await clients.provisioner.drop_alias(manifest.alias)
            else:
                await clients.provisioner.alter_alias(manifest.alias, previous_target)
        if await _current_alias_target(clients, manifest.alias) != previous_target:
            issues.append("alias")
    except Exception:
        issues.append("alias")
    if activation_attempted:
        try:
            await activator.restore(previous_marker)
            if await activator.snapshot() != previous_marker:
                issues.append("activation")
        except Exception:
            issues.append("activation")
    for attempted, role_name in (
        (writer_grant_attempted, "tap_writer"),
        (reader_grant_attempted, "tap_reader"),
    ):
        if not attempted or preexisting:
            continue
        try:
            if await _collection_grants(
                clients,
                manifest.physical_collection,
                role_name,
            ):
                await clients.provisioner.revoke_collection(
                    manifest.physical_collection,
                    role_name,
                )
            if await _collection_grants(
                clients,
                manifest.physical_collection,
                role_name,
            ):
                issues.append(role_name)
        except Exception:
            issues.append(role_name)
    if create_attempted and not preexisting:
        try:
            if await _collection_exists(
                clients,
                manifest.physical_collection,
            ):
                issues.append("collection")
        except Exception:
            issues.append("collection")
    return tuple(issues)


def _activation_matches(
    state: CorpusActivationState | None,
    manifest: DocFixtureManifest,
    digest: str,
) -> bool:
    return (
        state is not None
        and state.corpus_version == manifest.corpus_version
        and state.physical_collection == manifest.physical_collection
        and state.manifest_sha256 == digest
    )


def _same_immutable_chunk(original: DocFixtureChunk, tightened: DocFixtureChunk) -> bool:
    mutable = {"allowed_group_ids", "classification_rank", "environment", "deleted"}
    original_values = asdict(original)
    tightened_values = asdict(tightened)
    return all(
        original_values[name] == tightened_values[name]
        for name in original_values
        if name not in mutable
    )


def _acl_tightening_proof(
    original: DocFixtureChunk,
    tightened: DocFixtureChunk,
) -> tuple[str, str]:
    if not _classification_rank_is_valid(
        original.classification_rank
    ) or not _classification_rank_is_valid(tightened.classification_rank):
        raise PublishRejected("ACL classification rank must be an integer from 0 through 3")
    old_groups = set(original.allowed_group_ids)
    new_groups = set(tightened.allowed_group_ids)
    environment_narrows = tightened.environment == original.environment or (
        original.environment == "global" and tightened.environment in {"production", "staging"}
    )
    if (
        original.deleted
        or not new_groups <= old_groups
        or tightened.classification_rank < original.classification_rank
        or not environment_narrows
    ):
        raise PublishRejected("ACL changes must be monotonic authorization narrowing")
    chunk = json.dumps(original.chunk_id)
    if not original.deleted and tightened.deleted:
        return "deleted", f"chunk_id == {chunk} and deleted == false"
    removed_groups = sorted(old_groups - new_groups)
    if removed_groups:
        removed = json.dumps(removed_groups, separators=(",", ":"))
        return (
            "removed_group",
            f"chunk_id == {chunk} and ARRAY_CONTAINS_ANY(allowed_group_ids, {removed})",
        )
    if tightened.classification_rank > original.classification_rank:
        return (
            "classification",
            f"chunk_id == {chunk} and classification_rank <= {original.classification_rank}",
        )
    if tightened.environment != original.environment:
        return "environment", f'chunk_id == {chunk} and environment == "global"'
    raise PublishRejected("ACL changes must remove at least one prior authorization")


def _classification_rank_is_valid(value: object) -> bool:
    return type(value) is int and 0 <= value <= 3
