"""Current-policy, redaction, and immutable execution-binding contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    AuthorizationDenied,
    Classification,
    PolicyUnavailable,
    ProjectPolicy,
    ResourceGrant,
    RetrievalPolicyContext,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    BddAnchor,
    CodeAnchor,
    ContentRole,
    DocumentAnchor,
    FailureAnchor,
    IndexRevision,
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
    RedactionResult,
    SearchExecution,
    SearchHit,
)

SOURCE_HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64


def policy_context(
    *,
    tenant_id: str = "tenant-a",
    project_id: str = "project-a",
    user_id: str = "user-1",
    groups: frozenset[str] = frozenset({"group-one"}),
    roles: frozenset[str] = frozenset({"reader"}),
    classification: Classification = Classification.CONFIDENTIAL,
    environments: frozenset[str] = frozenset({"production"}),
    source_families: frozenset[str] = frozenset({"code"}),
    corpus_version: str = "corpus-17",
    acl_digest: str = "sha256:acl-17",
    policy_version: str = "policy-17",
    decision_id: str = "decision-17",
    resource_grants: tuple[ResourceGrant, ...] = (),
) -> RetrievalPolicyContext:
    subject = VerifiedSubjectFacts(
        tenant_id=tenant_id,
        user_id=user_id,
        group_ids=groups,
        roles=roles,
        token_verified=True,
    )
    policy = ProjectPolicy(
        tenant_id=tenant_id,
        project_id=project_id,
        permission_granted=True,
        allowed_group_ids=groups,
        classification_ceiling=classification,
        allowed_environments=environments,
        allowed_source_families=source_families,
        active_corpus_version=corpus_version,
        acl_digest=acl_digest,
        policy_version=policy_version,
        decision_id=decision_id,
        resource_grants=resource_grants,
    )
    return build_retrieval_policy_context(
        subject,
        policy,
        requested_tenant_id=tenant_id,
        requested_project_id=project_id,
    )


class CurrentPolicyVerifier:
    def __init__(
        self,
        result: RetrievalPolicyContext | BaseException | None,
    ) -> None:
        self.result = result
        self.calls: list[RetrievalPolicyContext] = []

    async def verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext | None:
        self.calls.append(expected)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class SequencedPolicyVerifier:
    def __init__(
        self,
        results: list[RetrievalPolicyContext | BaseException | None],
    ) -> None:
        self.results = results
        self.calls: list[RetrievalPolicyContext] = []

    async def verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext | None:
        self.calls.append(expected)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class RecordingRedactor:
    def __init__(self, result: RedactionResult | BaseException) -> None:
        self.result = result
        self.inputs: list[str] = []

    async def redact(self, text: str) -> RedactionResult:
        self.inputs.append(text)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class RecordingModel:
    embedding_model_id = "tap-embed-fixed-v1"
    embedding_dimension = 2

    def __init__(self) -> None:
        self.embedding_queries: list[str] = []
        self.answer_queries: list[str] = []

    async def embed(self, query: str) -> Embedding:
        self.embedding_queries.append(query)
        return Embedding(
            vector=(0.25, 0.5),
            model_id=self.embedding_model_id,
            provider_request_id="embed-request-17",
            gateway_call_id="embed-call-17",
            gateway_model_id="tap-embed-fixed-v1",
            provider_model_id="provider-embed-v1",
            completion_id="embedding-17",
        )

    async def answer(
        self,
        query: str,
        evidence: tuple[Any, ...],
        profile_id: str,
    ) -> AnswerGeneration:
        del evidence
        self.answer_queries.append(query)
        return AnswerGeneration(
            text="Authorization uses current policy.",
            claims=(
                GeneratedClaim(
                    text="Authorization uses current policy.",
                    evidence_labels=("S1",),
                ),
            ),
            model_id="tap-answer-fixed-v1",
            profile_id=profile_id,
            provider_request_id="answer-request-17",
            gateway_call_id="answer-call-17",
            gateway_model_id="tap-answer-fixed-v1",
            provider_model_id="provider-answer-v1",
            completion_id="completion-17",
        )


class RecordingSearch:
    def __init__(self, hits: tuple[SearchHit, ...] = ()) -> None:
        self.hits = hits
        self.executions: list[SearchExecution] = []

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        self.executions.append(execution)
        return self.hits


def knowledge_api(
    *,
    verifier: CurrentPolicyVerifier | SequencedPolicyVerifier,
    redactor: RecordingRedactor,
    model: RecordingModel,
    search: RecordingSearch,
) -> KnowledgeAPI:
    identifiers = iter(
        (
            "operation-17",
            "query-plan-17",
            "context-snapshot-17",
            "trace-17",
            "citation-17",
            "claim-17",
        )
    )
    return KnowledgeAPI(
        search=search,
        model=model,
        policy_verifier=verifier,
        redactor=redactor,
        id_factory=lambda: next(identifiers),
    )


def search_hit() -> SearchHit:
    return SearchHit(
        family=SourceFamily.CODE,
        chunk_id="h_" + "1" * 64,
        logical_chunk_id="h_" + "2" * 64,
        title="authorize",
        content="Authorization uses current policy.",
        source=SourceRevisionRef(
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
        ),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index="kb-code-v1-20260824",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="tap-embed-fixed-v1",
        score=0.9,
    )


def provenance_hit(
    *,
    family: SourceFamily,
    source_type: str,
    revision_kind: RevisionKind,
    revision: str,
    anchor: object,
) -> SearchHit:
    base = search_hit()
    return replace(
        base,
        family=family,
        source=SourceRevisionRef(
            source_id=f"source:{family.value}:17",
            source_type=source_type,
            revision_kind=revision_kind,
            revision=revision,
            source_content_hash=SOURCE_HASH,
            anchor=anchor,  # type: ignore[arg-type]
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current",
    [
        None,
        AuthorizationDenied("permission revoked"),
        policy_context(decision_id="decision-18"),
    ],
)
async def test_current_policy_failure_stops_before_redaction_embedding_or_search(
    current: RetrievalPolicyContext | BaseException | None,
) -> None:
    """Removing/weakening the current-policy gate must produce one side effect."""
    initial = policy_context()
    verifier = CurrentPolicyVerifier(current)
    redactor = RecordingRedactor(
        RedactionResult(sanitized_text="card [REDACTED]", redaction_version="redaction-v3")
    )
    model = RecordingModel()
    search = RecordingSearch()

    with pytest.raises((AuthorizationDenied, PolicyUnavailable)):
        await knowledge_api(
            verifier=verifier,
            redactor=redactor,
            model=model,
            search=search,
        ).search(SearchRequest(query="card 4111111111111111"), initial)

    assert verifier.calls == [initial]
    assert redactor.inputs == []
    assert model.embedding_queries == []
    assert search.executions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant-b"},
        {"project_id": "project-b"},
        {"user_id": "user-2"},
        {"groups": frozenset({"group-two"})},
        {"roles": frozenset({"writer"})},
        {"classification": Classification.RESTRICTED},
        {"environments": frozenset({"staging"})},
        {"source_families": frozenset({"doc"})},
        {"corpus_version": "corpus-18"},
        {"acl_digest": "sha256:acl-18"},
        {"policy_version": "policy-18"},
        {"decision_id": "decision-18"},
        {
            "resource_grants": (
                ResourceGrant(
                    family="code",
                    source_id="repo:checkout:payment.py",
                    revision_kind="git_commit",
                    revision="a" * 40,
                    source_content_hash=SOURCE_HASH,
                ),
            )
        },
    ],
)
async def test_every_changed_current_policy_fact_stops_before_side_effects(
    changes: dict[str, Any],
) -> None:
    """Current policy equality covers identity, ACL, scope, corpus, and resource facts."""
    initial = policy_context()
    redactor = RecordingRedactor(
        RedactionResult(sanitized_text="authorization", redaction_version="redaction-v3")
    )
    model = RecordingModel()
    search = RecordingSearch()

    with pytest.raises(AuthorizationDenied):
        await knowledge_api(
            verifier=CurrentPolicyVerifier(policy_context(**changes)),
            redactor=redactor,
            model=model,
            search=search,
        ).search(SearchRequest(query="authorization"), initial)

    assert redactor.inputs == []
    assert model.embedding_queries == []
    assert search.executions == []


@pytest.mark.asyncio
async def test_search_uses_redacted_query_and_one_bound_immutable_execution() -> None:
    """Raw egress or independently mutable execution fields must fail this contract."""
    current = policy_context()
    verifier = CurrentPolicyVerifier(current)
    redactor = RecordingRedactor(
        RedactionResult(sanitized_text="card [REDACTED]", redaction_version="redaction-v3")
    )
    model = RecordingModel()
    search = RecordingSearch()

    response = await knowledge_api(
        verifier=verifier,
        redactor=redactor,
        model=model,
        search=search,
    ).search(SearchRequest(query="card 4111111111111111"), current)

    assert redactor.inputs == ["card 4111111111111111"]
    assert model.embedding_queries == ["card [REDACTED]"]
    execution = search.executions[0]
    assert not hasattr(execution, "query")
    assert not hasattr(execution, "source_families")
    assert execution.plan.query_plan_id == "query-plan-17"
    assert execution.plan.operation_id == "operation-17"
    assert execution.plan.sanitized_query == "card [REDACTED]"
    assert execution.plan.raw_request_hash == (
        "sha256:6c0ae0d38b76f4cf17f8aa88a1072b6a7325dfc4b6586214c32d195e6c92bc0f"
    )
    assert execution.plan.sanitized_query_hash == (
        "sha256:f89010a4fcd6b3c49a09b78720b7d903a80ed1f426648498069a64fc2bc2cf9a"
    )
    assert execution.plan.redaction_version == "redaction-v3"
    assert execution.plan.policy_decision_id == "decision-17"
    assert execution.plan.policy_version == "policy-17"
    assert execution.plan.acl_digest == "sha256:acl-17"
    assert execution.plan.embedding_model_id == "tap-embed-fixed-v1"
    assert execution.plan.embedding_dimension == 2
    assert execution.context_snapshot.operation_id == execution.plan.operation_id
    assert execution.context_snapshot.policy_decision_id == execution.plan.policy_decision_id
    assert execution.context_snapshot.context_snapshot_id == "context-snapshot-17"
    assert execution.context_snapshot.layers[0].content_hash == (
        "sha256:f89010a4fcd6b3c49a09b78720b7d903a80ed1f426648498069a64fc2bc2cf9a"
    )
    with pytest.raises(FrozenInstanceError):
        execution.plan.candidate_limit = 100  # type: ignore[misc]

    assert response.trace_id == "trace-17"
    assert response.query_plan_id == "query-plan-17"
    assert response.context_snapshot_id == "context-snapshot-17"
    assert response.embedding_provenance.provider_request_id == "embed-request-17"
    assert response.embedding_provenance.gateway_call_id == "embed-call-17"
    assert response.embedding_provenance.provider_model_id == "provider-embed-v1"


@pytest.mark.asyncio
async def test_revocation_after_redaction_stops_before_embedding() -> None:
    """A decision revoked while redaction runs must never reach model egress."""
    current = policy_context()
    verifier = SequencedPolicyVerifier(
        [current, AuthorizationDenied("permission revoked after redaction")]
    )
    redactor = RecordingRedactor(
        RedactionResult(sanitized_text="card [REDACTED]", redaction_version="redaction-v3")
    )
    model = RecordingModel()
    search = RecordingSearch()

    with pytest.raises(AuthorizationDenied):
        await knowledge_api(
            verifier=verifier,
            redactor=redactor,
            model=model,
            search=search,
        ).search(SearchRequest(query="card 4111111111111111"), current)

    assert verifier.calls == [current, current]
    assert redactor.inputs == ["card 4111111111111111"]
    assert model.embedding_queries == []
    assert search.executions == []


@pytest.mark.asyncio
async def test_revocation_after_embedding_stops_before_search() -> None:
    """A decision revoked during embedding must never reach the Search provider."""
    current = policy_context()
    verifier = SequencedPolicyVerifier(
        [current, current, AuthorizationDenied("permission revoked during operation")]
    )
    redactor = RecordingRedactor(
        RedactionResult(sanitized_text="card [REDACTED]", redaction_version="redaction-v3")
    )
    model = RecordingModel()
    search = RecordingSearch()

    with pytest.raises(AuthorizationDenied):
        await knowledge_api(
            verifier=verifier,
            redactor=redactor,
            model=model,
            search=search,
        ).search(SearchRequest(query="card 4111111111111111"), current)

    assert verifier.calls == [current, current, current]
    assert redactor.inputs == ["card 4111111111111111"]
    assert model.embedding_queries == ["card [REDACTED]"]
    assert search.executions == []


@pytest.mark.asyncio
async def test_unavailable_redaction_stops_before_model_or_search() -> None:
    current = policy_context()
    redactor = RecordingRedactor(PolicyUnavailable("redaction unavailable"))
    model = RecordingModel()
    search = RecordingSearch()

    with pytest.raises(PolicyUnavailable):
        await knowledge_api(
            verifier=CurrentPolicyVerifier(current),
            redactor=redactor,
            model=model,
            search=search,
        ).search(SearchRequest(query="card 4111111111111111"), current)

    assert redactor.inputs == ["card 4111111111111111"]
    assert model.embedding_queries == []
    assert search.executions == []


@pytest.mark.asyncio
async def test_answer_uses_only_sanitized_query_and_preserves_internal_provenance() -> None:
    current = policy_context()
    model = RecordingModel()
    response = await knowledge_api(
        verifier=CurrentPolicyVerifier(current),
        redactor=RecordingRedactor(
            RedactionResult(
                sanitized_text="card [REDACTED]",
                redaction_version="redaction-v3",
            )
        ),
        model=model,
        search=RecordingSearch((search_hit(),)),
    ).answer(AnswerRequest(query="card 4111111111111111"), current)

    assert model.embedding_queries == ["card [REDACTED]"]
    assert model.answer_queries == ["card [REDACTED]"]
    assert response.answer_provenance is not None
    assert response.answer_provenance.provider_request_id == "answer-request-17"
    assert response.answer_provenance.gateway_call_id == "answer-call-17"
    assert response.answer_provenance.provider_model_id == "provider-answer-v1"
    assert response.answer_provenance.completion_id == "completion-17"


@pytest.mark.asyncio
async def test_search_result_vector_space_mismatch_fails_before_evidence() -> None:
    current = policy_context()
    mismatched = replace(search_hit(), embedding_model_version="unknown-embed-v9")

    with pytest.raises(AuthorizationDenied, match="outside bound execution"):
        await knowledge_api(
            verifier=CurrentPolicyVerifier(current),
            redactor=RecordingRedactor(
                RedactionResult(
                    sanitized_text="authorization",
                    redaction_version="redaction-v3",
                )
            ),
            model=RecordingModel(),
            search=RecordingSearch((mismatched,)),
        ).search(SearchRequest(query="authorization"), current)


@pytest.mark.asyncio
async def test_generic_search_port_rejects_the_exact_cross_family_provenance_attack() -> None:
    """A provider-neutral port must not materialize a document row as CODE evidence."""
    current = policy_context(source_families=frozenset({"code"}))
    adversarial = provenance_hit(
        family=SourceFamily.CODE,
        source_type="document",
        revision_kind=RevisionKind.BLOB_VERSION,
        revision="blob-version-17",
        anchor=DocumentAnchor(heading_path=("Authorization",), page=1),
    )

    with pytest.raises(AuthorizationDenied, match="outside bound execution"):
        await knowledge_api(
            verifier=CurrentPolicyVerifier(current),
            redactor=RecordingRedactor(
                RedactionResult(
                    sanitized_text="authorization",
                    redaction_version="redaction-v3",
                )
            ),
            model=RecordingModel(),
            search=RecordingSearch((adversarial,)),
        ).search(
            SearchRequest(query="authorization", source_families=(SourceFamily.CODE,)),
            current,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "source_type", "revision_kind", "revision", "anchor"),
    [
        (
            SourceFamily.CODE,
            "code",
            RevisionKind.BLOB_VERSION,
            "blob-version-17",
            CodeAnchor(repo="tap", path="a.py", symbol=None, line_start=1, line_end=2),
        ),
        (
            SourceFamily.CODE,
            "code",
            RevisionKind.GIT_COMMIT,
            "a" * 40,
            BddAnchor(feature_id="feature-17"),
        ),
        (
            SourceFamily.BDD,
            "bdd",
            RevisionKind.BLOB_VERSION,
            "blob-version-17",
            BddAnchor(feature_id="feature-17"),
        ),
        (
            SourceFamily.BDD,
            "bdd",
            RevisionKind.GIT_COMMIT,
            "a" * 40,
            CodeAnchor(repo="tap", path="a.py", symbol=None, line_start=1, line_end=2),
        ),
        (
            SourceFamily.DOC,
            "document",
            RevisionKind.GIT_COMMIT,
            "a" * 40,
            DocumentAnchor(heading_path=("Authorization",), page=1),
        ),
        (
            SourceFamily.DOC,
            "document",
            RevisionKind.BLOB_VERSION,
            "blob-version-17",
            FailureAnchor(incident_id="incident-17"),
        ),
        (
            SourceFamily.FAILURE,
            "failure",
            RevisionKind.BLOB_VERSION,
            "blob-version-17",
            FailureAnchor(incident_id="incident-17"),
        ),
        (
            SourceFamily.FAILURE,
            "failure",
            RevisionKind.MYSQL_VERSION,
            "mysql-bin.000017:42",
            DocumentAnchor(heading_path=("Authorization",), page=1),
        ),
    ],
    ids=(
        "code-revision",
        "code-anchor",
        "bdd-revision",
        "bdd-anchor",
        "doc-revision",
        "doc-anchor",
        "failure-revision",
        "failure-anchor",
    ),
)
async def test_generic_search_port_rejects_each_family_revision_and_anchor_mismatch(
    family: SourceFamily,
    source_type: str,
    revision_kind: RevisionKind,
    revision: str,
    anchor: object,
) -> None:
    current = policy_context(source_families=frozenset({family.value}))
    mismatched = provenance_hit(
        family=family,
        source_type=source_type,
        revision_kind=revision_kind,
        revision=revision,
        anchor=anchor,
    )

    with pytest.raises(AuthorizationDenied, match="outside bound execution"):
        await knowledge_api(
            verifier=CurrentPolicyVerifier(current),
            redactor=RecordingRedactor(
                RedactionResult(
                    sanitized_text="authorization",
                    redaction_version="redaction-v3",
                )
            ),
            model=RecordingModel(),
            search=RecordingSearch((mismatched,)),
        ).search(
            SearchRequest(query="authorization", source_families=(family,)),
            current,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "source_type", "revision_kind", "revision", "anchor"),
    [
        (
            SourceFamily.CODE,
            "bdd",
            RevisionKind.GIT_COMMIT,
            "a" * 40,
            CodeAnchor(repo="tap", path="a.py", symbol=None, line_start=1, line_end=2),
        ),
        (
            SourceFamily.BDD,
            "failure",
            RevisionKind.GIT_COMMIT,
            "a" * 40,
            BddAnchor(feature_id="feature-17"),
        ),
        (
            SourceFamily.DOC,
            "code",
            RevisionKind.BLOB_VERSION,
            "blob-version-17",
            DocumentAnchor(heading_path=("Authorization",), page=1),
        ),
        (
            SourceFamily.FAILURE,
            "document",
            RevisionKind.MYSQL_VERSION,
            "mysql-bin.000017:42",
            FailureAnchor(incident_id="incident-17"),
        ),
    ],
    ids=("code-as-bdd", "bdd-as-failure", "doc-as-code", "failure-as-document"),
)
async def test_generic_search_port_rejects_known_cross_family_source_type_labels(
    family: SourceFamily,
    source_type: str,
    revision_kind: RevisionKind,
    revision: str,
    anchor: object,
) -> None:
    current = policy_context(source_families=frozenset({family.value}))
    mismatched = provenance_hit(
        family=family,
        source_type=source_type,
        revision_kind=revision_kind,
        revision=revision,
        anchor=anchor,
    )

    with pytest.raises(AuthorizationDenied, match="outside bound execution"):
        await knowledge_api(
            verifier=CurrentPolicyVerifier(current),
            redactor=RecordingRedactor(
                RedactionResult(
                    sanitized_text="authorization",
                    redaction_version="redaction-v3",
                )
            ),
            model=RecordingModel(),
            search=RecordingSearch((mismatched,)),
        ).search(
            SearchRequest(query="authorization", source_families=(family,)),
            current,
        )


@pytest.mark.asyncio
async def test_generic_search_port_allows_an_unknown_doc_subtype_for_route_validation() -> None:
    """Only the Azure route allowlist, not the generic port, owns provider subtypes."""
    current = policy_context(source_families=frozenset({"doc"}))
    document = provenance_hit(
        family=SourceFamily.DOC,
        source_type="policy_manual_v2",
        revision_kind=RevisionKind.BLOB_VERSION,
        revision="blob-version-17",
        anchor=DocumentAnchor(heading_path=("Authorization",), page=1),
    )

    response = await knowledge_api(
        verifier=CurrentPolicyVerifier(current),
        redactor=RecordingRedactor(
            RedactionResult(
                sanitized_text="authorization",
                redaction_version="redaction-v3",
            )
        ),
        model=RecordingModel(),
        search=RecordingSearch((document,)),
    ).search(
        SearchRequest(query="authorization", source_families=(SourceFamily.DOC,)),
        current,
    )

    assert response.evidence[0].source.source_type == "policy_manual_v2"


class ProviderResultThatMustBeDiscarded:
    @property
    def family(self) -> SourceFamily:
        raise AssertionError("revoked Search results must not be inspected")


@pytest.mark.asyncio
async def test_revocation_during_search_discards_result_before_inspection() -> None:
    """Deleting the post-Search refresh would inspect and expose a stale result."""
    current = policy_context()
    verifier = SequencedPolicyVerifier(
        [
            current,
            current,
            current,
            AuthorizationDenied("permission revoked while Search ran"),
        ]
    )
    model = RecordingModel()
    search = RecordingSearch((ProviderResultThatMustBeDiscarded(),))  # type: ignore[arg-type]

    with pytest.raises(AuthorizationDenied, match="permission revoked while Search ran"):
        await knowledge_api(
            verifier=verifier,
            redactor=RecordingRedactor(
                RedactionResult(
                    sanitized_text="authorization",
                    redaction_version="redaction-v3",
                )
            ),
            model=model,
            search=search,
        ).search(SearchRequest(query="authorization"), current)

    assert len(search.executions) == 1
    assert model.embedding_queries == ["authorization"]
    assert verifier.calls == [current, current, current, current]


@pytest.mark.asyncio
@pytest.mark.parametrize("early_path", ["no-evidence", "required-missing", "conflict"])
async def test_post_search_revocation_preempts_every_early_abstention(
    early_path: str,
) -> None:
    """No abstention branch may return from a policy context stale after Search."""
    current = policy_context()
    request = AnswerRequest(query="authorization")
    hits: tuple[SearchHit, ...] = ()
    if early_path == "required-missing":
        required_source = "repo:checkout:required.py"
        current = policy_context(
            resource_grants=(
                ResourceGrant(
                    family="code",
                    source_id=required_source,
                    revision_kind="git_commit",
                    revision="b" * 40,
                    source_content_hash="sha256:" + "b" * 64,
                ),
            )
        )
        request = AnswerRequest(
            query="authorization",
            resource_refs=(
                ResourceRef(
                    family=SourceFamily.CODE,
                    source_id=required_source,
                    mode=ResourceMode.REQUIRED,
                ),
            ),
        )
        hits = (search_hit(),)
    elif early_path == "conflict":
        hits = (
            search_hit(),
            replace(
                search_hit(),
                chunk_id="h_" + "3" * 64,
                chunk_content_hash="sha256:" + "3" * 64,
                local_rank=2,
            ),
        )

    verifier = SequencedPolicyVerifier(
        [
            current,
            current,
            current,
            AuthorizationDenied("permission revoked while Search ran"),
        ]
    )
    model = RecordingModel()
    search = RecordingSearch(hits)

    with pytest.raises(AuthorizationDenied, match="permission revoked while Search ran"):
        await knowledge_api(
            verifier=verifier,
            redactor=RecordingRedactor(
                RedactionResult(
                    sanitized_text="authorization",
                    redaction_version="redaction-v3",
                )
            ),
            model=model,
            search=search,
        ).answer(request, current)

    assert len(search.executions) == 1
    assert model.answer_queries == []
    assert verifier.calls == [current, current, current, current]


@pytest.mark.asyncio
async def test_revocation_during_answer_generation_discards_provider_output() -> None:
    """Deleting the post-answer refresh would return stale text, claims, and citations."""
    current = policy_context()
    verifier = SequencedPolicyVerifier(
        [
            current,
            current,
            current,
            current,
            current,
            AuthorizationDenied("permission revoked while answer generation ran"),
        ]
    )
    model = RecordingModel()

    with pytest.raises(
        AuthorizationDenied,
        match="permission revoked while answer generation ran",
    ):
        await knowledge_api(
            verifier=verifier,
            redactor=RecordingRedactor(
                RedactionResult(
                    sanitized_text="authorization",
                    redaction_version="redaction-v3",
                )
            ),
            model=model,
            search=RecordingSearch((search_hit(),)),
        ).answer(AnswerRequest(query="authorization"), current)

    assert model.answer_queries == ["authorization"]
    assert verifier.calls == [current, current, current, current, current, current]
