from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    Classification,
    ProjectPolicy,
    ResourceGrant,
    ResourceSubtreeGrant,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.adapters.milvus import compile_milvus_filter
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    CodeAnchor,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    DocumentAnchor,
    FilterableSubtree,
    QueryPlan,
    ResolvedResourceRef,
    ResourceMode,
    RetrievalProfileId,
    RevisionKind,
    SourceFamily,
    anchor_authorization_key,
)
from tap.modules.knowledge.ports.errors import SearchBoundsExceeded
from tap.modules.knowledge.ports.models import SearchExecution


def doc_execution() -> SearchExecution:
    query = "How does the payment policy work?"
    query_hash = "sha256:" + hashlib.sha256(query.encode()).hexdigest()
    subject = VerifiedSubjectFacts(
        tenant_id="tenant-a",
        user_id="user-a",
        group_ids=frozenset({"group-one"}),
        roles=frozenset({"reader"}),
        token_verified=True,
    )
    project_policy = ProjectPolicy(
        tenant_id="tenant-a",
        project_id="project-a",
        permission_granted=True,
        allowed_group_ids=frozenset({"group-one"}),
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=frozenset({"production"}),
        allowed_source_families=frozenset({"doc"}),
        active_corpus_version="corpus-fixture-v1",
        acl_digest="sha256:" + "a" * 64,
        policy_version="policy-v1",
        decision_id="decision-v1",
    )
    policy = build_retrieval_policy_context(
        subject,
        project_policy,
        requested_tenant_id="tenant-a",
        requested_project_id="project-a",
    )
    plan = QueryPlan(
        query_plan_id="plan-v1",
        operation_id="operation-v1",
        tenant_id="tenant-a",
        project_id="project-a",
        policy_decision_id="decision-v1",
        policy_version="policy-v1",
        acl_digest="sha256:" + "a" * 64,
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=(SourceFamily.DOC,),
        resources=(),
        effective_environment="production",
        corpus_version="corpus-fixture-v1",
        candidate_limit=50,
        raw_request_hash="sha256:" + "b" * 64,
        sanitized_query=query,
        sanitized_query_hash=query_hash,
        redaction_version="redaction-v1",
        embedding_model_id="research-embedding-v1",
        embedding_dimension=1536,
    )
    snapshot = ContextSnapshot(
        context_snapshot_id="snapshot-v1",
        operation_id="operation-v1",
        tenant_id="tenant-a",
        project_id="project-a",
        policy_decision_id="decision-v1",
        policy_version="policy-v1",
        acl_digest="sha256:" + "a" * 64,
        layers=(
            ContextLayer(
                kind=ContextLayerKind.CURRENT_TURN,
                ref_ids=(),
                content_hash=query_hash,
                token_count=7,
            ),
        ),
    )
    return SearchExecution(
        policy=policy,
        plan=plan,
        context_snapshot=snapshot,
        query_vector=(0.0,) * 1536,
    )


def _resource(
    source_id: str,
    *,
    mode: ResourceMode = ResourceMode.SCOPE,
    family: SourceFamily = SourceFamily.DOC,
    subtree: FilterableSubtree | None = None,
) -> ResolvedResourceRef:
    if family is SourceFamily.CODE:
        return ResolvedResourceRef(
            family=family,
            source_id=source_id,
            mode=mode,
            revision_kind=RevisionKind.GIT_COMMIT,
            revision="d" * 40,
            source_content_hash="sha256:" + "e" * 64,
            anchor=CodeAnchor(
                repo="checkout",
                path="payment.py",
                symbol="authorize",
                line_start=10,
                line_end=20,
            ),
            subtree=subtree,
        )
    return ResolvedResourceRef(
        family=family,
        source_id=source_id,
        mode=mode,
        revision_kind=RevisionKind.BLOB_VERSION,
        revision="blob-version-v1",
        source_content_hash="sha256:" + "f" * 64,
        anchor=DocumentAnchor(heading_path=("Payments",), page=1),
        subtree=subtree,
    )


