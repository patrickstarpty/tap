"""Resolved required/preferred/conflicting resource behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

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
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerRequest,
    CodeAnchor,
    ContentRole,
    FilterableSubtree,
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

AUTHORIZED_ANCHOR = CodeAnchor(
    repo="checkout",
    path="payment.py",
    symbol="authorize",
    line_start=10,
    line_end=25,
)
SOURCE_HASH = "sha256:" + "a" * 64
OTHER_SOURCE_HASH = "sha256:" + "b" * 64
FIRST_VERSION_HASH = "sha256:" + "c" * 64
SECOND_VERSION_HASH = "sha256:" + "d" * 64


def policy_context() -> RetrievalPolicyContext:
    subject = VerifiedSubjectFacts(
        tenant_id="tenant-a",
        user_id="user-1",
        group_ids=frozenset({"group-one"}),
        roles=frozenset({"reader"}),
        token_verified=True,
    )
    policy = ProjectPolicy(
        tenant_id="tenant-a",
        project_id="project-a",
        permission_granted=True,
        allowed_group_ids=frozenset({"group-one"}),
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=frozenset({"production"}),
        allowed_source_families=frozenset({"code", "doc"}),
        active_corpus_version="corpus-17",
        acl_digest="sha256:acl-17",
        policy_version="policy-17",
        decision_id="decision-17",
        resource_grants=(
            ResourceGrant(
                family="code",
                source_id="repo:checkout:payment.py",
                revision_kind="git_commit",
                revision="a" * 40,
                source_content_hash=SOURCE_HASH,
                allowed_anchor_keys=frozenset({"code:checkout:payment.py:authorize:10:25"}),
            ),
        ),
    )
    return build_retrieval_policy_context(
        subject,
        policy,
        requested_tenant_id="tenant-a",
        requested_project_id="project-a",
    )


def scoped_policy_context(*, with_subtree: bool) -> RetrievalPolicyContext:
    base = policy_context()
    grant = base.resource_grants[0]
    scoped_grant = replace(
        grant,
        subtree_grants=(
            (
                ResourceSubtreeGrant(
                    anchor_key="code:checkout:payment.py:authorize:10:25",
                    root_ids=("root-payment",),
                    parent_ids=("parent-authorize",),
                    logical_chunk_ids=("h_" + "9" * 64,),
                ),
            )
            if with_subtree
            else ()
        ),
    )
    subject = VerifiedSubjectFacts(
        tenant_id=base.tenant_id,
        user_id=base.actor.user_id,
        group_ids=base.actor.allowed_group_ids,
        roles=base.actor.roles,
        token_verified=True,
    )
    policy = ProjectPolicy(
        tenant_id=base.tenant_id,
        project_id=base.project_id,
        permission_granted=True,
        allowed_group_ids=base.actor.allowed_group_ids,
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=base.allowed_environments,
        allowed_source_families=base.allowed_source_families,
        active_corpus_version=base.active_corpus_version,
        acl_digest=base.acl_digest,
        policy_version=base.policy_version,
        decision_id=base.decision_id,
        resource_grants=(scoped_grant,),
    )
    return build_retrieval_policy_context(
        subject,
        policy,
        requested_tenant_id=base.tenant_id,
        requested_project_id=base.project_id,
    )


class SamePolicyVerifier:
    async def verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext:
        return expected


class StaticRedactor:
    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(sanitized_text=text, redaction_version="redaction-v3")


class GroundedModel:
    embedding_model_id = "tap-embed-fixed-v1"
    embedding_dimension = 2

    def __init__(self) -> None:
        self.answer_calls = 0

    async def embed(self, query: str) -> Embedding:
        del query
        return Embedding(
            vector=(0.25, 0.5),
            model_id=self.embedding_model_id,
            provider_request_id="embed-17",
        )

    async def answer(self, query, evidence, profile_id) -> AnswerGeneration:
        del query, evidence, profile_id
        self.answer_calls += 1
        return AnswerGeneration(
            text="Grounded.",
            claims=(GeneratedClaim(text="Grounded.", evidence_labels=("S1",)),),
            model_id="tap-answer-fixed-v1",
            profile_id="grounded-answer-v1",
            provider_request_id="answer-17",
        )


class StaticSearch:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        self.executions: list[SearchExecution] = []

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        self.executions.append(execution)
        return self.hits


def knowledge(search: StaticSearch, model: GroundedModel) -> KnowledgeAPI:
    identifiers = iter(f"id-{index}" for index in range(100))
    return KnowledgeAPI(
        search=search,
        model=model,
        policy_verifier=SamePolicyVerifier(),
        redactor=StaticRedactor(),
        id_factory=lambda: next(identifiers),
    )


def hit(
    *,
    family: SourceFamily = SourceFamily.CODE,
    source_id: str = "repo:checkout:payment.py",
    revision_kind: RevisionKind = RevisionKind.GIT_COMMIT,
    revision: str = "a" * 40,
    source_hash: str = SOURCE_HASH,
    anchor: CodeAnchor = AUTHORIZED_ANCHOR,
    local_rank: int = 1,
    chunk_suffix: str = "1",
    logical_chunk_id: str = "h_" + "9" * 64,
    chunk_hash: str | None = None,
) -> SearchHit:
    return SearchHit(
        family=family,
        chunk_id="h_" + chunk_suffix * 64,
        logical_chunk_id=logical_chunk_id,
        title="authorize",
        content="Authorization uses current policy.",
        source=SourceRevisionRef(
            source_id=source_id,
            source_type=family.value,
            revision_kind=revision_kind,
            revision=revision,
            source_content_hash=source_hash,
            anchor=anchor,
        ),
        chunk_content_hash=chunk_hash or "sha256:" + chunk_suffix * 64,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index=f"kb-{family.value}-v1-20260824",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="tap-embed-fixed-v1",
        score=99.0,
        local_rank=local_rank,
    )


def required_request() -> AnswerRequest:
    return AnswerRequest(
        query="Where is authorization?",
        source_families=(SourceFamily.CODE, SourceFamily.DOC),
        resource_refs=(
            ResourceRef(
                family=SourceFamily.CODE,
                source_id="repo:checkout:payment.py",
                mode=ResourceMode.REQUIRED,
                requested_revision="a" * 40,
                anchor=AUTHORIZED_ANCHOR,
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatched_hit",
    [
        hit(family=SourceFamily.DOC),
        hit(revision_kind=RevisionKind.BLOB_VERSION),
        hit(source_hash=OTHER_SOURCE_HASH),
        hit(
            anchor=replace(
                AUTHORIZED_ANCHOR,
                symbol="capture",
                line_start=30,
                line_end=40,
            )
        ),
    ],
    ids=("family", "revision-kind", "source-hash", "anchor"),
)
async def test_required_coverage_matches_every_resolved_immutable_fact(
    mismatched_hit: SearchHit,
) -> None:
    """Source-ID-only coverage would allow each mismatched fixture to answer."""
    model = GroundedModel()
    response = await knowledge(StaticSearch((mismatched_hit,)), model).answer(
        required_request(),
        policy_context(),
    )

    assert response.abstained is True
    assert response.abstention_reason in {
        AbstentionReason.INSUFFICIENT_EVIDENCE,
        AbstentionReason.REVISION_MISMATCH,
    }
    assert model.answer_calls == 0


@pytest.mark.asyncio
async def test_required_coverage_rejects_evidence_outside_resolved_subtree() -> None:
    """Anchor-only coverage would accept a hit outside the server-resolved subtree."""
    outside = replace(
        hit(),
        root_id="root-other",
        parent_id="parent-other",
        logical_chunk_id="h_" + "8" * 64,
    )
    model = GroundedModel()
    response = await knowledge(StaticSearch((outside,)), model).answer(
        required_request(),
        scoped_policy_context(with_subtree=True),
    )

    assert response.abstained is True
    assert model.answer_calls == 0


@pytest.mark.asyncio
async def test_preferred_resource_gets_fixed_profile_weight_without_raw_score_comparison() -> None:
    """Removing the fixed boost must leave the rank-one unrelated hit first."""
    preferred = hit(local_rank=2, chunk_suffix="2")
    unrelated = hit(
        source_id="repo:checkout:other.py",
        source_hash=OTHER_SOURCE_HASH,
        local_rank=1,
        chunk_suffix="3",
    )
    response = await knowledge(StaticSearch((unrelated, preferred)), GroundedModel()).search(
        SearchRequest(
            query="Where is authorization?",
            source_families=(SourceFamily.CODE,),
            resource_refs=(
                ResourceRef(
                    family=SourceFamily.CODE,
                    source_id="repo:checkout:payment.py",
                    mode=ResourceMode.PREFERRED,
                    requested_revision="a" * 40,
                ),
            ),
        ),
        policy_context(),
    )

    assert response.evidence[0].chunk_id == preferred.chunk_id
    assert response.evidence[0].score == pytest.approx((1 / 62) + 0.01)
    assert response.evidence[1].score == pytest.approx(1 / 61)


@pytest.mark.asyncio
async def test_same_logical_chunk_with_different_hashes_abstains_as_conflicting() -> None:
    """Removing conflict detection must call the model and return a normal answer."""
    model = GroundedModel()
    response = await knowledge(
        StaticSearch(
            (
                hit(chunk_suffix="4", chunk_hash=FIRST_VERSION_HASH),
                hit(chunk_suffix="5", chunk_hash=SECOND_VERSION_HASH, local_rank=2),
            )
        ),
        model,
    ).answer(AnswerRequest(query="Which version?"), policy_context())

    assert response.abstained is True
    assert response.abstention_reason is AbstentionReason.CONFLICTING_SOURCES
    assert model.answer_calls == 0


@pytest.mark.asyncio
async def test_one_global_scope_union_excludes_uncovered_families_and_binds_subtree() -> None:
    """Per-family optional scoping would still query the uncovered doc family."""
    search = StaticSearch(())
    await knowledge(search, GroundedModel()).search(
        SearchRequest(
            query="Where is authorization?",
            source_families=(SourceFamily.CODE, SourceFamily.DOC),
            resource_refs=(
                ResourceRef(
                    family=SourceFamily.CODE,
                    source_id="repo:checkout:payment.py",
                    mode=ResourceMode.SCOPE,
                    requested_revision="a" * 40,
                    anchor=AUTHORIZED_ANCHOR,
                ),
            ),
        ),
        scoped_policy_context(with_subtree=True),
    )

    plan = search.executions[0].plan
    assert plan.source_families == (SourceFamily.CODE,)
    assert plan.resources[0].subtree == FilterableSubtree(
        root_ids=("root-payment",),
        parent_ids=("parent-authorize",),
        logical_chunk_ids=("h_" + "9" * 64,),
    )


@pytest.mark.asyncio
async def test_application_rejects_a_search_port_hit_outside_bound_scope() -> None:
    """Provider-side prefiltering remains mandatory, with application validation behind it."""
    outside = replace(
        hit(source_id="repo:checkout:other.py", source_hash=OTHER_SOURCE_HASH),
        root_id="root-other",
        parent_id="parent-other",
    )
    search = StaticSearch((outside,))

    with pytest.raises(AuthorizationDenied, match="outside bound execution"):
        await knowledge(search, GroundedModel()).search(
            SearchRequest(
                query="Where is authorization?",
                resource_refs=(
                    ResourceRef(
                        family=SourceFamily.CODE,
                        source_id="repo:checkout:payment.py",
                        mode=ResourceMode.SCOPE,
                        requested_revision="a" * 40,
                        anchor=AUTHORIZED_ANCHOR,
                    ),
                ),
            ),
            scoped_policy_context(with_subtree=True),
        )

    assert len(search.executions) == 1


@pytest.mark.asyncio
async def test_anchored_scope_without_server_subtree_fails_before_model_or_search() -> None:
    """Post-filter-only exact anchors would allow a broad source query first."""
    search = StaticSearch(())
    model = GroundedModel()
    with pytest.raises(Exception, match="subtree"):
        await knowledge(search, model).search(
            SearchRequest(
                query="Where is authorization?",
                resource_refs=(
                    ResourceRef(
                        family=SourceFamily.CODE,
                        source_id="repo:checkout:payment.py",
                        mode=ResourceMode.SCOPE,
                        requested_revision="a" * 40,
                        anchor=AUTHORIZED_ANCHOR,
                    ),
                ),
            ),
            scoped_policy_context(with_subtree=False),
        )

    assert search.executions == []
