from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest

from tap.modules.knowledge.ports.models import Embedding, EmbeddingUsage
from tap.operations.milvus import embeddings as embedding_module
from tap.operations.milvus.embeddings import (
    EMBEDDING_ALIAS,
    EMBEDDING_DIMENSION,
    EmbeddingInput,
    EmbeddingResearchRejected,
    FileEmbeddingCache,
    embedding_cache_key,
    generate_snapshot,
    load_fixture_inputs,
    research_litellm_config,
    write_research_report,
    write_vector_snapshot,
)
from tap.operations.milvus.fixtures import content_hash as fixture_content_hash

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "milvus"
DOC_FIXTURE = FIXTURES / "doc-fixture-v1.json"
QUERY_FIXTURE = FIXTURES / "query-cases-v1.json"


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
        cost: Decimal = Decimal("0.000001"),
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
                response_cost_usd=self.cost,
            ),
        )


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
    assert first_report.response_cost_usd == Decimal("0.000001")
    assert second_report.cache_hits == 1
    assert second_report.cache_misses == 0
    assert cache.get_count == 2


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
    assert second_report.response_cost_usd == Decimal("0")
    assert second_report.provider_request_ids == ()
    assert set(asdict(second_report)) == {
        "model_id",
        "dimension",
        "chunk_count",
        "query_count",
        "cache_hits",
        "cache_misses",
        "input_tokens",
        "response_cost_usd",
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
async def test_usage_and_aggregate_decimal_cost_are_required_and_bounded() -> None:
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
    with pytest.raises(EmbeddingResearchRejected, match="aggregate cost"):
        await generate_snapshot(
            expensive,
            (research_input("chunk-1"), research_input("chunk-2")),
            (),
            MemoryEmbeddingCache(),
        )


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
        "responseCostUsd",
        "startedAt",
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
        {"response_cost_usd": Decimal("NaN")},
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
        response_cost_usd=Decimal("0.000001"),
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
    assert len(snapshot.chunks) == 12
    assert len(snapshot.queries) == 8


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


def test_cli_builds_only_the_fixed_alias_and_hides_gateway_secret_from_repr() -> None:
    settings = {
        "LITELLM_BASE_URL": "http://127.0.0.1:4000",
        "LITELLM_MASTER_KEY": "PRIVATE_GATEWAY_SECRET",
        "LITELLM_EMBEDDING_MODEL": "provider/research-embed-1536",
    }
    config = research_litellm_config(settings)

    assert config.embedding_model_id == EMBEDDING_ALIAS
    assert config.embedding_dimension == EMBEDDING_DIMENSION
    assert config.allowed_embedding_model_labels == frozenset(
        {EMBEDDING_ALIAS, "provider/research-embed-1536"}
    )
    assert "PRIVATE_GATEWAY_SECRET" not in repr(config)


def test_env_example_contains_only_empty_embedding_provider_placeholders() -> None:
    repository = Path(__file__).resolve().parents[5]
    environment = {
        key: value
        for line in (repository / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, separator, value in (line.partition("="),)
        if separator
    }

    assert environment["LITELLM_BASE_URL"] == "http://127.0.0.1:4000"
    assert environment["LITELLM_EMBEDDING_MODEL"] == ""
    assert environment["LITELLM_EMBEDDING_API_KEY"] == ""


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