def _execution_with_resources(
    resources: tuple[ResolvedResourceRef, ...],
    *,
    group_ids: frozenset[str] = frozenset({"group-one"}),
) -> SearchExecution:
    base = doc_execution()
    grants = []
    for resource in resources:
        assert resource.anchor is not None
        anchor_key = anchor_authorization_key(resource.anchor)
        grants.append(
            ResourceGrant(
                family=resource.family.value,
                source_id=resource.source_id,
                revision_kind=resource.revision_kind.value,
                revision=resource.revision,
                source_content_hash=resource.source_content_hash,
                allowed_anchor_keys=frozenset({anchor_key}),
                subtree_grants=(
                    (
                        ResourceSubtreeGrant(
                            anchor_key=anchor_key,
                            root_ids=resource.subtree.root_ids,
                            parent_ids=resource.subtree.parent_ids,
                            logical_chunk_ids=resource.subtree.logical_chunk_ids,
                        ),
                    )
                    if resource.subtree is not None
                    else ()
                ),
            )
        )
    subject = VerifiedSubjectFacts(
        tenant_id="tenant-a",
        user_id="user-a",
        group_ids=group_ids,
        roles=frozenset({"reader"}),
        token_verified=True,
    )
    project_policy = ProjectPolicy(
        tenant_id="tenant-a",
        project_id="project-a",
        permission_granted=True,
        allowed_group_ids=group_ids,
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=frozenset({"production"}),
        allowed_source_families=frozenset(
            {SourceFamily.DOC.value, *(resource.family.value for resource in resources)}
        ),
        active_corpus_version="corpus-fixture-v1",
        acl_digest="sha256:" + "a" * 64,
        policy_version="policy-v1",
        decision_id="decision-v1",
        resource_grants=tuple(grants),
    )
    policy = build_retrieval_policy_context(
        subject,
        project_policy,
        requested_tenant_id="tenant-a",
        requested_project_id="project-a",
    )
    return replace(base, policy=policy, plan=replace(base.plan, resources=resources))


def test_milvus_filter_contains_every_mandatory_acl_clause() -> None:
    expression = compile_milvus_filter(doc_execution(), SourceFamily.DOC, max_bytes=32_768)

    for clause in (
        'tenant_id == "tenant-a"',
        'project_id == "project-a"',
        'ARRAY_CONTAINS_ANY(allowed_group_ids, ["group-one"])',
        "classification_rank in [0, 1, 2]",
        'environment in ["production", "global"]',
        'corpus_version == "corpus-fixture-v1"',
        "deleted == false",
    ):
        assert clause in expression


def test_milvus_filter_uses_only_global_when_execution_has_no_environment() -> None:
    execution = doc_execution()
    execution = replace(execution, plan=replace(execution.plan, effective_environment=None))

    expression = compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)

    assert 'environment in ["global"]' in expression


def test_milvus_filter_unions_only_current_family_scope_resources() -> None:
    first_scope = _resource(
        "blob:payment-policy",
        subtree=FilterableSubtree(
            root_ids=("root-payments",),
            parent_ids=("parent-refunds",),
        ),
    )
    second_scope = _resource(
        "blob:refund-policy",
        subtree=FilterableSubtree(logical_chunk_ids=("h_" + "1" * 64,)),
    )
    required = _resource("blob:required", mode=ResourceMode.REQUIRED)
    preferred = _resource("blob:preferred", mode=ResourceMode.PREFERRED)
    other_family_scope = _resource(
        "repo:checkout:payment.py",
        family=SourceFamily.CODE,
        subtree=FilterableSubtree(root_ids=("code-root",)),
    )
    execution = _execution_with_resources(
        (first_scope, second_scope, required, preferred, other_family_scope)
    )

    expression = compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)

    assert 'source_id == "blob:payment-policy"' in expression
    assert 'source_revision == "blob-version-v1"' in expression
    assert 'source_content_hash == "sha256:' + "f" * 64 + '"' in expression
    assert 'root_id in ["root-payments"]' in expression
    assert 'parent_id in ["parent-refunds"]' in expression
    assert 'source_id == "blob:refund-policy"' in expression
    assert 'logical_chunk_id in ["h_' + "1" * 64 + '"]' in expression
    expected_scope_suffix = (
        ' and ((source_id == "blob:payment-policy" '
        'and source_revision == "blob-version-v1" '
        'and source_content_hash == "sha256:' + "f" * 64 + '" and (root_id in ["root-payments"] '
        'or parent_id in ["parent-refunds"])) '
        'or (source_id == "blob:refund-policy" '
        'and source_revision == "blob-version-v1" '
        'and source_content_hash == "sha256:'
        + "f" * 64
        + '" and (logical_chunk_id in ["h_'
        + "1" * 64
        + '"])))'
    )
    assert expression.endswith(expected_scope_suffix)
    assert "blob:required" not in expression
    assert "blob:preferred" not in expression
    assert "repo:checkout:payment.py" not in expression


