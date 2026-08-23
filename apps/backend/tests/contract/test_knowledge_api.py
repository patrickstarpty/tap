"""Authorized Knowledge application and provider-boundary contracts."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tap.contracts.http import (
    RetrievalAnswerRequest as HttpAnswerRequest,
)
from tap.contracts.http import (
    RetrievalAnswerResponse as HttpAnswerResponse,
)
from tap.contracts.http import (
    RetrievalSearchRequest as HttpSearchRequest,
)
from tap.contracts.http import (
    RetrievalSearchResponse as HttpSearchResponse,
)
from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    AuthorizationDenied,
    Classification,
    ProjectPolicy,
    ResourceGrant,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.adapters.azure_ai_search import (
    AzureAISearchAdapter,
    AzureSearchConfig,
    SearchBoundsExceeded,
    SearchUnavailable,
)
from tap.modules.knowledge.adapters.litellm import LiteLLMAdapter, LiteLLMConfig
from tap.modules.knowledge.api import (
    KnowledgeAPI,
    answer_request_from_http,
    answer_response_to_http,
    search_request_from_http,
    search_response_to_http,
)
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerMode,
    AnswerRequest,
    Claim,
    CodeAnchor,
    ContentRole,
    Evidence,
    IndexRevision,
    ResolvedResourceRef,
    ResourceMode,
    ResourceRef,
    RevisionKind,
    SearchRequest,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    SearchExecution,
    SearchHit,
)


def policy_context(
    *,
    tenant_id: str = "tenant-a",
    groups: frozenset[str] = frozenset({"group-one"}),
    allowed_environments: frozenset[str] = frozenset({"production"}),
    allowed_source_families: frozenset[str] = frozenset({"doc", "code"}),
    resource_grants: tuple[ResourceGrant, ...] = (),
):
    subject = VerifiedSubjectFacts(
        tenant_id=tenant_id,
        user_id="user-1",
        group_ids=groups,
        roles=frozenset({"reader"}),
        token_verified=True,
    )
    project = ProjectPolicy(
        tenant_id=tenant_id,
        project_id="project-a",
        permission_granted=True,
        allowed_group_ids=groups,
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=allowed_environments,
        allowed_source_families=allowed_source_families,
        active_corpus_version="corpus-17",
        acl_digest="sha256:acl-17",
        policy_version="policy-17",
        decision_id="decision-17",
        resource_grants=resource_grants,
    )
    return build_retrieval_policy_context(
        subject,
        project,
        requested_tenant_id=tenant_id,
        requested_project_id="project-a",
    )


def source_revision() -> SourceRevisionRef:
    return SourceRevisionRef(
        source_id="repo:checkout:payment.py",
        source_type="code",
        revision_kind=RevisionKind.GIT_COMMIT,
        revision="a" * 40,
        source_content_hash="sha256:source",
        anchor=CodeAnchor(
            repo="checkout",
            path="payment.py",
            symbol="authorize",
            line_start=10,
            line_end=25,
        ),
    )


def search_hit() -> SearchHit:
    return SearchHit(
        family=SourceFamily.CODE,
        chunk_id="h_" + "1" * 64,
        logical_chunk_id="h_" + "2" * 64,
        title="authorize",
        content="Authorization requires the verified project policy.",
        source=source_revision(),
        chunk_content_hash="sha256:chunk",
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index="kb-code-v1-20260823",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="embed-v1",
        score=0.91,
    )


class FakeSearchPort:
    def __init__(self, hits: tuple[SearchHit, ...] = ()) -> None:
        self.hits = hits
        self.executions: list[SearchExecution] = []

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        self.executions.append(execution)
        return self.hits


class FakeModelPort:
    def __init__(self) -> None:
        self.embedding_queries: list[str] = []
        self.answer_evidence: list[tuple[Evidence, ...]] = []

    async def embed(self, query: str) -> Embedding:
        self.embedding_queries.append(query)
        return Embedding(
            vector=(0.25, 0.5),
            model_id="tap-embedding-v1",
            provider_request_id="embedding-request-1",
        )

    async def answer(
        self, query: str, evidence: tuple[Evidence, ...], profile_id: str
    ) -> AnswerGeneration:
        del query, profile_id
        self.answer_evidence.append(evidence)
        return AnswerGeneration(
            text="Policy facts are verified server-side.",
            claims=(
                GeneratedClaim(
                    text="Policy facts are verified server-side.",
                    evidence_labels=("S1",),
                ),
            ),
            model_id="tap-answer-v1",
            profile_id="grounded-answer-v1",
            provider_request_id="answer-request-1",
        )


def api(search: FakeSearchPort, model: FakeModelPort) -> KnowledgeAPI:
    ids = iter(
        (
            "trace-1",
            "query-plan-1",
            "context-snapshot-1",
            "citation-1",
            "claim-1",
            *(f"generated-{index}" for index in range(100)),
        )
    )
    return KnowledgeAPI(search=search, model=model, id_factory=lambda: next(ids))


@pytest.mark.asyncio
async def test_knowledge_search_rejects_environment_and_resource_anchor_before_search() -> None:
    """Removing either pre-search authorization check must call the fake Search port."""
    grant = ResourceGrant(
        family="code",
        source_id="repo:checkout:payment.py",
        revision_kind="git_commit",
        revision="a" * 40,
        source_content_hash="sha256:source",
        allowed_anchor_keys=frozenset({"code:checkout:payment.py:authorize:10:25"}),
    )
    policy = policy_context(resource_grants=(grant,))
    search = FakeSearchPort()
    knowledge = api(search, FakeModelPort())

    with pytest.raises(AuthorizationDenied):
        await knowledge.search(
            SearchRequest(query="authorization", requested_environment="development"),
            policy,
        )

    unauthorized_anchor = CodeAnchor(
        repo="checkout",
        path="secrets.py",
        symbol="read_secret",
        line_start=1,
        line_end=9,
    )
    with pytest.raises(AuthorizationDenied):
        await knowledge.search(
            SearchRequest(
                query="authorization",
                resource_refs=(
                    ResourceRef(
                        family=SourceFamily.CODE,
                        source_id="repo:checkout:payment.py",
                        mode=ResourceMode.REQUIRED,
                        requested_revision="a" * 40,
                        anchor=unauthorized_anchor,
                    ),
                ),
            ),
            policy,
        )

    assert search.executions == []


@pytest.mark.asyncio
async def test_knowledge_search_resolves_revision_caps_and_preserves_provenance() -> None:
    """Trusting a browser revision/topK or dropping source/index lineage must fail."""
    grant = ResourceGrant(
        family="code",
        source_id="repo:checkout:payment.py",
        revision_kind="git_commit",
        revision="a" * 40,
        source_content_hash="sha256:source",
        allowed_anchor_keys=frozenset({"code:checkout:payment.py:authorize:10:25"}),
    )
    search = FakeSearchPort((search_hit(),))
    model = FakeModelPort()
    knowledge = api(search, model)
    request = SearchRequest(
        query="authorization",
        answer_mode=AnswerMode.QUICK,
        source_families=(SourceFamily.CODE,),
        resource_refs=(
            ResourceRef(
                family=SourceFamily.CODE,
                source_id="repo:checkout:payment.py",
                mode=ResourceMode.REQUIRED,
                requested_revision="a" * 40,
                anchor=source_revision().anchor,
            ),
        ),
        requested_environment="production",
        requested_corpus_version="corpus-17",
        top_k=100,
    )

    response = await knowledge.search(request, policy_context(resource_grants=(grant,)))

    execution = search.executions[0]
    assert execution.candidate_limit == 20
    assert execution.profile_id == "quick-hybrid-v1"
    assert execution.resources[0].revision == "a" * 40
    assert execution.resources[0].source_content_hash == "sha256:source"
    assert execution.query_vector == (0.25, 0.5)
    assert response.trace_id == "trace-1"
    assert response.query_plan_id == "query-plan-1"
    assert response.context_snapshot_id == "context-snapshot-1"
    assert response.evidence[0].source.revision == "a" * 40
    assert response.evidence[0].index_revision.physical_index == "kb-code-v1-20260823"
    assert response.evidence[0].embedding_model_version == "embed-v1"
    assert response.evidence[0].citation_id == "citation-1"


@pytest.mark.asyncio
async def test_knowledge_answer_cites_each_claim_and_abstains_without_evidence() -> None:
    """Allowing unsupported claims or calling the model with no evidence must fail."""
    with_evidence_search = FakeSearchPort((search_hit(),))
    model = FakeModelPort()
    response = await api(with_evidence_search, model).answer(
        AnswerRequest(query="Where is policy verified?"), policy_context()
    )

    assert response.abstained is False
    assert response.abstention_reason is None
    assert response.query_plan_id == "query-plan-1"
    assert response.context_snapshot_id == "context-snapshot-1"
    assert response.claims == (
        Claim(
            claim_id="claim-1",
            text="Policy facts are verified server-side.",
            citation_ids=("citation-1",),
        ),
    )
    assert response.citations[0].source.revision == "a" * 40

    no_evidence_model = FakeModelPort()
    no_evidence = await api(FakeSearchPort(), no_evidence_model).answer(
        AnswerRequest(query="Unknown fact"), policy_context()
    )
    assert no_evidence.abstained is True
    assert no_evidence.abstention_reason is AbstentionReason.INSUFFICIENT_EVIDENCE
    assert no_evidence_model.answer_evidence == []


def schema_property_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for nested in value.values():
            names.update(schema_property_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(schema_property_names(nested))
    return names


def test_public_retrieval_dtos_reject_policy_and_provider_fields() -> None:
    """Adding any browser-controlled ACL/filter/index field must fail this schema guard."""
    malicious_fields: dict[str, object] = {
        "tenantId": "other-tenant",
        "projectId": "other-project",
        "allowedGroupIds": ["admin"],
        "classification": "restricted",
        "filter": "true",
        "rawFilter": "true",
        "physicalIndex": "kb-code-private",
        "physicalIndexName": "kb-code-private",
    }
    for name, value in malicious_fields.items():
        with pytest.raises(ValidationError):
            HttpSearchRequest.model_validate({"query": "authorization", name: value})

    forbidden = {
        "tenantId",
        "projectId",
        "allowedGroupIds",
        "classification",
        "filter",
        "rawFilter",
        "physicalIndex",
        "physicalIndexName",
    }
    for model in (
        HttpSearchRequest,
        HttpSearchResponse,
        HttpAnswerRequest,
        HttpAnswerResponse,
    ):
        assert not forbidden & schema_property_names(model.model_json_schema(by_alias=True))


@pytest.mark.parametrize("top_k", [True, 1.5, 101])
def test_internal_search_request_rejects_non_integer_or_out_of_bounds_top_k(
    top_k: object,
) -> None:
    """Bypassing Pydantic must not create an unbounded or bool candidate preference."""
    with pytest.raises((TypeError, ValueError)):
        SearchRequest(query="authorization", top_k=top_k)  # type: ignore[arg-type]


def ranked_hit(
    *,
    family: SourceFamily,
    local_rank: int,
    score: float,
    suffix: str,
) -> SearchHit:
    base = search_hit()
    return SearchHit(
        family=family,
        chunk_id="h_" + suffix * 64,
        logical_chunk_id="h_" + suffix * 64,
        title=base.title,
        content=base.content,
        source=base.source,
        chunk_content_hash=f"sha256:chunk-{suffix}",
        content_role=base.content_role,
        index_revision=IndexRevision(
            physical_index=f"kb-{family.value}-v1-20260823",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version=base.embedding_model_version,
        local_rank=local_rank,
        score=score,
    )


@pytest.mark.asyncio
async def test_cross_index_fusion_uses_local_rank_and_caps_final_results() -> None:
    """Sorting incomparable Azure scores or returning every candidate must fail."""
    hits = (
        ranked_hit(
            family=SourceFamily.CODE,
            local_rank=2,
            score=999.0,
            suffix="3",
        ),
        ranked_hit(
            family=SourceFamily.DOC,
            local_rank=1,
            score=0.01,
            suffix="4",
        ),
        *(
            ranked_hit(
                family=SourceFamily.CODE,
                local_rank=index,
                score=float(100 - index),
                suffix=hex(index + 4)[-1],
            )
            for index in range(3, 18)
        ),
    )
    response = await api(FakeSearchPort(hits), FakeModelPort()).search(
        SearchRequest(query="authorization", answer_mode=AnswerMode.QUICK),
        policy_context(),
    )

    assert len(response.evidence) == 10
    assert response.evidence[0].family is SourceFamily.DOC
    assert response.evidence[1].family is SourceFamily.CODE
    assert response.evidence[0].score == pytest.approx(1 / 61)
    assert response.evidence[1].score == pytest.approx(1 / 62)

    caller_capped = await api(FakeSearchPort(hits), FakeModelPort()).search(
        SearchRequest(query="authorization", answer_mode=AnswerMode.QUICK, top_k=1),
        policy_context(),
    )
    assert len(caller_capped.evidence) == 1


@pytest.mark.asyncio
async def test_http_mapping_is_explicit_and_drops_internal_index_provenance() -> None:
    """Leaking internal DTO/provider details through implicit model dumping must fail."""
    http_request = HttpSearchRequest.model_validate(
        {
            "query": "authorization",
            "answerMode": "quick",
            "sources": ["code"],
            "requestedEnvironment": "production",
            "topK": 7,
        }
    )
    internal_request = search_request_from_http(http_request)
    assert internal_request == SearchRequest(
        query="authorization",
        answer_mode=AnswerMode.QUICK,
        source_families=(SourceFamily.CODE,),
        requested_environment="production",
        top_k=7,
    )

    search = FakeSearchPort((search_hit(),))
    model = FakeModelPort()
    internal_response = await api(search, model).search(internal_request, policy_context())
    public_response = search_response_to_http(internal_response)
    dumped = public_response.model_dump(by_alias=True)
    assert dumped["queryPlanId"] == "query-plan-1"
    assert dumped["contextSnapshotId"] == "context-snapshot-1"
    assert dumped["hits"][0]["indexFamily"] == "code"
    assert dumped["hits"][0]["source"]["revision"] == "a" * 40
    assert dumped["hits"][0]["scores"]["rrf"] == pytest.approx(1 / 61)
    assert dumped["hits"][0]["aclDecisionId"] == "decision-17"
    assert dumped["hits"][0]["schemaVersion"] == "search-schema-v1"
    assert dumped["hits"][0]["embeddingModelVersion"] == "embed-v1"
    assert "physicalIndex" not in json.dumps(dumped)

    answer_request = answer_request_from_http(HttpAnswerRequest(query="authorization"))
    answer = await api(FakeSearchPort((search_hit(),)), FakeModelPort()).answer(
        answer_request, policy_context()
    )
    mapped_answer = answer_response_to_http(answer)
    assert mapped_answer.claims[0].citation_ids == ["citation-1"]


class FakeAzureResults:
    def __init__(self, rows: list[dict[str, Any]], request_id: str | None = None) -> None:
        self.rows = rows
        self.request_id = request_id

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row

        return iterate()


class FakeAzureClient:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        failures: list[BaseException] | None = None,
        delay: float = 0,
        request_id: str | None = None,
    ) -> None:
        self.rows = rows or []
        self.failures = failures or []
        self.delay = delay
        self.request_id = request_id
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> FakeAzureResults:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failures:
            raise self.failures.pop(0)
        return FakeAzureResults(self.rows, self.request_id)

    async def close(self) -> None:
        return None


def azure_row(*, anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "@search.score": 0.91,
        "chunkId": "h_" + "1" * 64,
        "logicalChunkId": "h_" + "2" * 64,
        "title": "authorize",
        "content": "Authorization requires the verified project policy.",
        "sourceId": "repo:checkout:payment.py",
        "sourceType": "code",
        "sourceRevision": "a" * 40,
        "anchorJson": json.dumps(anchor),
        "sourceContentHash": "sha256:source",
        "chunkContentHash": "sha256:chunk",
        "contentRole": "source",
        "derivedFromChunkIds": [],
        "corpusVersion": "corpus-17",
        "schemaVersion": "search-schema-v1",
        "embeddingModelVersion": "embed-v1",
    }


def azure_execution(*, families: tuple[SourceFamily, ...] = (SourceFamily.CODE,)):
    return SearchExecution(
        query="authorization",
        query_vector=(0.25, 0.5),
        source_families=families,
        resources=(),
        effective_environment="production",
        corpus_version="corpus-17",
        candidate_limit=30,
        profile_id="quick-hybrid-v1",
        policy=policy_context(tenant_id="tenant'o", groups=frozenset({"group'one"})),
    )


@pytest.mark.asyncio
async def test_azure_adapter_builds_escaped_mandatory_prefilter_and_caps_candidates() -> None:
    """Dropping/overriding an ACL clause or top cap must change this hand-derived request."""
    client = FakeAzureClient()
    adapter = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases={SourceFamily.CODE: "kb-code-read"},
            physical_indexes={SourceFamily.CODE: "kb-code-v1-20260823"},
            max_fan_out=1,
            per_index_candidates=7,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda _index: client,
    )

    await adapter.search(azure_execution())

    request = client.calls[0]
    assert request["filter"] == (
        "tenantId eq 'tenant''o' and projectId eq 'project-a' "
        "and allowedGroupIds/any(g: search.in(g, 'group''one', '|')) "
        "and search.in(classification, 'public|internal|confidential', '|') "
        "and search.in(environment, 'global|production', '|') "
        "and corpusVersion eq 'corpus-17'"
    )
    assert request["vector_filter_mode"] == "preFilter"
    assert request["top"] == 7
    assert request["vector_queries"][0]["k"] == 7
    assert "filter_override" not in request
    assert isinstance(request["client_request_id"], str)
    assert request["client_request_id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_fan_out", True),
        ("per_index_candidates", 1.5),
        ("max_connections", True),
        ("max_retries", False),
        ("deadline_seconds", float("nan")),
        ("connect_timeout_seconds", float("inf")),
        ("read_timeout_seconds", float("nan")),
    ],
)
def test_azure_config_rejects_bool_fractional_or_non_finite_bounds(
    field: str,
    value: object,
) -> None:
    """Runtime annotation bypasses must not disable Azure resource bounds."""
    values: dict[str, object] = {
        "endpoint": "https://search.example",
        "api_key": "not-a-real-key",
        "index_aliases": {SourceFamily.CODE: "kb-code-read"},
        field: value,
    }
    with pytest.raises((TypeError, ValueError)):
        AzureSearchConfig(**values)  # type: ignore[arg-type]


def test_azure_config_repr_does_not_reveal_credential() -> None:
    """Dataclass diagnostics must not render the Azure Search secret."""
    config = AzureSearchConfig(
        endpoint="https://search.example",
        api_key="secret-value-must-not-appear",
        index_aliases={SourceFamily.CODE: "kb-code-read"},
    )
    assert "secret-value-must-not-appear" not in repr(config)


def test_litellm_config_repr_does_not_reveal_credential() -> None:
    """Dataclass diagnostics must not render the LiteLLM gateway secret."""
    config = LiteLLMConfig(
        base_url="https://litellm.example",
        api_key="gateway-secret-must-not-appear",
        embedding_model_id="tap-embed-fixed-v1",
        answer_model_id="tap-answer-fixed-v1",
        answer_profile_id="grounded-answer-v1",
    )
    assert "gateway-secret-must-not-appear" not in repr(config)


@pytest.mark.asyncio
async def test_azure_adapter_bounds_fanout_retries_and_deadline() -> None:
    """Unbounded queries, retries, or waits must fail these externally visible limits."""
    clients = {
        SourceFamily.DOC: FakeAzureClient(),
        SourceFamily.CODE: FakeAzureClient(),
    }
    aliases = {SourceFamily.DOC: "kb-doc-read", SourceFamily.CODE: "kb-code-read"}
    bounded = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases=aliases,
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda index: clients[
            SourceFamily.DOC if index == "kb-doc-read" else SourceFamily.CODE
        ],
    )
    with pytest.raises(SearchBoundsExceeded):
        await bounded.search(azure_execution(families=(SourceFamily.DOC, SourceFamily.CODE)))
    assert clients[SourceFamily.DOC].calls == []
    assert clients[SourceFamily.CODE].calls == []

    retrying_client = FakeAzureClient(failures=[TimeoutError("first attempt")])
    retrying = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases={SourceFamily.CODE: "kb-code-read"},
            physical_indexes={SourceFamily.CODE: "kb-code-v1-20260823"},
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=1,
        ),
        client_factory=lambda _index: retrying_client,
    )
    await retrying.search(azure_execution())
    assert len(retrying_client.calls) == 2

    slow_client = FakeAzureClient(delay=0.05)
    deadline = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases={SourceFamily.CODE: "kb-code-read"},
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=0.001,
            max_retries=0,
        ),
        client_factory=lambda _index: slow_client,
    )
    with pytest.raises(SearchUnavailable):
        await deadline.search(azure_execution())
    assert len(slow_client.calls) == 1


@pytest.mark.asyncio
async def test_azure_adapter_rejects_invalid_direct_execution_numbers_before_query() -> None:
    """Internal callers must not bypass candidate/vector resource bounds."""
    client = FakeAzureClient()
    adapter = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases={SourceFamily.CODE: "kb-code-read"},
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda _index: client,
    )
    base = azure_execution()
    invalid_candidate = SearchExecution(
        query=base.query,
        query_vector=base.query_vector,
        source_families=base.source_families,
        resources=base.resources,
        effective_environment=base.effective_environment,
        corpus_version=base.corpus_version,
        candidate_limit=True,  # type: ignore[arg-type]
        profile_id=base.profile_id,
        policy=base.policy,
    )
    with pytest.raises(SearchBoundsExceeded):
        await adapter.search(invalid_candidate)

    invalid_vector = SearchExecution(
        query=base.query,
        query_vector=(float("nan"),),
        source_families=base.source_families,
        resources=base.resources,
        effective_environment=base.effective_environment,
        corpus_version=base.corpus_version,
        candidate_limit=base.candidate_limit,
        profile_id=base.profile_id,
        policy=base.policy,
    )
    with pytest.raises(SearchBoundsExceeded):
        await adapter.search(invalid_vector)

    assert client.calls == []


@pytest.mark.asyncio
async def test_azure_adapter_rejects_policy_family_bypass_and_trims_sibling_anchor() -> None:
    """Direct port calls and broad source filters must not widen the authorized scope."""
    bypass_client = FakeAzureClient()
    bypass = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases={SourceFamily.BDD: "kb-bdd-read"},
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda _index: bypass_client,
    )
    with pytest.raises(SearchUnavailable):
        await bypass.search(azure_execution(families=(SourceFamily.BDD,)))
    assert bypass_client.calls == []

    sibling_client = FakeAzureClient(
        rows=[
            azure_row(
                anchor={
                    "type": "code",
                    "repo": "checkout",
                    "path": "payment.py",
                    "symbol": "capture_card",
                    "lineStart": 30,
                    "lineEnd": 45,
                }
            )
        ]
    )
    scoped = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases={SourceFamily.CODE: "kb-code-read"},
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda _index: sibling_client,
    )
    execution = azure_execution()
    execution = SearchExecution(
        query=execution.query,
        query_vector=execution.query_vector,
        source_families=execution.source_families,
        resources=(
            ResolvedResourceRef(
                family=SourceFamily.CODE,
                source_id="repo:checkout:payment.py",
                mode=ResourceMode.SCOPE,
                revision_kind=RevisionKind.GIT_COMMIT,
                revision="a" * 40,
                source_content_hash="sha256:source",
                anchor=source_revision().anchor,
            ),
        ),
        effective_environment=execution.effective_environment,
        corpus_version=execution.corpus_version,
        candidate_limit=execution.candidate_limit,
        profile_id=execution.profile_id,
        policy=execution.policy,
    )

    assert await scoped.search(execution) == ()


@pytest.mark.asyncio
async def test_azure_adapter_preserves_only_allowlisted_search_request_id() -> None:
    """Dropping official request-id or forwarding arbitrary headers must fail."""
    client = FakeAzureClient(
        rows=[
            azure_row(
                anchor={
                    "type": "code",
                    "repo": "checkout",
                    "path": "payment.py",
                    "symbol": "authorize",
                    "lineStart": 10,
                    "lineEnd": 25,
                }
            )
        ],
        request_id="azure-request-17",
    )
    adapter = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint="https://search.example",
            api_key="not-a-real-key",
            index_aliases={SourceFamily.CODE: "kb-code-read"},
            physical_indexes={SourceFamily.CODE: "kb-code-v1-20260823"},
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda _index: client,
    )

    hits = await adapter.search(azure_execution())

    assert hits[0].provider_request_id == "azure-request-17"
    assert hits[0].index_revision.physical_index == "kb-code-v1-20260823"


@pytest.mark.asyncio
async def test_litellm_adapter_uses_fixed_models_captures_request_ids_and_bounds_retry() -> None:
    """Caller-selected models or unbounded 503 retries must fail this gateway contract."""
    attempts = 0
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        payloads.append(json.loads(request.content))
        if attempts == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200,
            headers={
                "x-request-id": "provider-answer-17",
                "x-litellm-call-id": "gateway-call-17",
                "x-litellm-model-id": "gateway-model-17",
                "x-untrusted-diagnostic": "must-not-cross-port",
            },
            json={
                "id": "body-request-id",
                "model": "provider-model-17",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "Grounded answer.",
                                    "claims": [
                                        {"text": "Grounded answer.", "evidenceLabels": ["S1"]}
                                    ],
                                }
                            )
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LiteLLMAdapter(
            LiteLLMConfig(
                base_url="https://litellm.example",
                api_key="not-a-real-key",
                embedding_model_id="tap-embed-fixed-v1",
                answer_model_id="tap-answer-fixed-v1",
                answer_profile_id="grounded-answer-v1",
                deadline_seconds=1,
                max_retries=1,
                max_connections=1,
            ),
            client=client,
        )
        result = await adapter.answer(
            "Where?",
            (
                Evidence(
                    family=SourceFamily.CODE,
                    chunk_id="h_" + "1" * 64,
                    logical_chunk_id="h_" + "2" * 64,
                    title="authorize",
                    content="Policy facts are verified server-side.",
                    source=source_revision(),
                    chunk_content_hash="sha256:chunk",
                    content_role=ContentRole.SOURCE,
                    citation_id="citation-1",
                    evidence_label="S1",
                    index_revision=IndexRevision(
                        physical_index="kb-code-v1-20260823",
                        schema_version="search-schema-v1",
                        corpus_version="corpus-17",
                    ),
                    embedding_model_version="embed-v1",
                    acl_decision_id="decision-17",
                    score=0.91,
                ),
            ),
            "quick-hybrid-v1",
        )

    assert attempts == 2
    assert all(payload["model"] == "tap-answer-fixed-v1" for payload in payloads)
    assert all("profile" not in payload for payload in payloads)
    assert result.profile_id == "grounded-answer-v1"
    assert result.provider_request_id == "provider-answer-17"
    assert result.gateway_call_id == "gateway-call-17"
    assert result.gateway_model_id == "gateway-model-17"
    assert result.provider_model_id == "provider-model-17"
    assert result.completion_id == "body-request-id"
    assert result.claims[0].evidence_labels == ("S1",)
