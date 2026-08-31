from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from tap.modules.knowledge.ports.models import Embedding, EmbeddingUsage
from tap.operations.milvus import embeddings as embedding_module
from tap.operations.milvus.bailian import BailianEmbeddingAdapter
from tap.operations.milvus.embeddings import (
    EMBEDDING_ALIAS,
    EMBEDDING_DIMENSION,
    EmbeddingInput,
    EmbeddingResearchRejected,
    FileEmbeddingCache,
    embedding_cache_key,
    generate_snapshot,
    load_fixture_inputs,
    load_vector_snapshot,
    research_bailian_config,
    write_research_report,
    write_vector_snapshot,
)
from tap.operations.milvus.fixtures import content_hash as fixture_content_hash

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "milvus"
DOC_FIXTURE = FIXTURES / "doc-fixture-v1.json"
QUERY_FIXTURE = FIXTURES / "query-cases-v1.json"
VECTOR_FIXTURE = FIXTURES / "vectors-research-embedding-v1.json"
REPOSITORY = Path(__file__).resolve().parents[5]
_CLI_SPEC = importlib.util.spec_from_file_location(
    "milvus_embedding_research_test_module",
    REPOSITORY / "scripts/milvus_embedding_research.py",
)
assert _CLI_SPEC is not None and _CLI_SPEC.loader is not None
research_cli = importlib.util.module_from_spec(_CLI_SPEC)
sys.modules[_CLI_SPEC.name] = research_cli
_CLI_SPEC.loader.exec_module(research_cli)


def research_input(item_id: str, text: str | None = None) -> EmbeddingInput:
    value = text if text is not None else f"{item_id} sanitized text"
    return EmbeddingInput(
        item_id=item_id,
        text=value,
        content_hash=fixture_content_hash(value),
    )


class MemoryEmbeddingCache:
    def __init__(self) -> None:
        self.values: dict[str, tuple[float, ...]] = {}
        self.get_count = 0

    def get(self, key: str) -> tuple[float, ...] | None:
        self.get_count += 1
        return self.values.get(key)

    def put(self, key: str, vector: tuple[float, ...]) -> None:
        self.values[key] = vector


class FakeEmbeddingModel:
    embedding_model_id = EMBEDDING_ALIAS
    embedding_dimension = EMBEDDING_DIMENSION

    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        cancel_on_call: int | None = None,
        request_ids: tuple[str, ...] = (),
        cost: Decimal = Decimal("0.000002"),
        cache: MemoryEmbeddingCache | None = None,
        expected_cache_reads: int | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.fail_on_call = fail_on_call
        self.cancel_on_call = cancel_on_call
        self.request_ids = request_ids
        self.cost = cost
        self.cache = cache
        self.expected_cache_reads = expected_cache_reads

    async def embed(self, query: str) -> Embedding:
        if self.cache is not None and self.expected_cache_reads is not None:
            assert self.cache.get_count == self.expected_cache_reads
        self.calls.append(query)
        call_number = len(self.calls)
        if self.fail_on_call == call_number:
            raise RuntimeError("provider body must not escape")
        if self.cancel_on_call == call_number:
            raise asyncio.CancelledError
        request_id = (
            self.request_ids[call_number - 1]
            if call_number <= len(self.request_ids)
            else f"request-{call_number}"
        )
        return Embedding(
            vector=(0.001,) * EMBEDDING_DIMENSION,
            model_id=self.embedding_model_id,
            provider_request_id=request_id,
            usage=EmbeddingUsage(
                input_tokens=4,
                total_tokens=4,
                response_cost_usd=None,
                calculated_cost_cny=self.cost,
            ),
        )


def cli_args() -> SimpleNamespace:
    return SimpleNamespace(
        doc_fixture=DOC_FIXTURE,
        query_fixture=QUERY_FIXTURE,
        cache_directory=Path(".local/milvus-embedding-cache"),
        report=Path(".local/milvus-research/report.json"),
        candidate_snapshot=Path(".local/milvus-research/vectors-research-embedding-v1.json"),
        max_chunks=100,
        max_queries=20,
    )


