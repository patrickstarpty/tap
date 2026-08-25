"""Strict Azure Search execution, provenance, and resource-bound contracts."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from typing import Any

import httpx
import pytest
from search_provider_conformance import expected_azure_filter

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    Classification,
    ProjectPolicy,
    ResourceGrant,
    ResourceSubtreeGrant,
    RetrievalPolicyContext,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.adapters.azure_ai_search import (
    AzureAISearchAdapter,
    AzureIndexTarget,
    AzureSearchConfig,
)
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    CodeAnchor,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    FilterableSubtree,
    QueryPlan,
    ResolvedResourceRef,
    ResourceMode,
    RetrievalProfileId,
    RevisionKind,
    SourceFamily,
)
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable
from tap.modules.knowledge.ports.models import SearchExecution

CANONICAL_HASH = "sha256:" + "a" * 64
SOURCE_HASH = "sha256:" + "b" * 64
CHUNK_HASH = "sha256:" + "c" * 64
SANITIZED_HASH = "sha256:df53c004039afb4e356e07ed30b12b466e3a5f1067ed623d7d76d99101e0894e"
ANCHOR = CodeAnchor(
    repo="checkout",
    path="payment.py",
    symbol="authorize",
    line_start=10,
    line_end=25,
)
SUBTREE = FilterableSubtree(
    root_ids=("root-payment",),
    parent_ids=("parent-authorize",),
    logical_chunk_ids=("h_" + "9" * 64,),
)
SOURCE_TYPES = {
    SourceFamily.DOC: frozenset({"document", "openapi"}),
    SourceFamily.CODE: frozenset({"code", "code_summary"}),
    SourceFamily.BDD: frozenset({"bdd"}),
    SourceFamily.FAILURE: frozenset({"failure"}),
}


def current_policy(*, with_resource: bool = False) -> RetrievalPolicyContext:
    groups = frozenset({"group-one"})
    grants = ()
    if with_resource:
        grants = (
            ResourceGrant(
                family="code",
                source_id="repo:checkout:payment.py",
                revision_kind="git_commit",
                revision="a" * 40,
                source_content_hash=SOURCE_HASH,
                allowed_anchor_keys=frozenset({"code:checkout:payment.py:authorize:10:25"}),
                subtree_grants=(
                    ResourceSubtreeGrant(
                        anchor_key="code:checkout:payment.py:authorize:10:25",
                        root_ids=SUBTREE.root_ids,
                        parent_ids=SUBTREE.parent_ids,
                        logical_chunk_ids=SUBTREE.logical_chunk_ids,
                    ),
                ),
            ),
        )
    subject = VerifiedSubjectFacts(
        tenant_id="tenant-a",
        user_id="user-1",
        group_ids=groups,
        roles=frozenset({"reader"}),
        token_verified=True,
    )
    policy = ProjectPolicy(
        tenant_id="tenant-a",
        project_id="project-a",
        permission_granted=True,
        allowed_group_ids=groups,
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=frozenset({"production"}),
        allowed_source_families=frozenset({"code", "doc"}),
        active_corpus_version="corpus-17",
        acl_digest="sha256:acl-17",
        policy_version="policy-17",
        decision_id="decision-17",
        resource_grants=grants,
    )
    return build_retrieval_policy_context(
        subject,
        policy,
        requested_tenant_id="tenant-a",
        requested_project_id="project-a",
    )


def plan(
    *,
    families: tuple[SourceFamily, ...] = (SourceFamily.CODE,),
    resources: tuple[ResolvedResourceRef, ...] = (),
    embedding_model_id: str = "tap-embed-fixed-v1",
    embedding_dimension: int = 2,
) -> QueryPlan:
    return QueryPlan(
        query_plan_id="plan-17",
        operation_id="operation-17",
        tenant_id="tenant-a",
        project_id="project-a",
        policy_decision_id="decision-17",
        policy_version="policy-17",
        acl_digest="sha256:acl-17",
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=families,
        resources=resources,
        effective_environment="production",
        corpus_version="corpus-17",
        candidate_limit=10,
        raw_request_hash=CANONICAL_HASH,
        sanitized_query="authorization [REDACTED]",
        sanitized_query_hash=SANITIZED_HASH,
        redaction_version="redaction-v3",
        embedding_model_id=embedding_model_id,
        embedding_dimension=embedding_dimension,
    )


def snapshot(*, content_hash: str = SANITIZED_HASH, **changes: str) -> ContextSnapshot:
    values = {
        "context_snapshot_id": "snapshot-17",
        "operation_id": "operation-17",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "policy_decision_id": "decision-17",
        "policy_version": "policy-17",
        "acl_digest": "sha256:acl-17",
    }
    values.update(changes)
    return ContextSnapshot(
        **values,
        layers=(
            ContextLayer(
                kind=ContextLayerKind.CURRENT_TURN,
                ref_ids=(),
                content_hash=content_hash,
                token_count=2,
            ),
        ),
    )


def execution(
    *,
    policy: RetrievalPolicyContext | None = None,
    query_plan: QueryPlan | None = None,
    context_snapshot: ContextSnapshot | None = None,
) -> SearchExecution:
    return SearchExecution(
        policy=policy or current_policy(),
        plan=query_plan or plan(),
        context_snapshot=context_snapshot or snapshot(),
        query_vector=(0.25, 0.5),
    )


def target(family: SourceFamily = SourceFamily.CODE) -> AzureIndexTarget:
    return AzureIndexTarget(
        query_index=f"kb-{family.value}-read",
        physical_index=f"kb-{family.value}-v1-20260824",
        schema_version="search-schema-v1",
        embedding_model_id="tap-embed-fixed-v1",
        vector_dimension=2,
        allowed_source_types=SOURCE_TYPES[family],
    )


def config(
    *,
    indexes: dict[SourceFamily, AzureIndexTarget] | None = None,
    **bounds: object,
) -> AzureSearchConfig:
    values: dict[str, object] = {
        "endpoint": "https://search.example",
        "indexes": indexes or {SourceFamily.CODE: target()},
        "query_api_key": "not-a-real-key",
        "allow_query_key_auth": True,
        "max_fan_out": 2,
        "per_index_candidates": 10,
        "max_connections": 2,
        "deadline_seconds": 1,
        "max_retries": 0,
        "max_rows": 10,
        "max_request_bytes": 32_768,
        "max_response_bytes": 65_536,
        "max_content_chars": 2_000,
        "max_derived_ids": 8,
    }
    values.update(bounds)
    return AzureSearchConfig(**values)  # type: ignore[arg-type]


class FakeResults:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        partial: bool = False,
        delay: float = 0,
    ) -> None:
        self.rows = rows
        self.partial = partial
        self.request_id = "azure-request-17"
        self.delay = delay

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield row

        return iterate()


class FakeClient:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, partial: bool = False) -> None:
        self.rows = rows or []
        self.partial = partial
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> FakeResults:
        self.calls.append(kwargs)
        return FakeResults(self.rows, partial=self.partial)

    async def close(self) -> None:
        return None


def valid_row() -> dict[str, Any]:
    return {
        "@search.score": 0.91,
        "indexFamily": "code",
        "chunkId": "h_" + "1" * 64,
        "logicalChunkId": "h_" + "9" * 64,
        "rootId": "root-payment",
        "parentId": "parent-authorize",
        "title": "authorize",
        "content": "Authorization requires current policy.",
        "sourceId": "repo:checkout:payment.py",
        "sourceType": "code",
        "sourceRevision": "a" * 40,
        "anchorJson": json.dumps(
            {
                "type": "code",
                "repo": "checkout",
                "path": "payment.py",
                "symbol": "authorize",
                "lineStart": 10,
                "lineEnd": 25,
            }
        ),
        "sourceContentHash": SOURCE_HASH,
        "chunkContentHash": CHUNK_HASH,
        "contentRole": "source",
        "derivedFromChunkIds": [],
        "corpusVersion": "corpus-17",
        "schemaVersion": "search-schema-v1",
        "embeddingModelVersion": "tap-embed-fixed-v1",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_execution",
    [
        execution(query_plan=replace(plan(), embedding_model_id="unknown-embed-v9")),
        execution(context_snapshot=snapshot(operation_id="operation-18")),
        execution(context_snapshot=snapshot(policy_decision_id="decision-18")),
        execution(context_snapshot=snapshot(content_hash=CANONICAL_HASH)),
    ],
    ids=("vector-model", "operation", "policy-decision", "context-lineage"),
)
async def test_plan_snapshot_and_vector_space_mismatch_fail_before_query(
    bad_execution: SearchExecution,
) -> None:
    """Ignoring any binding would call the selected Azure index."""
    client = FakeClient([valid_row()])
    adapter = AzureAISearchAdapter(config(), client_factory=lambda _index: client)

    with pytest.raises(SearchUnavailable):
        await adapter.search(bad_execution)

    assert client.calls == []


@pytest.mark.asyncio
async def test_direct_execution_rejects_forged_scope_and_uncovered_family() -> None:
    """Trusting caller-built resolved resources would widen direct port execution."""
    forged = ResolvedResourceRef(
        family=SourceFamily.CODE,
        source_id="repo:checkout:payment.py",
        mode=ResourceMode.SCOPE,
        revision_kind=RevisionKind.GIT_COMMIT,
        revision="a" * 40,
        source_content_hash=SOURCE_HASH,
        anchor=ANCHOR,
        subtree=SUBTREE,
    )
    clients = {SourceFamily.CODE: FakeClient(), SourceFamily.DOC: FakeClient()}
    adapter = AzureAISearchAdapter(
        config(
            indexes={
                SourceFamily.CODE: target(),
                SourceFamily.DOC: target(SourceFamily.DOC),
            }
        ),
        client_factory=lambda index: clients[
            SourceFamily.CODE if index == "kb-code-read" else SourceFamily.DOC
        ],
    )

    with pytest.raises(SearchUnavailable):
        await adapter.search(
            execution(
                policy=current_policy(with_resource=False),
                query_plan=plan(resources=(forged,)),
            )
        )

    with pytest.raises(SearchUnavailable):
        await adapter.search(
            execution(
                policy=current_policy(with_resource=True),
                query_plan=plan(
                    families=(SourceFamily.CODE, SourceFamily.DOC),
                    resources=(forged,),
                ),
            )
        )

    assert all(client.calls == [] for client in clients.values())


@pytest.mark.asyncio
async def test_scope_prefilter_contains_hash_and_filterable_subtree_before_query() -> None:
    """Source/revision-only filtering would query sibling content too broadly."""
    client = FakeClient()
    adapter = AzureAISearchAdapter(config(), client_factory=lambda _index: client)
    scoped = ResolvedResourceRef(
        family=SourceFamily.CODE,
        source_id="repo:checkout:payment.py",
        mode=ResourceMode.SCOPE,
        revision_kind=RevisionKind.GIT_COMMIT,
        revision="a" * 40,
        source_content_hash=SOURCE_HASH,
        anchor=ANCHOR,
        subtree=SUBTREE,
    )

    await adapter.search(
        execution(
            policy=current_policy(with_resource=True),
            query_plan=plan(resources=(scoped,)),
        )
    )

    emitted = client.calls[0]["filter"]
    assert f"sourceContentHash eq '{SOURCE_HASH}'" in emitted
    assert "rootId eq 'root-payment'" in emitted
    assert "parentId eq 'parent-authorize'" in emitted
    assert (
        "logicalChunkId eq 'h_9999999999999999999999999999999999999999999999999999999999999999'"
        in emitted
    )
    assert client.calls[0]["search_text"] == "authorization [REDACTED]"


@pytest.mark.asyncio
async def test_hybrid_request_uses_the_hand_derived_security_filter_for_both_channels() -> None:
    """Changing the single prefilter would let keyword and dense retrieval diverge."""
    client = FakeClient([])
    bound_execution = execution()
    adapter = AzureAISearchAdapter(config(), client_factory=lambda _index: client)

    await adapter.search(bound_execution)

    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["search_text"] == bound_execution.plan.sanitized_query
    assert request["vector_queries"]
    assert request["vector_filter_mode"] == "preFilter"
    assert request["filter"] == expected_azure_filter(bound_execution)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunkId", None),
        ("chunkId", "not-a-chunk-key"),
        ("logicalChunkId", ""),
        ("sourceContentHash", None),
        ("sourceContentHash", "sha256:" + "g" * 64),
        ("chunkContentHash", ""),
        ("chunkContentHash", "sha256:" + "A" * 64),
        ("sourceRevision", None),
        ("sourceRevision", "G" * 40),
        ("indexFamily", "doc"),
        ("corpusVersion", "corpus-old"),
        ("schemaVersion", "search-schema-old"),
        ("embeddingModelVersion", "unknown-embed-v9"),
        ("anchorJson", "{malformed"),
        (
            "anchorJson",
            json.dumps(
                {
                    "type": "code",
                    "repo": "checkout",
                    "path": "payment.py",
                    "symbol": "authorize",
                    "lineStart": 10,
                    "lineEnd": 25,
                    "unexpected": "untrusted provenance",
                }
            ),
        ),
        ("@search.score", float("nan")),
        ("derivedFromChunkIds", ["h_" + "2" * 64] * 9),
        ("content", ""),
        ("content", "x" * 2_001),
    ],
)
async def test_any_malformed_or_mismatched_row_rejects_the_selected_page(
    field: str,
    value: object,
) -> None:
    """Coercion/truncation would return one malformed row as authorized evidence."""
    row = valid_row()
    row[field] = value
    client = FakeClient([row])
    adapter = AzureAISearchAdapter(config(), client_factory=lambda _index: client)

    with pytest.raises(SearchUnavailable):
        await adapter.search(execution())


@pytest.mark.asyncio
async def test_selected_index_rejects_source_type_outside_its_route_allowlist() -> None:
    """A cross-family sourceType label must invalidate the whole selected page."""
    row = valid_row()
    row["sourceType"] = "openapi"

    with pytest.raises(SearchUnavailable):
        route = AzureIndexTarget(
            query_index="kb-code-read",
            physical_index="kb-code-v1-20260824",
            schema_version="search-schema-v1",
            embedding_model_id="tap-embed-fixed-v1",
            vector_dimension=2,
            allowed_source_types=frozenset({"code", "code_summary"}),
        )
        adapter = AzureAISearchAdapter(
            config(indexes={SourceFamily.CODE: route}),
            client_factory=lambda _index: FakeClient([row]),
        )
        await adapter.search(execution())


@pytest.mark.parametrize(
    ("allowed_source_types", "error", "message"),
    [
        (frozenset(), ValueError, "source type"),
        ({"code"}, TypeError, "frozenset"),
        (frozenset({""}), ValueError, "source type"),
    ],
)
def test_index_target_requires_an_immutable_closed_source_type_allowlist(
    allowed_source_types: object,
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        AzureIndexTarget(
            query_index="kb-code-read",
            physical_index="kb-code-v1-20260824",
            schema_version="search-schema-v1",
            embedding_model_id="tap-embed-fixed-v1",
            vector_dimension=2,
            allowed_source_types=allowed_source_types,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_rows_partial_pages_request_bytes_and_response_bytes_are_hard_bounded() -> None:
    """Truncating/first-buffering would return or allocate beyond the configured bounds."""
    too_many = FakeClient([valid_row(), valid_row()])
    row_bounded = AzureAISearchAdapter(
        config(max_rows=1),
        client_factory=lambda _index: too_many,
    )
    with pytest.raises(SearchUnavailable):
        await row_bounded.search(execution())

    over_requested_top = FakeClient([valid_row(), valid_row()])
    top_bounded = AzureAISearchAdapter(
        config(per_index_candidates=1, max_rows=10),
        client_factory=lambda _index: over_requested_top,
    )
    with pytest.raises(SearchUnavailable):
        await top_bounded.search(execution())

    partial = FakeClient([valid_row()], partial=True)
    partial_adapter = AzureAISearchAdapter(config(), client_factory=lambda _index: partial)
    with pytest.raises(SearchUnavailable):
        await partial_adapter.search(execution())

    request_client = FakeClient()
    request_bounded = AzureAISearchAdapter(
        config(max_request_bytes=256),
        client_factory=lambda _index: request_client,
    )
    with pytest.raises(SearchBoundsExceeded):
        await request_bounded.search(execution())
    assert request_client.calls == []

    async def oversized_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b"x" * 2_000 + b"}")

    response_bounded = AzureAISearchAdapter(
        config(max_response_bytes=512),
        http_transport=httpx.MockTransport(oversized_response),
    )
    with pytest.raises(SearchUnavailable):
        await response_bounded.search(execution())


@pytest.mark.asyncio
@pytest.mark.parametrize("next_link", [0, "", {}])
async def test_malformed_pagination_marker_rejects_the_rest_page(next_link: object) -> None:
    """Falsy coercion must not turn a malformed partial-page marker into completeness."""

    async def malformed_partial(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"value": [valid_row()], "@odata.nextLink": next_link},
        )

    adapter = AzureAISearchAdapter(
        config(),
        http_transport=httpx.MockTransport(malformed_partial),
    )
    with pytest.raises(SearchUnavailable):
        await adapter.search(execution())


@pytest.mark.asyncio
async def test_outer_deadline_starts_before_filter_and_request_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting the timer only at the socket would omit mandatory filter construction."""
    client = FakeClient([valid_row()])
    adapter = AzureAISearchAdapter(
        config(deadline_seconds=0.001),
        client_factory=lambda _index: client,
    )
    original = AzureAISearchAdapter._security_filter

    def slow_filter(bound: SearchExecution, family: SourceFamily) -> str:
        time.sleep(0.01)
        return original(bound, family)

    monkeypatch.setattr(
        AzureAISearchAdapter,
        "_security_filter",
        staticmethod(slow_filter),
    )

    with pytest.raises(SearchUnavailable):
        await adapter.search(execution())

    assert client.calls == []


