"""Authorized Knowledge application and provider-boundary contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
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
    ResourceSubtreeGrant,
    RetrievalPolicyContext,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.adapters.azure_ai_search import (
    AzureAISearchAdapter,
    AzureIndexTarget,
    AzureSearchConfig,
)
from tap.modules.knowledge.adapters.litellm import LiteLLMAdapter, LiteLLMConfig
from tap.modules.knowledge.api import (
    KnowledgeAPI,
    answer_request_from_http,
    answer_response_to_http,
    search_request_from_http,
    search_response_to_http,
)
from tap.modules.knowledge.application.retrieve import AuthorizedRetrieval
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerMode,
    AnswerRequest,
    Claim,
    CodeAnchor,
    ContentRole,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    DocumentAnchor,
    Evidence,
    FilterableSubtree,
    IndexRevision,
    QueryPlan,
    ResolvedResourceRef,
    ResourceMode,
    ResourceRef,
    RetrievalProfileId,
    RevisionKind,
    SearchRequest,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    RedactionResult,
    SearchExecution,
    SearchHit,
)

SOURCE_HASH = "sha256:" + "a" * 64
HIT_CONTENT = "Authorization requires the verified project policy."
CHUNK_HASH = "sha256:" + hashlib.sha256(HIT_CONTENT.encode("utf-8")).hexdigest()


def test_generated_claim_span_uses_equal_paragraphs_not_substrings() -> None:
    """A claim paragraph remains grounded when another paragraph merely mentions it."""
    answer = "Claim exact.\n\nAnother paragraph mentions Claim exact."

    assert AuthorizedRetrieval._complete_paragraph_span(answer, "Claim exact.") == (0, 12)
    assert (
        AuthorizedRetrieval._complete_paragraph_span("First.\n\nSecond.", "First.\n\nSecond.")
        is None
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
        source_content_hash=SOURCE_HASH,
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
        content=HIT_CONTENT,
        source=source_revision(),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index="kb-code-v1-20260823",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="tap-embedding-v1",
        score=0.91,
    )


class FakeSearchPort:
    def __init__(
        self,
        hits: tuple[SearchHit, ...] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.hits = hits
        self.error = error
        self.executions: list[SearchExecution] = []

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        self.executions.append(execution)
        if self.error is not None:
            raise self.error
        return self.hits


class FakeModelPort:
    embedding_model_id = "tap-embedding-v1"
    embedding_dimension = 2

    def __init__(self) -> None:
        self.embedding_queries: list[str] = []
        self.answer_evidence: list[tuple[Evidence, ...]] = []

    async def embed(self, query: str) -> Embedding:
        self.embedding_queries.append(query)
        return Embedding(
            vector=(0.25, 0.5),
            model_id=self.embedding_model_id,
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


class CurrentPolicyVerifier:
    async def verify_current(self, expected: RetrievalPolicyContext) -> RetrievalPolicyContext:
        return expected


class PassthroughRedactor:
    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(sanitized_text=text, redaction_version="redaction-v1")


def api(search: FakeSearchPort, model: FakeModelPort) -> KnowledgeAPI:
    ids = iter(
        (
            "operation-1",
            "query-plan-1",
            "context-snapshot-1",
            "trace-1",
            "citation-1",
            "claim-1",
            *(f"generated-{index}" for index in range(100)),
        )
    )
    return KnowledgeAPI(
        search=search,
        model=model,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=lambda: next(ids),
    )


@pytest.mark.asyncio
async def test_knowledge_search_rejects_environment_and_resource_anchor_before_search() -> None:
    """Removing either pre-search authorization check must call the fake Search port."""
    grant = ResourceGrant(
        family="code",
        source_id="repo:checkout:payment.py",
        revision_kind="git_commit",
        revision="a" * 40,
        source_content_hash=SOURCE_HASH,
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
        source_content_hash=SOURCE_HASH,
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
    assert execution.plan.candidate_limit == 20
    assert execution.plan.retrieval_profile_id.value == "quick-hybrid-v1"
    assert execution.plan.resources[0].revision == "a" * 40
    assert execution.plan.resources[0].source_content_hash == SOURCE_HASH
    assert execution.query_vector == (0.25, 0.5)
    assert response.trace_id == "trace-1"
    assert response.query_plan_id == "query-plan-1"
    assert response.context_snapshot_id == "context-snapshot-1"
    assert response.evidence[0].source.revision == "a" * 40
    assert response.evidence[0].index_revision.physical_index == "kb-code-v1-20260823"
    assert response.evidence[0].embedding_model_version == "tap-embedding-v1"
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
            answer_start=0,
            answer_end=38,
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


@pytest.mark.asyncio
async def test_knowledge_api_rejects_forged_hit_content_before_model_io() -> None:
    forged = replace(search_hit(), content="Forged index text with a real manifest hash.")
    model = FakeModelPort()

    with pytest.raises(AuthorizationDenied):
        await api(FakeSearchPort((forged,)), model).answer(
            AnswerRequest(query="Where is policy verified?"), policy_context()
        )

    assert model.answer_evidence == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [SearchUnavailable("provider unavailable"), SearchBoundsExceeded("bound")],
)
async def test_knowledge_answer_propagates_search_errors_without_generating_an_answer(
    error: Exception,
) -> None:
    """Catching a provider failure as abstention would hide an unavailable search backend."""
    model = FakeModelPort()
    knowledge = api(FakeSearchPort(error=error), model)

    with pytest.raises(type(error)):
        await knowledge.answer(AnswerRequest(query="Where is policy verified?"), policy_context())

    assert model.answer_evidence == []


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


@pytest.mark.parametrize(
    "changes",
    [
        {"answer_mode": "quick"},
        {"source_families": [SourceFamily.CODE]},
        {"source_families": ("code",)},
        {"resource_refs": []},
        {"resource_refs": ("code",)},
        {"requested_environment": 17},
        {"requested_environment": "x" * 129},
        {"requested_corpus_version": 17},
        {"requested_corpus_version": "x" * 129},
    ],
)
def test_internal_retrieval_intent_rejects_runtime_type_and_size_bypasses(
    changes: dict[str, object],
) -> None:
    """Framework-free callers must fail before redaction/model/Search ports."""
    with pytest.raises((TypeError, ValueError)):
        SearchRequest(query="authorization", **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            CodeAnchor,
            {
                "repo": "tap",
                "path": "a.py",
                "symbol": None,
                "line_start": True,
                "line_end": 2,
            },
        ),
        (DocumentAnchor, {"page": True}),
        (DocumentAnchor, {"start_offset": True}),
        (DocumentAnchor, {"bbox": (0.0, 0.0, float("nan"), 1.0)}),
        (DocumentAnchor, {"bbox": (0.0,) * 5}),
    ],
)
def test_internal_anchor_numeric_bounds_are_strict(
    model: type[Any], values: dict[str, object]
) -> None:
    with pytest.raises((TypeError, ValueError)):
        model(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"family": "code"},
        {"source_id": 17},
        {"source_id": "x" * 1_025},
        {"mode": "scope"},
        {"requested_revision": 17},
        {"requested_revision": "x" * 513},
        {"anchor": "code:tap:a.py"},
    ],
)
def test_internal_resource_ref_is_closed_and_bounded(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "family": SourceFamily.CODE,
        "source_id": "repo:tap:a.py",
        "mode": ResourceMode.PREFERRED,
    }
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        ResourceRef(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": "G" * 40},
        {"revision": "a" * 39},
        {"source_content_hash": "sha256:" + "g" * 64},
        {"source_content_hash": "sha256:" + "A" * 64},
    ],
)
def test_domain_source_revision_rejects_noncanonical_immutable_values(
    changes: dict[str, object],
) -> None:
    """Malformed citation provenance must fail at the framework-free model boundary."""
    with pytest.raises(ValueError):
        replace(source_revision(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": "a" * 41},
        {"source_content_hash": "sha256:" + "0" * 63},
    ],
)
def test_resolved_resource_rejects_noncanonical_filter_values(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "family": SourceFamily.CODE,
        "source_id": "repo:checkout:payment.py",
        "mode": ResourceMode.SCOPE,
        "revision_kind": RevisionKind.GIT_COMMIT,
        "revision": "a" * 40,
        "source_content_hash": SOURCE_HASH,
        "anchor": None,
    }
    values.update(changes)
    with pytest.raises(ValueError):
        ResolvedResourceRef(**values)  # type: ignore[arg-type]


def test_evidence_rejects_noncanonical_chunk_hash_before_citation_mapping() -> None:
    with pytest.raises(ValueError):
        Evidence(
            family=SourceFamily.CODE,
            chunk_id="h_" + "1" * 64,
            logical_chunk_id="h_" + "2" * 64,
            title="authorize",
            content="Authorized content.",
            source=source_revision(),
            chunk_content_hash="sha256:" + "z" * 64,
            content_role=ContentRole.SOURCE,
            citation_id="citation-1",
            evidence_label="S1",
            index_revision=IndexRevision(
                physical_index="kb-code-v1-20260824",
                schema_version="search-schema-v1",
                corpus_version="corpus-17",
            ),
            embedding_model_version="tap-embedding-v1",
            acl_decision_id="decision-17",
            score=1 / 61,
        )


def ranked_hit(
    *,
    family: SourceFamily,
    local_rank: int,
    score: float,
    suffix: str,
) -> SearchHit:
    base = search_hit()
    source = base.source
    if family is SourceFamily.DOC:
        source = SourceRevisionRef(
            source_id="document:authorization",
            source_type="document",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision="blob-version-17",
            source_content_hash=SOURCE_HASH,
            anchor=DocumentAnchor(heading_path=("Authorization",), page=1),
        )
    return SearchHit(
        family=family,
        chunk_id="h_" + suffix * 64,
        logical_chunk_id="h_" + suffix * 64,
        title=base.title,
        content=base.content,
        source=source,
        chunk_content_hash=CHUNK_HASH,
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
    assert dumped["hits"][0]["embeddingModelVersion"] == "tap-embedding-v1"
    assert "physicalIndex" not in json.dumps(dumped)

    answer_request = answer_request_from_http(HttpAnswerRequest(query="authorization"))
    answer = await api(FakeSearchPort((search_hit(),)), FakeModelPort()).answer(
        answer_request, policy_context()
    )
    mapped_answer = answer_response_to_http(answer)
    assert mapped_answer.claims[0].citation_ids == ["citation-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("source", "revision", "A" * 40),
        ("source", "revision", "g" * 40),
        ("source", "source_content_hash", "sha256:" + "A" * 64),
        ("source", "source_content_hash", "sha256:" + "a" * 63),
        ("evidence", "chunk_content_hash", "sha256:" + "g" * 64),
    ],
    ids=(
        "git-uppercase",
        "git-non-hex",
        "source-hash-uppercase",
        "source-hash-wrong-length",
        "hit-chunk-hash-non-hex",
    ),
)
async def test_internal_search_mapping_revalidates_runtime_mutated_public_provenance(
    target: str,
    field: str,
    value: str,
) -> None:
    """Mapping must not trust frozen objects that were mutated after construction."""
    response = await api(FakeSearchPort((search_hit(),)), FakeModelPort()).search(
        SearchRequest(query="authorization", source_families=(SourceFamily.CODE,)),
        policy_context(),
    )
    evidence = response.evidence[0]
    mutated = evidence.source if target == "source" else evidence
    object.__setattr__(mutated, field, value)

    with pytest.raises(ValidationError):
        search_response_to_http(response)


@pytest.mark.asyncio
async def test_internal_answer_mapping_revalidates_runtime_mutated_citation_hash() -> None:
    answer = await api(FakeSearchPort((search_hit(),)), FakeModelPort()).answer(
        AnswerRequest(query="authorization"),
        policy_context(),
    )
    object.__setattr__(
        answer.citations[0],
        "chunk_content_hash",
        "sha256:" + "B" * 64,
    )

    with pytest.raises(ValidationError):
        answer_response_to_http(answer)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "source_changes"),
    [
        (SourceFamily.DOC, {}),
        (None, {"revision_kind": RevisionKind.BLOB_VERSION}),
        (
            None,
            {"anchor": DocumentAnchor(heading_path=("Authorization",), page=1)},
        ),
        (
            None,
            {
                "source_type": "document",
                "revision_kind": RevisionKind.BLOB_VERSION,
                "revision": "etag:blob-version-17",
                "anchor": DocumentAnchor(heading_path=("Authorization",), page=1),
            },
        ),
    ],
    ids=("outer-family", "revision-kind", "anchor", "code-with-document-source"),
)
async def test_internal_search_mapping_revalidates_family_compatible_provenance(
    family: SourceFamily | None,
    source_changes: dict[str, object],
) -> None:
    response = await api(FakeSearchPort((search_hit(),)), FakeModelPort()).search(
        SearchRequest(query="authorization", source_families=(SourceFamily.CODE,)),
        policy_context(),
    )
    evidence = response.evidence[0]
    if family is not None:
        object.__setattr__(evidence, "family", family)
    for field, value in source_changes.items():
        object.__setattr__(evidence.source, field, value)

    with pytest.raises(ValidationError):
        search_response_to_http(response)


@pytest.mark.asyncio
async def test_internal_answer_mapping_retains_and_revalidates_citation_family() -> None:
    answer = await api(FakeSearchPort((search_hit(),)), FakeModelPort()).answer(
        AnswerRequest(query="authorization"),
        policy_context(),
    )
    citation = answer.citations[0]
    assert hasattr(citation, "family"), "internal Citation must retain its source family"
    object.__setattr__(citation, "family", SourceFamily.DOC)

    with pytest.raises(ValidationError):
        answer_response_to_http(answer)


@pytest.mark.asyncio
async def test_internal_answer_mapping_rejects_mutated_citation_source_family() -> None:
    answer = await api(FakeSearchPort((search_hit(),)), FakeModelPort()).answer(
        AnswerRequest(query="authorization"),
        policy_context(),
    )
    source = answer.citations[0].source
    for field, value in {
        "source_type": "document",
        "revision_kind": RevisionKind.BLOB_VERSION,
        "revision": "etag:blob-version-17",
        "anchor": DocumentAnchor(heading_path=("Authorization",), page=1),
    }.items():
        object.__setattr__(source, field, value)

    with pytest.raises(ValidationError):
        answer_response_to_http(answer)


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


def azure_row(
    *,
    anchor: dict[str, Any],
    root_id: str | None = None,
    parent_id: str | None = None,
) -> dict[str, Any]:
    return {
        "@search.score": 0.91,
        "indexFamily": "code",
        "chunkId": "h_" + "1" * 64,
        "logicalChunkId": "h_" + "2" * 64,
        "rootId": root_id,
        "parentId": parent_id,
        "title": "authorize",
        "content": "Authorization requires the verified project policy.",
        "sourceId": "repo:checkout:payment.py",
        "sourceType": "code",
        "sourceRevision": "a" * 40,
        "anchorJson": json.dumps(anchor),
        "sourceContentHash": SOURCE_HASH,
        "chunkContentHash": CHUNK_HASH,
        "contentRole": "source",
        "derivedFromChunkIds": [],
        "corpusVersion": "corpus-17",
        "schemaVersion": "search-schema-v1",
        "embeddingModelVersion": "tap-embed-fixed-v1",
    }


def azure_target(family: SourceFamily) -> AzureIndexTarget:
    source_types = {
        SourceFamily.DOC: frozenset({"document", "openapi"}),
        SourceFamily.CODE: frozenset({"code", "code_summary"}),
        SourceFamily.BDD: frozenset({"bdd"}),
        SourceFamily.FAILURE: frozenset({"failure"}),
    }
    return AzureIndexTarget(
        query_index=f"kb-{family.value}-read",
        physical_index=f"kb-{family.value}-v1-20260823",
        schema_version="search-schema-v1",
        embedding_model_id="tap-embed-fixed-v1",
        vector_dimension=2,
        allowed_source_types=source_types[family],
    )


def azure_config(
    *,
    families: tuple[SourceFamily, ...] = (SourceFamily.CODE,),
    **changes: object,
) -> AzureSearchConfig:
    values: dict[str, object] = {
        "endpoint": "https://search.example",
        "indexes": {family: azure_target(family) for family in families},
        "query_api_key": "not-a-real-key",
        "allow_query_key_auth": True,
        "max_fan_out": len(families),
        "per_index_candidates": 10,
        "max_connections": 1,
        "deadline_seconds": 1,
        "max_retries": 0,
    }
    values.update(changes)
    return AzureSearchConfig(**values)  # type: ignore[arg-type]


def azure_execution(
    *,
    families: tuple[SourceFamily, ...] = (SourceFamily.CODE,),
    policy: RetrievalPolicyContext | None = None,
    resources: tuple[ResolvedResourceRef, ...] = (),
) -> SearchExecution:
    current = policy or policy_context(tenant_id="tenant'o", groups=frozenset({"group'one"}))
    sanitized_query = "authorization"
    sanitized_hash = "sha256:" + hashlib.sha256(sanitized_query.encode()).hexdigest()
    plan = QueryPlan(
        query_plan_id="query-plan-azure-1",
        operation_id="operation-azure-1",
        tenant_id=current.tenant_id,
        project_id=current.project_id,
        policy_decision_id=current.decision_id,
        policy_version=current.policy_version,
        acl_digest=current.acl_digest,
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=families,
        resources=resources,
        effective_environment="production",
        corpus_version="corpus-17",
        candidate_limit=30,
        raw_request_hash="sha256:" + "a" * 64,
        sanitized_query=sanitized_query,
        sanitized_query_hash=sanitized_hash,
        redaction_version="redaction-v1",
        embedding_model_id="tap-embed-fixed-v1",
        embedding_dimension=2,
    )
    snapshot = ContextSnapshot(
        context_snapshot_id="context-snapshot-azure-1",
        operation_id=plan.operation_id,
        tenant_id=current.tenant_id,
        project_id=current.project_id,
        policy_decision_id=current.decision_id,
        policy_version=current.policy_version,
        acl_digest=current.acl_digest,
        layers=(
            ContextLayer(
                kind=ContextLayerKind.CURRENT_TURN,
                ref_ids=(),
                content_hash=sanitized_hash,
                token_count=1,
            ),
        ),
    )
    return SearchExecution(
        policy=current,
        plan=plan,
        context_snapshot=snapshot,
        query_vector=(0.25, 0.5),
    )


@pytest.mark.asyncio
async def test_azure_adapter_builds_escaped_mandatory_prefilter_and_caps_candidates() -> None:
    """Dropping/overriding an ACL clause or top cap must change this hand-derived request."""
    client = FakeAzureClient()
    adapter = AzureAISearchAdapter(
        azure_config(
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
        "indexes": {SourceFamily.CODE: azure_target(SourceFamily.CODE)},
        "query_api_key": "not-a-real-key",
        "allow_query_key_auth": True,
        field: value,
    }
    with pytest.raises((TypeError, ValueError)):
        AzureSearchConfig(**values)  # type: ignore[arg-type]


def test_azure_config_repr_does_not_reveal_credential() -> None:
    """Dataclass diagnostics must not render the Azure Search secret."""
    config = AzureSearchConfig(
        endpoint="https://search.example",
        indexes={SourceFamily.CODE: azure_target(SourceFamily.CODE)},
        query_api_key="secret-value-must-not-appear",
        allow_query_key_auth=True,
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
        embedding_dimension=2,
        allowed_embedding_model_labels=frozenset({"tap-embed-fixed-v1"}),
        allowed_answer_model_labels=frozenset({"tap-answer-fixed-v1"}),
        allowed_retrieval_profile_ids=frozenset({"quick-hybrid-v1"}),
    )
    assert "gateway-secret-must-not-appear" not in repr(config)


@pytest.mark.asyncio
async def test_azure_adapter_bounds_fanout_retries_and_deadline() -> None:
    """Unbounded queries, retries, or waits must fail these externally visible limits."""
    clients = {
        SourceFamily.DOC: FakeAzureClient(),
        SourceFamily.CODE: FakeAzureClient(),
    }
    bounded = AzureAISearchAdapter(
        azure_config(
            families=(SourceFamily.DOC, SourceFamily.CODE),
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
        azure_config(
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
    assert (
        retrying_client.calls[0]["client_request_id"]
        == retrying_client.calls[1]["client_request_id"]
    )

    slow_client = FakeAzureClient(delay=0.05)
    deadline = AzureAISearchAdapter(
        azure_config(
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
        azure_config(
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda _index: client,
    )
    base = azure_execution()
    with pytest.raises((TypeError, ValueError)):
        replace(base.plan, candidate_limit=True)  # type: ignore[arg-type]

    invalid_vector = SearchExecution(
        policy=base.policy,
        plan=base.plan,
        context_snapshot=base.context_snapshot,
        query_vector=(float("nan"),),
    )
    with pytest.raises(SearchBoundsExceeded):
        await adapter.search(invalid_vector)

    assert client.calls == []


@pytest.mark.asyncio
async def test_azure_adapter_rejects_policy_family_bypass_and_trims_sibling_anchor() -> None:
    """Direct port calls and broad source filters must not widen the authorized scope."""
    bypass_client = FakeAzureClient()
    bypass = AzureAISearchAdapter(
        azure_config(
            families=(SourceFamily.BDD,),
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
        azure_config(
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=1,
            max_retries=0,
        ),
        client_factory=lambda _index: sibling_client,
    )
    subtree = FilterableSubtree(root_ids=("root-authorize",))
    grant = ResourceGrant(
        family="code",
        source_id="repo:checkout:payment.py",
        revision_kind="git_commit",
        revision="a" * 40,
        source_content_hash=SOURCE_HASH,
        allowed_anchor_keys=frozenset({"code:checkout:payment.py:authorize:10:25"}),
        subtree_grants=(
            ResourceSubtreeGrant(
                anchor_key="code:checkout:payment.py:authorize:10:25",
                root_ids=subtree.root_ids,
            ),
        ),
    )
    current = policy_context(resource_grants=(grant,))
    execution = azure_execution(
        policy=current,
        resources=(
            ResolvedResourceRef(
                family=SourceFamily.CODE,
                source_id="repo:checkout:payment.py",
                mode=ResourceMode.SCOPE,
                revision_kind=RevisionKind.GIT_COMMIT,
                revision="a" * 40,
                source_content_hash=SOURCE_HASH,
                anchor=source_revision().anchor,
                subtree=subtree,
            ),
        ),
    )

    with pytest.raises(SearchUnavailable):
        await scoped.search(execution)


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
        azure_config(
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
                embedding_dimension=2,
                allowed_embedding_model_labels=frozenset(
                    {
                        "tap-embed-fixed-v1",
                    }
                ),
                allowed_answer_model_labels=frozenset(
                    {
                        "tap-answer-fixed-v1",
                        "gateway-model-17",
                        "provider-model-17",
                    }
                ),
                allowed_retrieval_profile_ids=frozenset({"quick-hybrid-v1"}),
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
                    chunk_content_hash=CHUNK_HASH,
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