def cli_settings() -> dict[str, str]:
    return {
        "LITELLM_BASE_URL": "http://127.0.0.1:4000",
        "LITELLM_MASTER_KEY": "sanitized-test-key",
        "LITELLM_EMBEDDING_MODEL": "text-embedding-v4",
        "LITELLM_EMBEDDING_API_KEY": "sanitized-provider-key",
        "LITELLM_EMBEDDING_API_BASE": (
            "https://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        ),
    }


def test_cache_key_is_exactly_bound_to_model_dimension_and_hash() -> None:
    digest = "sha256:" + "a" * 64
    expected = (
        "h_"
        + hashlib.sha256(("research-embedding-v1\x001536\x00" + digest).encode("utf-8")).hexdigest()
    )
    assert embedding_cache_key(EMBEDDING_ALIAS, EMBEDDING_DIMENSION, digest) == expected
    assert embedding_cache_key("other", EMBEDDING_DIMENSION, digest) != expected
    assert embedding_cache_key(EMBEDDING_ALIAS, 2, digest) != expected
    assert (
        embedding_cache_key(EMBEDDING_ALIAS, EMBEDDING_DIMENSION, "sha256:" + "b" * 64) != expected
    )


def test_committed_vector_snapshot_loads_against_exact_fixture_hashes() -> None:
    chunks, queries = load_fixture_inputs(DOC_FIXTURE, QUERY_FIXTURE)

    snapshot = load_vector_snapshot(
        VECTOR_FIXTURE,
        chunk_hashes={item.item_id: item.content_hash for item in chunks},
        query_hashes={item.item_id: item.content_hash for item in queries},
    )

    assert snapshot.model_id == EMBEDDING_ALIAS
    assert snapshot.dimension == EMBEDDING_DIMENSION
    assert len(snapshot.chunks) == 12
    assert len(snapshot.queries) == 8


@pytest.mark.parametrize(
    "mutation",
    ("extra-field", "wrong-model", "wrong-dimension", "wrong-hash", "integer-vector"),
)
def test_vector_snapshot_loader_rejects_widened_identity_and_vector_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    chunk_hash = "sha256:" + "a" * 64
    query_hash = "sha256:" + "b" * 64
    raw: dict[str, object] = {
        "chunks": {"chunk-1": {"inputHash": chunk_hash, "vector": [0.001] * EMBEDDING_DIMENSION}},
        "dimension": EMBEDDING_DIMENSION,
        "modelId": EMBEDDING_ALIAS,
        "queries": {"query-1": {"inputHash": query_hash, "vector": [0.001] * EMBEDDING_DIMENSION}},
    }
    if mutation == "extra-field":
        raw["extra"] = True
    elif mutation == "wrong-model":
        raw["modelId"] = "other-model"
    elif mutation == "wrong-dimension":
        raw["dimension"] = 1024
    elif mutation == "wrong-hash":
        raw["chunks"]["chunk-1"]["inputHash"] = "sha256:" + "c" * 64  # type: ignore[index]
    else:
        raw["chunks"]["chunk-1"]["vector"][0] = 0  # type: ignore[index]
    path = tmp_path / "vectors.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(EmbeddingResearchRejected, match="vector snapshot"):
        load_vector_snapshot(
            path,
            chunk_hashes={"chunk-1": chunk_hash},
            query_hashes={"query-1": query_hash},
        )


def test_vector_snapshot_loader_rejects_duplicate_keys_and_nonfinite_constants(
    tmp_path: Path,
) -> None:
    path = tmp_path / "vectors.json"
    for payload in (
        '{"chunks":{},"chunks":{},"dimension":1536,"modelId":"research-embedding-v1","queries":{}}',
        '{"chunks":{},"dimension":1536,"modelId":"research-embedding-v1","queries":{},"value":NaN}',
    ):
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(EmbeddingResearchRejected, match="vector snapshot"):
            load_vector_snapshot(path, chunk_hashes={}, query_hashes={})


@pytest.mark.asyncio
async def test_whole_run_preflight_and_all_cache_reads_precede_first_model_call() -> None:
    cache = MemoryEmbeddingCache()
    chunks = (research_input("chunk-1"), research_input("chunk-2"))
    queries = (research_input("query-1"),)
    model = FakeEmbeddingModel(cache=cache, expected_cache_reads=3)

    snapshot, report = await generate_snapshot(model, chunks, queries, cache)

    assert model.calls == [item.text for item in (*chunks, *queries)]
    assert set(snapshot.chunks) == {"chunk-1", "chunk-2"}
    assert set(snapshot.queries) == {"query-1"}
    assert report.cache_misses == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "queries", "max_chunks", "max_queries"),
    [
        (tuple(research_input(f"chunk-{i}") for i in range(101)), (), 100, 20),
        ((), tuple(research_input(f"query-{i}") for i in range(21)), 100, 20),
        (tuple(research_input(f"chunk-{i}") for i in range(501)), (), 500, 100),
        ((), tuple(research_input(f"query-{i}") for i in range(101)), 500, 100),
    ],
)
async def test_default_and_hard_caps_reject_before_model_calls(
    chunks: tuple[EmbeddingInput, ...],
    queries: tuple[EmbeddingInput, ...],
    max_chunks: int,
    max_queries: int,
) -> None:
    model = FakeEmbeddingModel()

    with pytest.raises(EmbeddingResearchRejected, match="bounded research profile"):
        await generate_snapshot(
            model,
            chunks,
            queries,
            MemoryEmbeddingCache(),
            max_chunks=max_chunks,
            max_queries=max_queries,
        )

    assert model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "bad-hash",
        "oversized-text",
        "duplicate-id",
        "hash-collision",
        "wrong-text-type",
        "wrong-model",
        "wrong-dimension",
    ],
)
async def test_identity_hash_text_and_vector_space_are_closed_before_calls(mutation: str) -> None:
    first = research_input("chunk-1")
    second = research_input("chunk-2")
    chunks = (first, second)
    model = FakeEmbeddingModel()
    if mutation == "bad-hash":
        chunks = (replace(first, content_hash="sha256:" + "0" * 64), second)
    elif mutation == "oversized-text":
        oversized = "x" * 8_001
        chunks = (research_input("chunk-1", oversized), second)
    elif mutation == "duplicate-id":
        chunks = (first, replace(second, item_id=first.item_id))
    elif mutation == "hash-collision":
        chunks = (first, replace(second, content_hash=first.content_hash))
    elif mutation == "wrong-text-type":
        object.__setattr__(first, "text", 17)
    elif mutation == "wrong-model":
        model.embedding_model_id = "other-model"
    else:
        model.embedding_dimension = 2

    with pytest.raises(EmbeddingResearchRejected):
        await generate_snapshot(model, chunks, (), MemoryEmbeddingCache())

    assert model.calls == []


