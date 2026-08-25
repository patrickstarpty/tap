from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import pytest
from pydantic import SecretStr
from test_milvus_filter import doc_execution
from test_milvus_mapping import doc_target, valid_doc_row

from tap.modules.knowledge.adapters.milvus.audit import (
    MilvusSearchAuditEvent,
    SearchAuditSink,
)
from tap.modules.knowledge.adapters.milvus.config import MilvusSearchConfig
from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.adapters.milvus.transport import (
    MILVUS_OUTPUT_FIELDS,
    MilvusCollectionDescriptor,
    MilvusHybridRequest,
    MilvusQueryRequest,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable


def descriptor() -> MilvusCollectionDescriptor:
    return MilvusCollectionDescriptor(
        collection_name="kb_doc_v1_corpus_fixture_v1",
        family=SourceFamily.DOC,
        schema_version="doc-schema-v1",
        schema_sha256="sha256:" + "c" * 64,
        corpus_version="corpus-fixture-v1",
        embedding_model_version="research-embedding-v1",
        vector_dimension=1536,
        dynamic_fields_enabled=False,
        consistency_level="Strong",
    )


def config(**changes: object) -> MilvusSearchConfig:
    values: dict[str, object] = {
        "uri": "http://127.0.0.1:19530",
        "database": "tap_local",
        "username": "tap_reader",
        "password": SecretStr("tap-local-reader"),
        "targets": {SourceFamily.DOC: doc_target()},
    }
    values.update(changes)
    return MilvusSearchConfig(**values)  # type: ignore[arg-type]


class RecordingReader:
    def __init__(
        self,
        rows: tuple[Mapping[str, object], ...] | None = None,
        *,
        collection_descriptor: MilvusCollectionDescriptor | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.requests: list[MilvusHybridRequest] = []
        self.alias_calls: list[str] = []
        self.collection_calls: list[str] = []
        self.query_calls: list[MilvusQueryRequest] = []
        self.rows = (valid_doc_row(),) if rows is None else rows
        self.collection_descriptor = collection_descriptor or descriptor()
        self.failure = failure

    async def describe_alias(self, alias: str) -> str:
        self.alias_calls.append(alias)
        return "kb_doc_v1_corpus_fixture_v1"

    async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
        self.collection_calls.append(collection_name)
        return self.collection_descriptor

    async def hybrid_search(self, request: MilvusHybridRequest) -> tuple[Mapping[str, object], ...]:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.rows

    async def query(self, request: MilvusQueryRequest) -> tuple[Mapping[str, object], ...]:
        self.query_calls.append(request)
        return ()

    async def close(self) -> None:
        return None


class RecordingAuditSink(SearchAuditSink):
    def __init__(self, failure: Exception | None = None) -> None:
        self.events: list[MilvusSearchAuditEvent] = []
        self.failure = failure

    async def emit(self, event: MilvusSearchAuditEvent) -> None:
        self.events.append(event)
        if self.failure is not None:
            raise self.failure


@pytest.mark.asyncio
async def test_search_binds_once_and_builds_one_closed_bounded_hybrid_request() -> None:
    """Changing channel filters, fields, limits, or binding count breaks the request contract."""
    reader = RecordingReader()
    audit = RecordingAuditSink()
    execution = doc_execution()
    object.__setattr__(execution.plan, "candidate_limit", 12)

    hits = await MilvusSearchAdapter(config(candidate_limit=7), reader, audit).search(execution)

    request = reader.requests.pop()
    bm25, dense = request.channels
    assert reader.alias_calls == ["kb_doc_active"]
    assert reader.collection_calls == ["kb_doc_v1_corpus_fixture_v1"]
    assert bm25.kind == "bm25"
    assert dense.kind == "dense"
    assert bm25.query == execution.plan.sanitized_query
    assert dense.query == execution.query_vector
    assert bm25.filter_expression == dense.filter_expression
    assert request.collection_name == "kb_doc_v1_corpus_fixture_v1"
    assert request.limit == 7
    assert request.output_fields == MILVUS_OUTPUT_FIELDS
    assert tuple(hit.local_rank for hit in hits) == tuple(range(1, len(hits) + 1))
    assert len(audit.events) == 1
    assert audit.events[0].outcome == "success"


@pytest.mark.asyncio
async def test_reader_capability_identity_is_preserved_for_io_but_not_exported_to_audit() -> None:
    """String-copying before I/O or exporting the token would break reader-owned capabilities."""

    class CollectionCapability(str):
        pass

    capability = CollectionCapability("kb_doc_v1_corpus_fixture_v1")

    class CapabilityReader(RecordingReader):
        async def describe_alias(self, alias: str) -> str:
            self.alias_calls.append(alias)
            return capability

        async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
            assert collection_name is capability
            return await super().describe_collection(collection_name)

        async def hybrid_search(
            self, request: MilvusHybridRequest
        ) -> tuple[Mapping[str, object], ...]:
            assert request.collection_name is capability
            return await super().hybrid_search(request)

    audit = RecordingAuditSink()

    await MilvusSearchAdapter(config(), CapabilityReader(), audit).search(doc_execution())

    assert audit.events[0].physical_collection == capability
    assert type(audit.events[0].physical_collection) is str
    assert audit.events[0].physical_collection is not capability


@pytest.mark.parametrize(
    "mutate",
    (
        lambda execution: object.__setattr__(execution, "query_vector", ()),
        lambda execution: object.__setattr__(execution, "query_vector", (0.0,) * 1535),
        lambda execution: object.__setattr__(
            execution, "query_vector", (math.inf,) + (0.0,) * 1535
        ),
        lambda execution: object.__setattr__(
            execution.plan, "source_families", (SourceFamily.CODE,)
        ),
        lambda execution: object.__setattr__(
            execution.plan, "source_families", (SourceFamily.DOC, SourceFamily.DOC)
        ),
        lambda execution: object.__setattr__(execution.plan, "candidate_limit", 0),
    ),
    ids=("empty-vector", "wrong-dimension", "non-finite", "unconfigured", "duplicate", "limit"),
)
@pytest.mark.asyncio
async def test_execution_bounds_fail_before_any_reader_io(mutate) -> None:
    """Moving validation after target binding would expose invalid work to provider I/O."""
    execution = doc_execution()
    mutate(execution)
    reader = RecordingReader()
    audit = RecordingAuditSink()

    with pytest.raises(SearchBoundsExceeded):
        await MilvusSearchAdapter(config(), reader, audit).search(execution)

    assert reader.alias_calls == []
    assert reader.collection_calls == []
    assert reader.requests == []
    assert len(audit.events) == 1
    assert audit.events[0].error_code == "bounds"


@pytest.mark.asyncio
async def test_corrupted_target_cardinality_fails_before_any_reader_io() -> None:
    """Removing the one-doc-target guard would bind an ambiguous provider target."""
    invalid_config = config()
    object.__setattr__(invalid_config, "targets", {})
    reader = RecordingReader()

    with pytest.raises(SearchBoundsExceeded):
        await MilvusSearchAdapter(invalid_config, reader, RecordingAuditSink()).search(
            doc_execution()
        )

    assert reader.alias_calls == []


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("collection_name", "kb_doc_v1_other"),
        ("schema_version", "doc-schema-v2"),
        ("corpus_version", "corpus-other-v1"),
        ("embedding_model_version", "other-embedding-v1"),
        ("vector_dimension", 768),
    ),
)
@pytest.mark.asyncio
async def test_alias_or_collection_drift_returns_no_hits(field: str, forged: object) -> None:
    """Skipping immutable target binding would let a drifted collection return citations."""
    reader = RecordingReader(collection_descriptor=replace(descriptor(), **{field: forged}))

    with pytest.raises(SearchUnavailable):
        await MilvusSearchAdapter(config(), reader, RecordingAuditSink()).search(doc_execution())

    assert reader.requests == []


