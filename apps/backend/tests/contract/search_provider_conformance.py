from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from test_milvus_filter import _execution_with_resources, _resource, doc_execution
from test_milvus_mapping import valid_doc_row
from test_milvus_search_strict import RecordingAuditSink, RecordingReader, config

from tap.modules.knowledge.adapters.azure_ai_search import (
    AzureAISearchAdapter,
    AzureIndexTarget,
    AzureSearchConfig,
)
from tap.modules.knowledge.adapters.milvus.filter import compile_milvus_filter
from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.domain.models import FilterableSubtree, SourceFamily
from tap.modules.knowledge.ports.errors import SearchUnavailable
from tap.modules.knowledge.ports.models import SearchExecution, SearchHit


@dataclass(frozen=True, slots=True)
class ConformanceResult:
    channels: tuple[str, ...]
    outbound_filters: tuple[str, ...]
    expected_filter: str
    provider_rows: tuple[Mapping[str, object], ...]
    hits: tuple[SearchHit, ...]


class SearchProviderConformanceHarness(Protocol):
    provider_name: Literal["azure", "milvus"]

    async def run_case(self, case_id: str) -> ConformanceResult: ...


def _execution(case_id: str) -> SearchExecution:
    if case_id == "resource-scope":
        return _execution_with_resources(
            (
                _resource(
                    "blob:handbook/payment-policy",
                    subtree=FilterableSubtree(root_ids=("h_" + "3" * 64,)),
                ),
            )
        )
    return doc_execution()


def _provider_rows(case_id: str) -> tuple[Mapping[str, object], ...]:
    if case_id == "allowed":
        return (valid_doc_row(),)
    if case_id in {
        "denied-group",
        "wrong-tenant",
        "wrong-project",
        "over-classification",
        "wrong-environment",
        "wrong-corpus",
        "resource-scope",
    }:
        return ()
    if case_id == "unavailable":
        raise SearchUnavailable("controlled provider outage")
    raise ValueError("unknown conformance case")


def _azure_row(row: Mapping[str, object]) -> dict[str, Any]:
    return {
        "@search.score": row["score"],
        "indexFamily": row["index_family"],
        "chunkId": row["chunk_id"],
        "logicalChunkId": row["logical_chunk_id"],
        "rootId": row["root_id"],
        "parentId": row["parent_id"],
        "title": row["title"],
        "content": row["content"],
        "sourceId": row["source_id"],
        "sourceType": row["source_type"],
        "sourceRevision": row["source_revision"],
        "anchorJson": row["anchor_json"],
        "sourceContentHash": row["source_content_hash"],
        "chunkContentHash": row["chunk_content_hash"],
        "contentRole": row["content_role"],
        "derivedFromChunkIds": row["derived_from_chunk_ids"],
        "corpusVersion": row["corpus_version"],
        "schemaVersion": row["schema_version"],
        "embeddingModelVersion": row["embedding_model_version"],
    }


class _AzureResults:
    def __init__(self, rows: tuple[Mapping[str, object], ...]) -> None:
        self._rows = rows
        self.request_id = "azure-conformance-request"
        self.partial = False

    def __aiter__(self):
        async def iterate():
            for row in self._rows:
                yield row

        return iterate()


class _AzureClient:
    def __init__(self, rows: tuple[Mapping[str, object], ...], *, unavailable: bool) -> None:
        self.rows = rows
        self.unavailable = unavailable
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> _AzureResults:
        self.calls.append(kwargs)
        if self.unavailable:
            raise SearchUnavailable("controlled provider outage")
        return _AzureResults(tuple(_azure_row(row) for row in self.rows))

    async def close(self) -> None:
        return None