@pytest.mark.parametrize("cost", [Decimal("1E-999"), Decimal("1E+1")])
def test_embedding_usage_rejects_noncanonical_decimal_exponents(cost: Decimal) -> None:
    with pytest.raises(ValueError, match="cost"):
        EmbeddingUsage(input_tokens=1, total_tokens=1, response_cost_usd=cost)


@pytest.mark.asyncio
async def test_same_normalized_content_across_chunk_and_query_is_embedded_once_and_fanned_out() -> (
    None
):
    chunk = research_input("chunk-1", "e\u0301\r\nsanitized")
    query = research_input("query-1", "é\nsanitized")
    cache = MemoryEmbeddingCache()
    model = FakeEmbeddingModel(cache=cache, expected_cache_reads=1)

    first, first_report = await generate_snapshot(model, (chunk,), (query,), cache)
    second, second_report = await generate_snapshot(model, (chunk,), (query,), cache)

    assert model.calls == ["é\nsanitized"]
    assert first.chunks["chunk-1"] == first.queries["query-1"]
    assert second == first
    assert first_report.cache_hits == 0
    assert first_report.cache_misses == 1
    assert first_report.input_tokens == 4
    assert first_report.calculated_cost_cny == Decimal("0.000002")
    assert first_report.currency == "CNY"
    assert second_report.cache_hits == 1
    assert second_report.cache_misses == 0
    assert cache.get_count == 2


@pytest.mark.asyncio
async def test_verified_digest_collision_with_different_normalized_text_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collision = "sha256:" + "c" * 64
    chunks = (
        EmbeddingInput(item_id="chunk-1", text="first", content_hash=collision),
        EmbeddingInput(item_id="chunk-2", text="second", content_hash=collision),
    )
    model = FakeEmbeddingModel()
    monkeypatch.setattr(embedding_module, "content_hash", lambda _value: collision)

    with pytest.raises(EmbeddingResearchRejected, match="hash collision"):
        await generate_snapshot(model, chunks, (), MemoryEmbeddingCache())

    assert model.calls == []


@pytest.mark.asyncio
async def test_cache_hit_is_deterministic_and_second_run_has_zero_paid_usage() -> None:
    chunks = (research_input("chunk-1"), research_input("chunk-2"))
    queries = (research_input("query-1"),)
    cache = MemoryEmbeddingCache()
    model = FakeEmbeddingModel()

    first, first_report = await generate_snapshot(model, chunks, queries, cache)
    call_count = len(model.calls)
    second, second_report = await generate_snapshot(model, chunks, queries, cache)

    assert second == first
    assert len(model.calls) == call_count
    assert first_report.cache_misses == 3
    assert second_report.cache_hits == 3
    assert second_report.cache_misses == 0
    assert second_report.input_tokens == 0
    assert second_report.calculated_cost_cny == Decimal("0")
    assert second_report.provider_request_ids == ()
    assert set(asdict(second_report)) == {
        "model_id",
        "dimension",
        "chunk_count",
        "query_count",
        "cache_hits",
        "cache_misses",
        "input_tokens",
        "currency",
        "unit_price_per_1000_input_tokens",
        "calculated_cost_cny",
        "pricing_source",
        "provider_request_ids",
        "started_at",
        "finished_at",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "cancel"])
async def test_provider_failure_or_cancellation_does_not_write_partial_cache(failure: str) -> None:
    cache = MemoryEmbeddingCache()
    model = FakeEmbeddingModel(
        fail_on_call=2 if failure == "provider" else None,
        cancel_on_call=2 if failure == "cancel" else None,
    )
    chunks = (research_input("chunk-1"), research_input("chunk-2"))

    expected = asyncio.CancelledError if failure == "cancel" else RuntimeError
    with pytest.raises(expected):
        await generate_snapshot(model, chunks, (), cache)

    assert cache.values == {}


@pytest.mark.asyncio
async def test_cache_commit_failure_leaves_only_complete_content_addressed_entries() -> None:
    class FailingPutCache(MemoryEmbeddingCache):
        def __init__(self) -> None:
            super().__init__()
            self.put_count = 0

        def put(self, key: str, vector: tuple[float, ...]) -> None:
            self.put_count += 1
            if self.put_count == 2:
                raise OSError("cache unavailable")
            super().put(key, vector)

    cache = FailingPutCache()
    model = FakeEmbeddingModel()
    with pytest.raises(OSError, match="cache unavailable"):
        await generate_snapshot(
            model,
            (research_input("chunk-1"), research_input("chunk-2")),
            (),
            cache,
        )

    assert len(model.calls) == 2
    assert len(cache.values) == 1
    assert all(len(vector) == EMBEDDING_DIMENSION for vector in cache.values.values())


@pytest.mark.asyncio
async def test_request_ids_are_safely_bounded_deduplicated_and_stable() -> None:
    model = FakeEmbeddingModel(request_ids=("req-a", "req-a", "req-b"))
    chunks = tuple(research_input(f"chunk-{index}") for index in range(3))

    _snapshot, report = await generate_snapshot(model, chunks, (), MemoryEmbeddingCache())

    assert report.provider_request_ids == ("req-a", "req-b")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_id",
    ["", "x" * 129, "secret value", "line\nbreak", "路径"],
)
async def test_unsafe_provider_request_ids_fail_closed(request_id: str) -> None:
    model = FakeEmbeddingModel(request_ids=(request_id,))

    with pytest.raises(EmbeddingResearchRejected, match="provider request identity"):
        await generate_snapshot(
            model,
            (research_input("chunk-1"),),
            (),
            MemoryEmbeddingCache(),
        )