@pytest.mark.asyncio
async def test_selected_index_failure_cancels_other_fanout_queries() -> None:
    """A failed strict fan-out must not leave sibling index work running in background."""

    class DelayedPartialClient(FakeClient):
        async def search(self, **kwargs: Any) -> FakeResults:
            self.calls.append(kwargs)
            await asyncio.sleep(0.01)
            return FakeResults([], partial=True)

    class BlockingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False

        async def search(self, **kwargs: Any) -> FakeResults:
            self.calls.append(kwargs)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

    failing = DelayedPartialClient()
    blocking = BlockingClient()
    clients = {
        "kb-code-read": failing,
        "kb-doc-read": blocking,
    }
    adapter = AzureAISearchAdapter(
        config(
            indexes={
                SourceFamily.CODE: target(),
                SourceFamily.DOC: target(SourceFamily.DOC),
            }
        ),
        client_factory=lambda index: clients[index],
    )

    with pytest.raises(SearchUnavailable):
        await adapter.search(
            execution(query_plan=plan(families=(SourceFamily.CODE, SourceFamily.DOC)))
        )

    await asyncio.sleep(0)
    assert blocking.cancelled is True


class TokenProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_token(self) -> str:
        self.calls += 1
        return "short-lived-token"


@pytest.mark.asyncio
async def test_bearer_token_is_default_and_physical_identity_is_explicit() -> None:
    """Implicit query-key auth or alias-as-physical provenance must fail this boundary."""
    provider = TokenProvider()
    seen_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(
            200,
            headers={"request-id": "azure-request-17"},
            json={"value": [valid_row()]},
        )

    bearer_config = AzureSearchConfig(
        endpoint="https://search.example",
        indexes={SourceFamily.CODE: target()},
        bearer_token_provider=provider,
        max_fan_out=1,
        per_index_candidates=10,
        max_connections=1,
        deadline_seconds=1,
        max_retries=0,
    )
    adapter = AzureAISearchAdapter(
        bearer_config,
        http_transport=httpx.MockTransport(handler),
    )
    hits = await adapter.search(execution())

    assert provider.calls == 1
    assert seen_headers["authorization"] == "Bearer short-lived-token"
    assert "api-key" not in seen_headers
    assert hits[0].index_revision.physical_index == "kb-code-v1-20260824"
    assert "short-lived-token" not in repr(bearer_config)

    with pytest.raises((TypeError, ValueError)):
        AzureIndexTarget(
            query_index="kb-code-read",
            physical_index="",
            schema_version="search-schema-v1",
            embedding_model_id="tap-embed-fixed-v1",
            vector_dimension=2,
            allowed_source_types=frozenset({"code"}),
        )


@pytest.mark.asyncio
async def test_index_target_mapping_is_snapshotted_before_adapter_use() -> None:
    """Mutating a caller dict must not relabel hits from an already selected query index."""
    configured = {SourceFamily.CODE: target()}
    search_config = config(indexes=configured)
    client = FakeClient([valid_row()])
    adapter = AzureAISearchAdapter(
        search_config,
        client_factory=lambda _index: client,
    )

    configured[SourceFamily.CODE] = replace(
        target(),
        physical_index="kb-code-spoofed-after-config",
    )
    hits = await adapter.search(execution())

    assert hits[0].index_revision.physical_index == "kb-code-v1-20260824"
    with pytest.raises(TypeError):
        search_config.indexes[SourceFamily.CODE] = target()  # type: ignore[index]
