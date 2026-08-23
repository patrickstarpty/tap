"""Trusted retrieval-policy construction behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    AuthorizationDenied,
    Classification,
    PolicyUnavailable,
    ProjectPolicy,
    ResourceGrant,
    VerifiedSubjectFacts,
)


def subject(
    *,
    tenant_id: str = "tenant-a",
    groups: frozenset[str] = frozenset({"group-shared", "group-subject-only"}),
    token_verified: bool = True,
) -> VerifiedSubjectFacts:
    return VerifiedSubjectFacts(
        tenant_id=tenant_id,
        user_id="user-1",
        group_ids=groups,
        roles=frozenset({"reader"}),
        token_verified=token_verified,
    )


def project_policy(
    *,
    tenant_id: str = "tenant-a",
    project_id: str = "project-a",
    permission_granted: bool = True,
) -> ProjectPolicy:
    return ProjectPolicy(
        tenant_id=tenant_id,
        project_id=project_id,
        permission_granted=permission_granted,
        allowed_group_ids=frozenset({"group-shared", "group-policy-only"}),
        classification_ceiling=Classification.CONFIDENTIAL,
        allowed_environments=frozenset({"staging", "production"}),
        allowed_source_families=frozenset({"doc", "code"}),
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
                source_content_hash="sha256:source",
                allowed_anchor_keys=frozenset({"code:checkout:payment.py:authorize:10:25"}),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("requested_tenant", "requested_project", "policy_tenant", "policy_project"),
    [
        ("tenant-b", "project-a", "tenant-a", "project-a"),
        ("tenant-a", "project-b", "tenant-a", "project-a"),
        ("tenant-a", "project-a", "tenant-b", "project-a"),
        ("tenant-a", "project-a", "tenant-a", "project-b"),
    ],
)
def test_policy_context_rejects_tenant_or_project_mismatch(
    requested_tenant: str,
    requested_project: str,
    policy_tenant: str,
    policy_project: str,
) -> None:
    """Removing any tenant/project equality check must admit one mismatch above."""
    with pytest.raises(AuthorizationDenied):
        build_retrieval_policy_context(
            subject(),
            project_policy(tenant_id=policy_tenant, project_id=policy_project),
            requested_tenant_id=requested_tenant,
            requested_project_id=requested_project,
        )


def test_policy_context_uses_group_intersection_and_explicit_classification_set() -> None:
    """Using either caller groups or a lexical ceiling directly must fail this test."""
    context = build_retrieval_policy_context(
        subject(),
        project_policy(),
        requested_tenant_id="tenant-a",
        requested_project_id="project-a",
    )

    assert context.actor.allowed_group_ids == frozenset({"group-shared"})
    assert context.allowed_classifications == frozenset(
        {
            Classification.PUBLIC,
            Classification.INTERNAL,
            Classification.CONFIDENTIAL,
        }
    )
    assert context.allowed_environments == frozenset({"staging", "production"})
    assert context.allowed_source_families == frozenset({"doc", "code"})
    assert context.active_corpus_version == "corpus-17"


def test_policy_context_rejects_unverified_subject_or_empty_group_intersection() -> None:
    """Treating unverified identity or an empty ACL as public must fail this test."""
    with pytest.raises(AuthorizationDenied):
        build_retrieval_policy_context(
            subject(token_verified=False),
            project_policy(),
            requested_tenant_id="tenant-a",
            requested_project_id="project-a",
        )

    with pytest.raises(AuthorizationDenied):
        build_retrieval_policy_context(
            subject(groups=frozenset({"unrelated-group"})),
            project_policy(),
            requested_tenant_id="tenant-a",
            requested_project_id="project-a",
        )

    with pytest.raises(AuthorizationDenied):
        build_retrieval_policy_context(
            subject(groups=frozenset({"bad|group"})),
            ProjectPolicy(
                tenant_id="tenant-a",
                project_id="project-a",
                permission_granted=True,
                allowed_group_ids=frozenset({"bad|group"}),
                classification_ceiling=Classification.INTERNAL,
                allowed_environments=frozenset({"production"}),
                allowed_source_families=frozenset({"doc"}),
                active_corpus_version="corpus-17",
                acl_digest="sha256:acl-17",
                policy_version="policy-17",
                decision_id="decision-17",
            ),
            requested_tenant_id="tenant-a",
            requested_project_id="project-a",
        )


def test_policy_context_fails_closed_when_policy_is_unavailable_or_permission_revoked() -> None:
    """Falling back to identity-only access during an outage/revocation must fail."""
    with pytest.raises(PolicyUnavailable):
        build_retrieval_policy_context(
            subject(),
            None,
            requested_tenant_id="tenant-a",
            requested_project_id="project-a",
        )

    with pytest.raises(AuthorizationDenied):
        build_retrieval_policy_context(
            subject(),
            project_policy(permission_granted=False),
            requested_tenant_id="tenant-a",
            requested_project_id="project-a",
        )


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_policy_runtime_boolean_fields_reject_coercible_non_booleans(value: object) -> None:
    """Treating truthy strings or integer booleans as verified facts must fail closed."""
    with pytest.raises((TypeError, ValueError)):
        subject(token_verified=value)  # type: ignore[arg-type]

    with pytest.raises((TypeError, ValueError)):
        project_policy(permission_granted=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    (
        "tenant_id",
        "project_id",
        "active_corpus_version",
        "acl_digest",
        "policy_version",
        "decision_id",
    ),
)
def test_project_policy_required_string_facts_reject_truthy_non_strings(field: str) -> None:
    """A truthy runtime value must not become part of a current policy binding."""
    with pytest.raises((TypeError, ValueError)):
        replace(project_policy(), **{field: 1})