@pytest.mark.asyncio
async def test_usage_and_exact_official_cny_calculation_are_required() -> None:
    class InvalidUsageModel(FakeEmbeddingModel):
        async def embed(self, query: str) -> Embedding:
            result = await super().embed(query)
            return replace(result, usage=None)

    with pytest.raises(EmbeddingResearchRejected, match="usage and cost"):
        await generate_snapshot(
            InvalidUsageModel(),
            (research_input("chunk-1"),),
            (),
            MemoryEmbeddingCache(),
        )

    expensive = FakeEmbeddingModel(cost=Decimal("60"))
    with pytest.raises(EmbeddingResearchRejected, match="usage"):
        await generate_snapshot(
            expensive,
            (research_input("chunk-1"), research_input("chunk-2")),
            (),
            MemoryEmbeddingCache(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input_tokens", True),
        ("input_tokens", -1),
        ("input_tokens", 1_000_001),
        ("total_tokens", False),
        ("total_tokens", 3),
        ("total_tokens", 1_000_001),
        ("response_cost_usd", Decimal("0.01")),
        ("calculated_cost_cny", None),
        ("calculated_cost_cny", "0.01"),
        ("calculated_cost_cny", Decimal("NaN")),
        ("calculated_cost_cny", Decimal("1E-19")),
        ("calculated_cost_cny", Decimal("101")),
    ],
)
async def test_core_revalidates_mutated_usage_before_cache_or_output(
    field: str,
    value: object,
) -> None:
    class MutatedUsageModel(FakeEmbeddingModel):
        async def embed(self, query: str) -> Embedding:
            embedding = await super().embed(query)
            assert embedding.usage is not None
            object.__setattr__(embedding.usage, field, value)
            return embedding

    cache = MemoryEmbeddingCache()

    with pytest.raises(EmbeddingResearchRejected, match="usage"):
        await generate_snapshot(
            MutatedUsageModel(),
            (research_input("chunk-1"),),
            (),
            cache,
        )

    assert cache.values == {}


def test_file_cache_round_trips_atomic_closed_entries_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    cache = FileEmbeddingCache(tmp_path / "cache")
    item = research_input("chunk-1")
    key = embedding_cache_key(EMBEDDING_ALIAS, EMBEDDING_DIMENSION, item.content_hash)
    vector = (0.001,) * EMBEDDING_DIMENSION

    assert cache.get(key) is None
    cache.put(key, vector)
    assert cache.get(key) == vector
    assert list((tmp_path / "cache").glob(".*.tmp")) == []

    entry = tmp_path / "cache" / f"{key}.json"
    raw = json.loads(entry.read_text())
    raw["dimension"] = 2
    entry.write_text(json.dumps(raw))
    with pytest.raises(EmbeddingResearchRejected, match="cache entry"):
        cache.get(key)


