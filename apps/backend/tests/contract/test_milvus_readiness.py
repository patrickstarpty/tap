from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Never, cast

import pytest
from fastapi.testclient import TestClient
from test_milvus_mapping import doc_target
from test_milvus_search_strict import descriptor

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices, KnowledgeHttpService
from tap.modules.knowledge.adapters.milvus.readiness import (
    MilvusReadinessCanary,
    MilvusReadinessProbe,
)
from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusHybridRequest,
    MilvusQueryRequest,
)
from tap.modules.knowledge.ports.errors import SearchUnavailable

CANARY_CHUNK = "h_" + "7" * 64


def canary() -> MilvusReadinessCanary:
    return MilvusReadinessCanary(
        chunk_id=CANARY_CHUNK,
        tenant_id="tenant-canary",
        project_id="project-canary",
        group_id="group-canary",
        corpus_version="corpus-fixture-v1",
    )


class ReadinessReader:
    def __init__(
        self,
        rows: tuple[Mapping[str, object], ...] = ({"chunk_id": CANARY_CHUNK},),
        *,
        delay: float = 0,
    ) -> None:
        self.rows = rows
        self.delay = delay
        self.alias_calls: list[str] = []
        self.collection_calls: list[str] = []
        self.query_calls: list[MilvusQueryRequest] = []
        self.hybrid_calls: list[MilvusHybridRequest] = []

    async def describe_alias(self, alias: str) -> str:
        self.alias_calls.append(alias)
        return "kb_doc_v1_corpus_fixture_v1"

    async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
        self.collection_calls.append(collection_name)
        return descriptor()

    async def hybrid_search(self, request: MilvusHybridRequest) -> tuple[Mapping[str, object], ...]:
        self.hybrid_calls.append(request)
        return ()

    async def query(self, request: MilvusQueryRequest) -> tuple[Mapping[str, object], ...]:
        self.query_calls.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.rows

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_readiness_binds_once_then_runs_one_closed_acl_canary_query() -> None:
    """Dropping any canary predicate or widening output would make readiness non-authoritative."""
    reader = ReadinessReader()

    await MilvusReadinessProbe(doc_target(), reader, canary()).check()

    assert reader.alias_calls == ["kb_doc_active"]
    assert reader.collection_calls == ["kb_doc_v1_corpus_fixture_v1"]
    assert len(reader.query_calls) == 1
    assert reader.hybrid_calls == []
    request = reader.query_calls[0]
    assert request.collection_name == "kb_doc_v1_corpus_fixture_v1"
    assert request.output_fields == ("chunk_id",)
    assert request.limit == 2
    assert request.filter_expression == (
        f'chunk_id == "{CANARY_CHUNK}" '
        'and tenant_id == "tenant-canary" '
        'and project_id == "project-canary" '
        'and ARRAY_CONTAINS(allowed_group_ids, "group-canary") '
        'and corpus_version == "corpus-fixture-v1" '
        "and deleted == false"
    )


@pytest.mark.asyncio
async def test_readiness_canary_filter_is_exact_and_safely_escaped() -> None:
    """Concatenation or appended widening would change the complete observed expression."""
    reader = ReadinessReader()
    escaped = MilvusReadinessCanary(
        chunk_id=CANARY_CHUNK,
        tenant_id='tenant-"quoted"\\路径',
        project_id="project-付款",
        group_id='group-"reader"\\值',
        corpus_version="corpus-版本-v1",
    )

    await MilvusReadinessProbe(doc_target(), reader, escaped).check()

    assert len(reader.query_calls) == 1
    assert reader.query_calls[0].filter_expression == (
        f'chunk_id == "{CANARY_CHUNK}" '
        'and tenant_id == "tenant-\\"quoted\\"\\\\路径" '
        'and project_id == "project-付款" '
        'and ARRAY_CONTAINS(allowed_group_ids, "group-\\"reader\\"\\\\值") '
        'and corpus_version == "corpus-版本-v1" '
        "and deleted == false"
    )


@pytest.mark.parametrize(
    "rows",
    (
        (),
        ({"chunk_id": CANARY_CHUNK}, {"chunk_id": CANARY_CHUNK}),
        ({"chunk_id": "h_" + "8" * 64},),
        ({"wrong": CANARY_CHUNK},),
        ({"chunk_id": CANARY_CHUNK, "tenant_id": "tenant-canary"},),
    ),
    ids=("missing", "multiple", "wrong-chunk", "missing-field", "widened-row"),
)
@pytest.mark.asyncio
async def test_readiness_rejects_any_non_exact_canary_result(
    rows: tuple[Mapping[str, object], ...],
) -> None:
    """Accepting a non-exact scalar result would report readiness without the canary contract."""
    with pytest.raises(SearchUnavailable, match="readiness canary failed"):
        await MilvusReadinessProbe(doc_target(), ReadinessReader(rows), canary()).check()


@pytest.mark.asyncio
async def test_readiness_timeout_is_bounded_and_provider_neutral() -> None:
    """Missing the outer timeout would let readiness hang behind a nonconforming reader."""
    reader = ReadinessReader(delay=0.05)

    with pytest.raises(SearchUnavailable, match="readiness check timed out") as raised:
        await MilvusReadinessProbe(doc_target(), reader, canary(), timeout_seconds=0.01).check()

    assert raised.value.__cause__ is None


class UnavailableKnowledgeService:
    """Fail if an unrelated route reaches the deferred knowledge dependency."""

    def __init__(self) -> None:
        self.accessed = False

    def __getattr__(self, name: str) -> Never:
        self.accessed = True
        msg = f"liveness must not access the knowledge service ({name})"
        raise AssertionError(msg)


def test_http_liveness_does_not_access_search_or_knowledge_readiness() -> None:
    """Resolving search readiness from liveness would make process health depend on Milvus."""
    service = UnavailableKnowledgeService()
    client = TestClient(create_app(HttpServices(knowledge=cast(KnowledgeHttpService, service))))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.accessed is False