class AzureConformanceHarness:
    provider_name: Literal["azure"] = "azure"

    async def run_case(self, case_id: str) -> ConformanceResult:
        execution = _execution(case_id)
        unavailable = case_id == "unavailable"
        provider_rows = () if unavailable else _provider_rows(case_id)
        client = _AzureClient(provider_rows, unavailable=unavailable)
        target = AzureIndexTarget(
            query_index="kb-doc-read",
            physical_index="kb_doc_v1_corpus_fixture_v1",
            schema_version="doc-schema-v1",
            embedding_model_id="research-embedding-v1",
            vector_dimension=1536,
            allowed_source_types=frozenset({"doc"}),
        )
        adapter = AzureAISearchAdapter(
            AzureSearchConfig(
                endpoint="https://search.example",
                indexes={SourceFamily.DOC: target},
                query_api_key="not-a-real-key",
                allow_query_key_auth=True,
                max_retries=0,
                per_index_candidates=50,
            ),
            client_factory=lambda _index: client,
        )
        hits = await adapter.search(execution)
        outbound_filter = client.calls[0]["filter"]
        assert isinstance(outbound_filter, str)
        return ConformanceResult(
            channels=("bm25", "dense"),
            outbound_filters=(outbound_filter, outbound_filter),
            expected_filter=expected_azure_filter(execution),
            provider_rows=provider_rows,
            hits=hits,
        )


class MilvusConformanceHarness:
    provider_name: Literal["milvus"] = "milvus"

    async def run_case(self, case_id: str) -> ConformanceResult:
        execution = _execution(case_id)
        unavailable = case_id == "unavailable"
        provider_rows = () if unavailable else _provider_rows(case_id)
        reader = RecordingReader(
            provider_rows,
            failure=SearchUnavailable("controlled provider outage") if unavailable else None,
        )
        adapter = MilvusSearchAdapter(config(), reader, RecordingAuditSink())
        hits = await adapter.search(execution)
        request = reader.requests[0]
        return ConformanceResult(
            channels=tuple(channel.kind for channel in request.channels),
            outbound_filters=tuple(channel.filter_expression for channel in request.channels),
            expected_filter=compile_milvus_filter(
                execution,
                SourceFamily.DOC,
                max_bytes=32_768,
            ),
            provider_rows=provider_rows,
            hits=hits,
        )


def expected_azure_filter(execution: SearchExecution) -> str:
    policy = execution.policy
    plan = execution.plan
    classification_values = tuple(
        value
        for value in ("public", "internal", "confidential", "restricted")
        if any(item.value == value for item in policy.allowed_classifications)
    )
    environment_values = (
        ("global",)
        if plan.effective_environment is None
        else ("global", plan.effective_environment)
    )

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def search_in(values: tuple[str, ...]) -> str:
        return literal("|".join(value.replace("|", "||") for value in values))

    clauses = [
        f"tenantId eq {literal(policy.tenant_id)}",
        f"projectId eq {literal(policy.project_id)}",
        "allowedGroupIds/any(g: search.in(g, "
        f"{search_in(tuple(sorted(policy.actor.allowed_group_ids)))}, '|'))",
        f"search.in(classification, {search_in(classification_values)}, '|')",
        f"search.in(environment, {search_in(environment_values)}, '|')",
        f"corpusVersion eq {literal(plan.corpus_version)}",
    ]
    scoped = tuple(
        resource
        for resource in plan.resources
        if resource.family is SourceFamily.DOC and resource.mode.value == "scope"
    )
    if scoped:
        resource_clauses = []
        for resource in scoped:
            parts = [
                f"sourceId eq {literal(resource.source_id)}",
                f"sourceRevision eq {literal(resource.revision)}",
                f"sourceContentHash eq {literal(resource.source_content_hash)}",
            ]
            if resource.subtree is not None:
                locators = [
                    *(f"rootId eq {literal(value)}" for value in resource.subtree.root_ids),
                    *(f"parentId eq {literal(value)}" for value in resource.subtree.parent_ids),
                    *(
                        f"logicalChunkId eq {literal(value)}"
                        for value in resource.subtree.logical_chunk_ids
                    ),
                ]
                parts.append("(" + " or ".join(locators) + ")")
            resource_clauses.append("(" + " and ".join(parts) + ")")
        clauses.append("(" + " or ".join(resource_clauses) + ")")
    return " and ".join(clauses)


def azure_harness() -> SearchProviderConformanceHarness:
    return AzureConformanceHarness()


def milvus_harness() -> SearchProviderConformanceHarness:
    return MilvusConformanceHarness()