def test_report_and_snapshot_writes_are_closed_atomic_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MemoryEmbeddingCache()
    model = FakeEmbeddingModel()
    chunks = (research_input("chunk-secret", "sanitized body must stay out"),)
    snapshot, report = asyncio.run(generate_snapshot(model, chunks, (), cache))
    report_path = tmp_path / "report.json"
    snapshot_path = tmp_path / "snapshot.json"

    write_research_report(report_path, report)
    write_vector_snapshot(snapshot_path, snapshot)

    report_text = report_path.read_text()
    snapshot_value = json.loads(snapshot_path.read_text())
    assert "sanitized body must stay out" not in report_text
    assert "0.001" not in report_text
    assert set(json.loads(report_text)) == {
        "cacheHits",
        "cacheMisses",
        "chunkCount",
        "dimension",
        "finishedAt",
        "inputTokens",
        "modelId",
        "providerRequestIds",
        "queryCount",
        "calculatedCostCny",
        "currency",
        "pricingSource",
        "startedAt",
        "unitPricePer1000InputTokens",
    }
    assert set(snapshot_value) == {"chunks", "dimension", "modelId", "queries"}
    assert set(snapshot_value["chunks"]["chunk-secret"]) == {"inputHash", "vector"}
    assert "sanitized body must stay out" not in repr(chunks[0])
    assert "0.001" not in repr(snapshot)

    before = report_path.read_bytes()

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(embedding_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_research_report(report_path, report)
    assert report_path.read_bytes() == before
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"provider_request_ids": ("PRIVATE SECRET",)},
        {"calculated_cost_cny": Decimal("NaN")},
        {"currency": "USD"},
        {"input_tokens": True},
        {"cache_misses": 601},
        {"model_id": "other-model"},
    ],
)
def test_report_writer_revalidates_runtime_mutation_before_persistence(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    report = embedding_module.EmbeddingResearchReport(
        model_id=EMBEDDING_ALIAS,
        dimension=EMBEDDING_DIMENSION,
        chunk_count=1,
        query_count=0,
        cache_hits=0,
        cache_misses=1,
        input_tokens=4,
        currency="CNY",
        unit_price_per_1000_input_tokens=Decimal("0.0005"),
        calculated_cost_cny=Decimal("0.000002"),
        pricing_source="official_rate_2026-08-27",
        provider_request_ids=("request-1",),
        started_at="2026-08-26T00:00:00.000Z",
        finished_at="2026-08-26T00:00:01.000Z",
    )
    mutated = replace(report, **mutation)
    path = tmp_path / "report.json"

    with pytest.raises(EmbeddingResearchRejected, match="report"):
        write_research_report(path, mutated)

    assert not path.exists()


def test_default_loader_uses_only_the_trusted_task8_fixture_and_queries() -> None:
    chunks, queries = load_fixture_inputs(DOC_FIXTURE, QUERY_FIXTURE)

    assert len(chunks) == 12
    assert len(queries) == 8
    assert {item.item_id for item in chunks} == {
        "h_092942db17b1bd1d119c8eb111a8249d94e9db074bd2a34019b6bd33f2dba5ff",
        "h_472b2fa4711da1223e4058f816767ee7c5c0318ff4906380211763dd40f7ad60",
        "h_799ece3989c0faf10aabae36dfcf8ece9a120a9863d4f31854b190f7d9f08d24",
        "h_fb0507540d4a28658f3805f5e552ce244d6992bea71fdf7d7bad635870b1008e",
        "h_ea1622d4667f6985f5f59b5e25a6e58a6d6aab2adf3d9a683ad0f8b528180aee",
        "h_a7197a6ba0a25079b05d8d9c8cbc52b692c2acdcffec6c38eb13eeae5f120209",
        "h_565d18d72389b5c79149ff56cf5666c79e8414cdce6e6380668e6e1e43e823de",
        "h_f303b8e3716185acaa10327d6c0e1ef05f6fee2c2709f15e0a4dc8434776eea0",
        "h_943c0328aa393f7b8e65d1c8bc84c8153517b2469c8fa6824e1172ac55b04ed7",
        "h_3b4349c78a23a7ceefb8f943e0e44bcfbef763652179414872bc46f290acb9c8",
        "h_8bc75220773199c35dc778c011dae2f6397926d99ce06b831868ffc0f44f947e",
        "h_c26c045c574521bfeb5c5d9211d1933b936a2338b87bc46b97503beab2dd86ad",
    }
    assert {item.item_id for item in queries} == {
        "refund-allowed",
        "payment-global-allowed",
        "payment-wrong-group",
        "payment-wrong-project",
        "payment-wrong-tenant",
        "security-over-classification",
        "release-wrong-environment",
        "payment-subtree-card-only",
    }
    assert all(
        item.content_hash == "sha256:" + hashlib.sha256(item.text.encode()).hexdigest()
        for item in (*chunks, *queries)
    )


@pytest.mark.asyncio
async def test_default_task8_inputs_account_for_unique_content_without_losing_case_ids() -> None:
    chunks, queries = load_fixture_inputs(DOC_FIXTURE, QUERY_FIXTURE)
    model = FakeEmbeddingModel()

    snapshot, report = await generate_snapshot(model, chunks, queries, MemoryEmbeddingCache())

    assert len(model.calls) == 18
    assert report.cache_misses == 18
    assert report.input_tokens == 72
    assert report.calculated_cost_cny == Decimal("0.000036")
    assert len(snapshot.chunks) == 12
    assert len(snapshot.queries) == 8


@pytest.mark.asyncio
async def test_cli_runner_sends_fixed_alias_and_1536_dimensions_on_every_embedding_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        request_id = f"request-{len(requests)}"
        return httpx.Response(
            200,
            headers={"x-request-id": request_id},
            json={
                "id": request_id,
                "object": "list",
                "model": "text-embedding-v4",
                "data": [
                    {
                        "object": "embedding",
                        "index": 0,
                        "embedding": [0.001] * EMBEDDING_DIMENSION,
                    }
                ],
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            },
        )

    class RecordingAdapter(BailianEmbeddingAdapter):
        def __init__(self, config: object) -> None:
            self._recording_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            super().__init__(config, client=self._recording_client)  # type: ignore[arg-type]

        async def close(self) -> None:
            await self._recording_client.aclose()

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", RecordingAdapter)

    await research_cli._run(cli_args(), cli_settings())

    assert len(requests) == 18
    assert all(json.loads(request.content)["model"] == "text-embedding-v4" for request in requests)
    assert all(json.loads(request.content)["dimensions"] == 1536 for request in requests)
    assert all(json.loads(request.content)["encoding_format"] == "float" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "cancel", "candidate", "report"])
