"""Destructive-isolated behavioral health orchestration for local Milvus."""

from __future__ import annotations

import re
from collections.abc import Mapping

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusChannelRequest,
    MilvusHybridRequest,
    MilvusQueryRequest,
)
from tap.operations.milvus.contracts import (
    MilvusHealthReport,
    MilvusProbeClients,
)

_PROBE_ID = re.compile(r"[A-Za-z0-9_]{1,80}\Z")
_SCOPED_ROLE_NAMES = ("tap_reader", "tap_writer")
_ALLOWED_CHUNK_ID = "h_" + "1" * 64
_DENIED_CHUNK_ID = "h_" + "2" * 64
_ALLOWED_FILTER = (
    'tenant_id == "tap-health" and project_id == "tap-health" '
    'and ARRAY_CONTAINS(allowed_group_ids, "tap-health-allowed") and deleted == false'
)
_DENIED_FILTER = (
    f'chunk_id == "{_DENIED_CHUNK_ID}" and tenant_id == "tap-health" '
    'and project_id == "tap-health" '
    'and ARRAY_CONTAINS(allowed_group_ids, "tap-health-allowed") and deleted == false'
)

HEALTH_SCHEMA: Mapping[str, object] = {
    "family": "doc",
    "schema_version": "health-v1",
    "corpus_version": "health-v1",
    "embedding_model_version": "health-v1",
    "vector_dimension": 2,
}


async def run_health_probe(
    clients: MilvusProbeClients,
    probe_id: str,
) -> MilvusHealthReport:
    """Exercise an isolated collection through all three non-root identities."""
    if not isinstance(probe_id, str) or _PROBE_ID.fullmatch(probe_id) is None:
        raise ValueError("Milvus health probe ID must be a safe bounded identifier")
    collection_name = f"tap_health_probe_{probe_id}"
    alias = f"tap_health_alias_{probe_id}"
    collection_created = False
    alias_created = False
    granted_roles: list[str] = []
    try:
        await clients.provisioner.create_collection(collection_name, HEALTH_SCHEMA)
        collection_created = True
        await clients.provisioner.create_indexes(collection_name)
        for role_name in _SCOPED_ROLE_NAMES:
            granted_roles.append(role_name)
            await clients.provisioner.grant_collection(collection_name, role_name)
        await clients.provisioner.create_alias(alias, collection_name)
        alias_created = True
        await clients.provisioner.alter_alias(alias, collection_name)
        if await clients.provisioner.describe_alias(alias) != collection_name:
            raise RuntimeError("Milvus provisioner alias probe failed")

        rows = (
            _probe_row(_ALLOWED_CHUNK_ID, "tap-health-allowed"),
            _probe_row(_DENIED_CHUNK_ID, "tap-health-denied"),
        )
        await clients.writer.insert(collection_name, rows)
        await clients.writer.upsert(collection_name, (rows[0],))
        await clients.writer.flush(collection_name)

        resolved = await clients.reader.describe_alias(alias)
        await clients.reader.describe_collection(resolved)
        allowed = await clients.reader.hybrid_search(_hybrid_request(resolved))
        denied = await clients.reader.query(_denied_request(resolved))
        await clients.denied_probe.verify(collection_name)

        await clients.writer.delete(collection_name, (_DENIED_CHUNK_ID,))
        await clients.writer.flush(collection_name)
        return MilvusHealthReport(
            probe_id=probe_id,
            allowed_hits=len(allowed),
            denied_hits=len(denied),
            cleanup_complete=True,
        )
    finally:
        cleanup_failed = False
        if alias_created:
            try:
                await clients.provisioner.drop_alias(alias)
            except Exception:
                cleanup_failed = True
        for role_name in granted_roles:
            try:
                await clients.provisioner.revoke_collection(collection_name, role_name)
            except Exception:
                cleanup_failed = True
        if collection_created:
            try:
                await clients.provisioner.drop_collection(collection_name)
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise RuntimeError("Milvus health cleanup failed") from None


def _probe_row(chunk_id: str, group_id: str) -> Mapping[str, object]:
    return {
        "chunk_id": chunk_id,
        "content": "health probe",
        "dense_vector": [1.0, 0.0],
        "tenant_id": "tap-health",
        "project_id": "tap-health",
        "allowed_group_ids": [group_id],
        "classification_rank": 0,
        "environment": "local",
        "corpus_version": "health-v1",
        "deleted": False,
    }


def _hybrid_request(collection_name: str) -> MilvusHybridRequest:
    return MilvusHybridRequest(
        collection_name=collection_name,
        channels=(
            MilvusChannelRequest(
                kind="bm25",
                query="health probe",
                filter_expression=_ALLOWED_FILTER,
                limit=2,
            ),
            MilvusChannelRequest(
                kind="dense",
                query=(1.0, 0.0),
                filter_expression=_ALLOWED_FILTER,
                limit=2,
            ),
        ),
        output_fields=("chunk_id",),
        limit=2,
    )


def _denied_request(collection_name: str) -> MilvusQueryRequest:
    return MilvusQueryRequest(
        collection_name=collection_name,
        filter_expression=_DENIED_FILTER,
        output_fields=("chunk_id",),
        limit=2,
    )