@pytest.mark.parametrize(
    "rows",
    (
        ({**valid_doc_row(), "tenant_id": "tenant-a"},),
        ({**valid_doc_row(), "chunk_id": "malformed"},),
        (valid_doc_row(), {**valid_doc_row(), "content": ""}),
    ),
    ids=("extra-field", "malformed-row", "valid-then-malformed"),
)
@pytest.mark.asyncio
async def test_any_invalid_provider_row_rejects_the_entire_page(
    rows: tuple[Mapping[str, object], ...],
) -> None:
    """Returning incrementally mapped hits would leak a partial success page."""
    audit = RecordingAuditSink()

    with pytest.raises(SearchUnavailable):
        await MilvusSearchAdapter(config(), RecordingReader(rows), audit).search(doc_execution())

    assert audit.events[0].provider_row_count == len(rows)
    assert audit.events[0].rejected_row_count == len(rows)


@pytest.mark.asyncio
async def test_rows_over_effective_candidate_limit_reject_the_entire_page() -> None:
    """Trusting a fake or widened reader could bypass the adapter's candidate bound."""
    execution = doc_execution()
    object.__setattr__(execution.plan, "candidate_limit", 1)
    rows = (valid_doc_row(), valid_doc_row())

    with pytest.raises(SearchUnavailable):
        await MilvusSearchAdapter(config(), RecordingReader(rows), RecordingAuditSink()).search(
            execution
        )


@pytest.mark.asyncio
async def test_unexpected_transport_failure_is_provider_neutral_and_returns_no_hits() -> None:
    """Leaking transport exceptions would widen the public SearchPort error surface."""
    reader = RecordingReader(failure=RuntimeError("sdk detail"))

    with pytest.raises(SearchUnavailable, match="search provider is unavailable") as raised:
        await MilvusSearchAdapter(config(), reader, RecordingAuditSink()).search(doc_execution())

    assert raised.value.__cause__ is None