async def test_cli_run_revokes_completion_marker_and_never_restores_it_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    repository = tmp_path / "repository"
    report_path = repository / ".local/milvus-research/report.json"
    candidate_path = repository / ".local/milvus-research/vectors-research-embedding-v1.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text('{"old":"completion"}\n', encoding="utf-8")
    calls: list[str] = []

    class LifecycleAdapter(FakeEmbeddingModel):
        def __init__(self, _config: object) -> None:
            super().__init__()

        async def embed(self, query: str) -> Embedding:
            assert not report_path.exists()
            calls.append(query)
            if failure == "provider":
                raise RuntimeError("sanitized provider failure")
            if failure == "cancel":
                raise asyncio.CancelledError
            return await super().embed(query)

        async def close(self) -> None:
            return None

    original_snapshot_writer = research_cli.write_vector_snapshot_at
    original_report_writer = research_cli.write_research_report_at

    def candidate_writer(directory_fd: int, name: str, snapshot: object) -> None:
        original_snapshot_writer(directory_fd, name, snapshot)  # type: ignore[arg-type]
        if failure == "candidate":
            raise OSError("candidate durability fault")

    def report_writer(directory_fd: int, name: str, report: object) -> None:
        assert candidate_path.is_file()
        original_report_writer(directory_fd, name, report)  # type: ignore[arg-type]
        if failure == "report":
            raise OSError("report durability fault")

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", LifecycleAdapter)
    monkeypatch.setattr(research_cli, "write_vector_snapshot_at", candidate_writer)
    monkeypatch.setattr(research_cli, "write_research_report_at", report_writer)

    expected = asyncio.CancelledError if failure == "cancel" else (RuntimeError, OSError)
    with pytest.raises(expected):
        await research_cli._run(cli_args(), cli_settings())

    assert calls
    assert not report_path.exists()
    if failure in {"candidate", "report"}:
        assert candidate_path.is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value_kind"),
    [
        ("cache_directory", "escape"),
        ("cache_directory", "wrong-subdir"),
        ("report", "absolute-outside"),
        ("candidate_snapshot", "wrong-subdir"),
    ],
)
async def test_cli_rejects_output_paths_outside_the_exact_local_profile_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value_kind: str,
) -> None:
    repository = tmp_path / "repository"
    report_path = repository / ".local/milvus-research/report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text('{"old":"completion"}\n', encoding="utf-8")
    constructed = False

    class ForbiddenAdapter:
        def __init__(self, _config: object) -> None:
            nonlocal constructed
            constructed = True

    args = cli_args()
    if value_kind == "escape":
        value = Path("../outside")
    elif value_kind == "absolute-outside":
        value = tmp_path / "outside-report.json"
    else:
        value = Path(".local/other/output")
    setattr(args, field, value)
    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", ForbiddenAdapter)

    with pytest.raises(EmbeddingResearchRejected, match="output path"):
        await research_cli._run(args, cli_settings())

    assert not constructed
    assert report_path.read_text(encoding="utf-8") == '{"old":"completion"}\n'
    assert not (tmp_path / "outside-report.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "symlink_kind",
    ["local-parent", "research-parent", "cache", "report", "candidate"],
)
async def test_cli_rejects_symlinked_output_path_components_before_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symlink_kind: str,
) -> None:
    repository = tmp_path / "repository"
    local = repository / ".local"
    outside = tmp_path / "outside"
    outside.mkdir()
    if symlink_kind == "local-parent":
        repository.mkdir()
        local.symlink_to(outside, target_is_directory=True)
    else:
        local.mkdir(parents=True)
        research = local / "milvus-research"
        if symlink_kind == "research-parent":
            research.symlink_to(outside, target_is_directory=True)
        else:
            research.mkdir()
            target = outside / "target"
            if symlink_kind == "cache":
                target.mkdir()
                (local / "milvus-embedding-cache").symlink_to(target, target_is_directory=True)
            elif symlink_kind == "report":
                target.write_text("outside", encoding="utf-8")
                (research / "report.json").symlink_to(target)
            else:
                target.write_text("outside", encoding="utf-8")
                (research / "vectors-research-embedding-v1.json").symlink_to(target)
    constructed = False

    class ForbiddenAdapter:
        def __init__(self, _config: object) -> None:
            nonlocal constructed
            constructed = True

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", ForbiddenAdapter)

    with pytest.raises(EmbeddingResearchRejected, match="output path"):
        await research_cli._run(cli_args(), cli_settings())

    assert not constructed


@pytest.mark.asyncio
@pytest.mark.parametrize("report_fault", [False, True])
async def test_cli_held_directory_fds_prevent_rename_symlink_output_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_fault: bool,
) -> None:
    repository = tmp_path / "repository"
    local = repository / ".local"
    cache = local / "milvus-embedding-cache"
    research = local / "milvus-research"
    outside_cache = tmp_path / "outside-cache"
    outside_research = tmp_path / "outside-research"
    cache.mkdir(parents=True)
    research.mkdir()
    outside_cache.mkdir()
    outside_research.mkdir()
    (research / "report.json").write_text("old completion\n", encoding="utf-8")
    switched = False

    class SwitchingAdapter(FakeEmbeddingModel):
        def __init__(self, _config: object) -> None:
            super().__init__()

        async def embed(self, query: str) -> Embedding:
            nonlocal switched
            if not switched:
                switched = True
                cache.rename(local / "held-cache")
                cache.symlink_to(outside_cache, target_is_directory=True)
                research.rename(local / "held-research")
                research.symlink_to(outside_research, target_is_directory=True)
            return await super().embed(query)

        async def close(self) -> None:
            return None

    original_report_writer = getattr(research_cli, "write_research_report_at", None)

    def fail_after_report_replace(
        directory_fd: int,
        name: str,
        report: object,
    ) -> None:
        assert original_report_writer is not None
        original_report_writer(directory_fd, name, report)
        raise OSError("post-replace report fault")

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", SwitchingAdapter)
    if report_fault:
        monkeypatch.setattr(
            research_cli,
            "write_research_report_at",
            fail_after_report_replace,
            raising=False,
        )

    if report_fault:
        with pytest.raises(OSError, match="report fault"):
            await research_cli._run(cli_args(), cli_settings())
    else:
        await research_cli._run(cli_args(), cli_settings())

    assert list(outside_cache.iterdir()) == []
    assert list(outside_research.iterdir()) == []
    assert len(list((local / "held-cache").glob("h_*.json"))) == 18
    held_research = local / "held-research"
    assert (held_research / "vectors-research-embedding-v1.json").is_file()
    assert (held_research / "report.json").is_file() is not report_fault
    assert list(held_research.glob(".*.tmp")) == []