def test_milvus_filter_json_encodes_quotes_backslashes_and_unicode() -> None:
    source_id = 'blob:付款:"policy"\\draft'
    execution = _execution_with_resources((_resource(source_id),))

    expression = compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)

    assert f"source_id == {json.dumps(source_id, ensure_ascii=False)}" in expression


def test_milvus_filter_rejects_control_characters() -> None:
    execution = _execution_with_resources((_resource("blob:payment\npolicy"),))

    with pytest.raises(SearchBoundsExceeded, match="control character"):
        compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)


@pytest.mark.parametrize("family", (SourceFamily.CODE, SourceFamily.BDD, SourceFamily.FAILURE))
def test_milvus_filter_rejects_non_doc_families(family: SourceFamily) -> None:
    with pytest.raises(SearchBoundsExceeded, match="doc"):
        compile_milvus_filter(doc_execution(), family, max_bytes=32_768)


@pytest.mark.parametrize("empty_field", ("groups", "classifications"))
def test_milvus_filter_rejects_empty_acl_sets_after_trusted_context_corruption(
    empty_field: str,
) -> None:
    execution = doc_execution()
    if empty_field == "groups":
        object.__setattr__(execution.policy.actor, "allowed_group_ids", frozenset())
    else:
        object.__setattr__(execution.policy, "allowed_classifications", frozenset())

    with pytest.raises(SearchBoundsExceeded, match="ACL"):
        compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)


def test_milvus_filter_rejects_more_than_twenty_resources() -> None:
    execution = doc_execution()
    resources = tuple(
        _resource(f"blob:policy-{index}", mode=ResourceMode.REQUIRED) for index in range(21)
    )
    object.__setattr__(execution.plan, "resources", resources)

    with pytest.raises(SearchBoundsExceeded, match="resources"):
        compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)


def test_milvus_filter_rejects_more_than_thirty_two_subtree_locators() -> None:
    subtree = FilterableSubtree(
        root_ids=tuple(f"root-{index}" for index in range(17)),
        parent_ids=tuple(f"parent-{index}" for index in range(16)),
    )
    execution = _execution_with_resources((_resource("blob:payment-policy", subtree=subtree),))

    with pytest.raises(SearchBoundsExceeded, match="locators"):
        compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)


def test_milvus_filter_accepts_256_character_literals_and_rejects_longer_ones() -> None:
    allowed = "a" * 256
    accepted = _execution_with_resources((_resource(allowed),))
    rejected = _execution_with_resources((_resource(allowed + "a"),))

    assert allowed in compile_milvus_filter(accepted, SourceFamily.DOC, max_bytes=32_768)
    with pytest.raises(SearchBoundsExceeded, match="256"):
        compile_milvus_filter(rejected, SourceFamily.DOC, max_bytes=32_768)


def test_milvus_filter_applies_the_limit_to_utf8_bytes() -> None:
    source_id = "blob:" + "付款政策" * 20
    execution = _execution_with_resources((_resource(source_id),))
    expression = compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)

    assert len(expression.encode("utf-8")) > len(expression)
    with pytest.raises(SearchBoundsExceeded, match="byte"):
        compile_milvus_filter(
            execution,
            SourceFamily.DOC,
            max_bytes=len(expression.encode("utf-8")) - 1,
        )


def test_milvus_filter_rejects_expression_larger_than_32_kib() -> None:
    group_ids = frozenset(f"group-{index:03d}-" + "g" * 246 for index in range(128))
    execution = _execution_with_resources((), group_ids=group_ids)

    with pytest.raises(SearchBoundsExceeded, match="byte"):
        compile_milvus_filter(execution, SourceFamily.DOC, max_bytes=32_768)


@pytest.mark.parametrize("max_bytes", (0, -1, 32_769, True))
def test_milvus_filter_rejects_invalid_compiler_byte_bounds(max_bytes: object) -> None:
    with pytest.raises(SearchBoundsExceeded, match="byte"):
        compile_milvus_filter(
            doc_execution(),
            SourceFamily.DOC,
            max_bytes=max_bytes,  # type: ignore[arg-type]
        )