@pytest.mark.asyncio
async def test_cli_closes_every_held_directory_fd_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    opened_directory_fds: list[int] = []
    active_directory_fds: set[int] = set()
    real_open = os.open
    real_close = os.close

    def tracked_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if flags & getattr(os, "O_DIRECTORY", 0):
            opened_directory_fds.append(descriptor)
            active_directory_fds.add(descriptor)
        return descriptor

    def tracked_close(descriptor: int) -> None:
        active_directory_fds.discard(descriptor)
        real_close(descriptor)

    class CancellingAdapter(FakeEmbeddingModel):
        def __init__(self, _config: object) -> None:
            super().__init__()

        async def embed(self, query: str) -> Embedding:
            raise asyncio.CancelledError

        async def close(self) -> None:
            return None

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", CancellingAdapter)
    monkeypatch.setattr(research_cli.os, "open", tracked_open)
    monkeypatch.setattr(research_cli.os, "close", tracked_close)
    monkeypatch.setattr(research_cli, "_dirfd_capabilities_available", lambda: True)

    task = asyncio.create_task(research_cli._run(cli_args(), cli_settings()))
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert len(opened_directory_fds) >= 4
    assert active_directory_fds == set()


@pytest.mark.asyncio
async def test_cli_fails_before_marker_or_provider_when_dirfd_api_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    report = repository / ".local/milvus-research/report.json"
    report.parent.mkdir(parents=True)
    report.write_text("old completion\n", encoding="utf-8")
    constructed = False

    class ForbiddenAdapter:
        def __init__(self, _config: object) -> None:
            nonlocal constructed
            constructed = True

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", ForbiddenAdapter)
    monkeypatch.setattr(
        research_cli,
        "_dirfd_capabilities_available",
        lambda: False,
        raising=False,
    )

    with pytest.raises(EmbeddingResearchRejected, match="directory capability"):
        await research_cli._run(cli_args(), cli_settings())

    assert not constructed
    assert report.read_text(encoding="utf-8") == "old completion\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_kind", ["ordinary", "cancel"])
async def test_cli_cleanup_failure_preserves_primary_exception_and_records_incomplete_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_kind: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    primary: BaseException = (
        asyncio.CancelledError("primary cancellation")
        if primary_kind == "cancel"
        else RuntimeError("primary provider failure")
    )
    remove_calls = 0

    class FailingAdapter(FakeEmbeddingModel):
        def __init__(self, _config: object) -> None:
            super().__init__()

        async def embed(self, query: str) -> Embedding:
            raise primary

        async def close(self) -> None:
            return None

    def fail_repeated_cleanup(_directory_fd: int, _name: str) -> None:
        nonlocal remove_calls
        remove_calls += 1
        if remove_calls > 1:
            raise OSError("PRIVATE cleanup sink detail")

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", FailingAdapter)
    monkeypatch.setattr(
        research_cli,
        "_remove_completion_marker_at",
        fail_repeated_cleanup,
        raising=False,
    )

    task = asyncio.create_task(research_cli._run(cli_args(), cli_settings()))
    with pytest.raises(type(primary)) as caught:
        await task

    assert caught.value is primary
    assert task.cancelled() is (primary_kind == "cancel")
    assert remove_calls == 3
    assert getattr(primary, "__notes__", []) == [
        "embedding research completion marker cleanup was incomplete"
    ]
    assert isinstance(primary.__cause__, EmbeddingResearchRejected)
    assert str(primary.__cause__) == "embedding research completion marker cleanup was incomplete"
    assert "PRIVATE" not in " ".join(getattr(primary, "__notes__", []))


@pytest.mark.asyncio
async def test_cli_initial_marker_revoke_failure_stops_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    constructed = False

    class ForbiddenAdapter:
        def __init__(self, _config: object) -> None:
            nonlocal constructed
            constructed = True

    def fail_revoke(_directory_fd: int, _name: str) -> None:
        raise OSError("PRIVATE marker detail")

    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", ForbiddenAdapter)
    monkeypatch.setattr(
        research_cli,
        "_remove_completion_marker_at",
        fail_revoke,
        raising=False,
    )

    with pytest.raises(EmbeddingResearchRejected, match="could not be revoked") as caught:
        await research_cli._run(cli_args(), cli_settings())

    assert not constructed
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("flag", [None, "", "true", "yes", "0", "2"])
def test_cli_requires_the_exact_paid_research_flag_before_running(flag: str | None) -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import scripts.milvus_embedding_research as cli
async def forbidden(*args, **kwargs):
    raise AssertionError('paid runner was called')
cli._run = forbidden
raise SystemExit(cli.main([]))
"""
    environment = dict(os.environ)
    if flag is None:
        environment.pop("TAP_RUN_PAID_EMBEDDING_RESEARCH", None)
    else:
        environment["TAP_RUN_PAID_EMBEDDING_RESEARCH"] = flag
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "Paid embedding research requires explicit opt-in.\n"


def test_cli_sanitizes_provider_failure_and_cancellation() -> None:
    repository = Path(__file__).resolve().parents[5]
    for exception in ("RuntimeError", "asyncio.CancelledError"):
        program = f"""
import asyncio
import scripts.milvus_embedding_research as cli
async def fail(*args, **kwargs):
    raise {exception}('PRIVATE_TEXT PRIVATE_VECTOR PRIVATE_SECRET')
cli._run = fail
raise SystemExit(cli.main([]))
"""
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=repository,
            env={**os.environ, "TAP_RUN_PAID_EMBEDDING_RESEARCH": "1"},
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 1
        assert completed.stdout == ""
        assert completed.stderr == "Embedding research failed.\n"


def test_cli_builds_only_the_fixed_direct_route_and_hides_provider_config() -> None:
    settings = {
        "LITELLM_BASE_URL": "http://127.0.0.1:4000",
        "LITELLM_MASTER_KEY": "PRIVATE_GATEWAY_SECRET",
        "LITELLM_EMBEDDING_MODEL": "text-embedding-v4",
        "LITELLM_EMBEDDING_API_KEY": "PRIVATE_PROVIDER_SECRET",
        "LITELLM_EMBEDDING_API_BASE": (
            "https://ws-privateworkspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        ),
    }
    config = research_bailian_config(settings)

    assert config.deadline_seconds == 15.0
    assert "PRIVATE_GATEWAY_SECRET" not in repr(config)
    assert "PRIVATE_PROVIDER_SECRET" not in repr(config)
    assert "ws-privateworkspace.cn-beijing.maas.aliyuncs.com" not in repr(config)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LITELLM_EMBEDDING_API_BASE", ""),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "http://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://user@ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/v1",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/extra",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1?key=PRIVATE_QUERY_SECRET",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1#PRIVATE_FRAGMENT_SECRET",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://evil.example/compatible-mode/v1",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://ws-abcdefghijklmnop.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "LITELLM_EMBEDDING_API_BASE",
            "https://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com:443/compatible-mode/v1",
        ),
        ("LITELLM_EMBEDDING_API_KEY", ""),
        ("LITELLM_EMBEDDING_MODEL", "openai/text-embedding-v4"),
    ],
    ids=(
        "missing-base",
        "http",
        "userinfo",
        "wrong-path",
        "extra-path",
        "query",
        "fragment",
        "wrong-provider-host",
        "wrong-region",
        "explicit-port",
        "missing-key",
        "provider-prefix-must-not-be-in-model",
    ),
)
def test_research_provider_route_rejects_incomplete_or_widened_settings_without_leak(
    name: str,
    value: str,
) -> None:
    settings = cli_settings()
    settings[name] = value

    with pytest.raises(EmbeddingResearchRejected) as caught:
        research_bailian_config(settings)

    message = str(caught.value)
    assert "aliyuncs.com" not in message
    assert "PRIVATE_" not in message
    assert "sanitized-provider-key" not in message


@pytest.mark.asyncio
async def test_invalid_provider_route_fails_before_marker_removal_or_adapter_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    report_path = repository / ".local/milvus-research/report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text('{"old":"completion"}\n', encoding="utf-8")
    constructed = False

    class ForbiddenAdapter:
        def __init__(self, _config: object) -> None:
            nonlocal constructed
            constructed = True

    settings = cli_settings()
    settings["LITELLM_EMBEDDING_API_BASE"] = (
        "http://ws-abcdefghijklmnop.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setattr(research_cli, "_REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(research_cli, "BailianEmbeddingAdapter", ForbiddenAdapter)

    with pytest.raises(EmbeddingResearchRejected):
        await research_cli._run(cli_args(), settings)

    assert not constructed
    assert report_path.read_text(encoding="utf-8") == '{"old":"completion"}\n'


def test_embedding_provider_config_is_fixed_and_secrets_remain_empty_placeholders() -> None:
    repository = Path(__file__).resolve().parents[5]
    environment = {
        key: value
        for line in (repository / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, separator, value in (line.partition("="),)
        if separator
    }

    assert environment["LITELLM_BASE_URL"] == "http://127.0.0.1:4000"
    assert environment["LITELLM_IMAGE"] == "ghcr.io/berriai/litellm:v1.87.0"
    assert environment["LITELLM_ATHENA_EMBEDDING_MODEL"] == ("dashscope/text-embedding-v4")
    assert environment["LITELLM_EMBEDDING_MODEL"] == "text-embedding-v4"
    assert environment["LITELLM_EMBEDDING_API_KEY"] == ""
    assert environment["LITELLM_EMBEDDING_API_BASE"] == ""
    assert environment["DASHSCOPE_API_KEY"] == ""

    compose = yaml.safe_load((repository / "compose.yaml").read_text(encoding="utf-8"))
    gateway = yaml.safe_load(
        (repository / "deploy/local/litellm/config.yaml").read_text(encoding="utf-8")
    )
    compose_environment = compose["services"]["litellm"]["environment"]
    assert compose["services"]["litellm"]["image"] == (
        "${LITELLM_IMAGE:-ghcr.io/berriai/litellm:v1.87.0}"
    )
    assert compose_environment["LITELLM_ATHENA_EMBEDDING_MODEL"] == (
        "${LITELLM_ATHENA_EMBEDDING_MODEL:-dashscope/text-embedding-v4}"
    )
    assert not any(key.startswith("LITELLM_EMBEDDING_") for key in compose_environment)
    assert compose_environment["DASHSCOPE_API_KEY"] == "${DASHSCOPE_API_KEY:-}"
    embedding_route = next(
        item for item in gateway["model_list"] if item["model_name"] == "athena-embedding"
    )
    assert embedding_route["litellm_params"] == {
        "model": "os.environ/LITELLM_ATHENA_EMBEDDING_MODEL",
        "api_key": "os.environ/DASHSCOPE_API_KEY",
    }
    assert "research-embedding-v1" not in str(gateway)


def test_make_paid_target_fails_explicitly_instead_of_skipping() -> None:
    repository = Path(__file__).resolve().parents[5]
    environment = dict(os.environ)
    environment.pop("TAP_RUN_PAID_EMBEDDING_RESEARCH", None)
    completed = subprocess.run(
        ["make", "research-embeddings"],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "requires TAP_RUN_PAID_EMBEDDING_RESEARCH=1" in completed.stderr
    assert "No rule to make target" not in completed.stderr
