"""Trusted retrieval-policy construction behavior."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    AuthorizationDenied,
    AuthorizedActor,
    Classification,
    PolicyUnavailable,
    ProjectPolicy,
    ResourceGrant,
    VerifiedSubjectFacts,
)

SOURCE_HASH = "sha256:" + "a" * 64
GroupSetBuilder = Callable[[frozenset[str]], object]


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
                source_content_hash=SOURCE_HASH,
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


@pytest.mark.parametrize(
    ("field", "build"),
    (
        ("group_ids", lambda groups: replace(subject(), group_ids=groups)),
        ("allowed_group_ids", lambda groups: replace(project_policy(), allowed_group_ids=groups)),
        (
            "allowed_group_ids",
            lambda groups: AuthorizedActor(
                user_id="user-1",
                allowed_group_ids=groups,
                roles=frozenset({"reader"}),
            ),
        ),
    ),
)
def test_policy_group_sets_allow_128_members_with_a_256_character_member(
    field: str,
    build: GroupSetBuilder,
) -> None:
    """A legal bounded group set must remain available to policy construction."""
    groups = frozenset({*(f"group-{index:03d}" for index in range(127)), "g" * 256})

    del field
    build(groups)


@pytest.mark.parametrize(
    ("field", "build"),
    (
        ("group_ids", lambda groups: replace(subject(), group_ids=groups)),
        ("allowed_group_ids", lambda groups: replace(project_policy(), allowed_group_ids=groups)),
        (
            "allowed_group_ids",
            lambda groups: AuthorizedActor(
                user_id="user-1",
                allowed_group_ids=groups,
                roles=frozenset({"reader"}),
            ),
        ),
    ),
)
def test_policy_group_sets_reject_more_than_128_members_without_echoing_values(
    field: str,
    build: GroupSetBuilder,
) -> None:
    """Unbounded group expansion must fail without disclosing membership values."""
    groups = frozenset(f"group-{index:03d}" for index in range(129))

    with pytest.raises(ValueError, match=rf"^{field} must contain at most 128 values$") as error:
        build(groups)

    assert "group-000" not in str(error.value)


@pytest.mark.parametrize(
    "build",
    (
        lambda groups: replace(subject(), group_ids=groups),
        lambda groups: replace(project_policy(), allowed_group_ids=groups),
        lambda groups: AuthorizedActor(
            user_id="user-1",
            allowed_group_ids=groups,
            roles=frozenset({"reader"}),
        ),
    ),
)
@pytest.mark.parametrize("invalid_group", ("g" * 257, ""), ids=("too-long", "blank"))
def test_policy_group_sets_reject_overlong_or_blank_members_without_echoing_values(
    build: GroupSetBuilder,
    invalid_group: str,
) -> None:
    """Invalid group identifiers must fail before they can become provider filters."""
    with pytest.raises((TypeError, ValueError)) as error:
        build(frozenset({invalid_group}))

    if invalid_group:
        assert invalid_group not in str(error.value)


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


@pytest.mark.parametrize(
    ("revision", "source_hash"),
    [
        ("a" * 39, "sha256:" + "a" * 64),
        ("a" * 41, "sha256:" + "a" * 64),
        ("A" * 40, "sha256:" + "a" * 64),
        ("g" * 40, "sha256:" + "a" * 64),
        ("a" * 40, "sha256:" + "a" * 63),
        ("a" * 40, "sha256:" + "A" * 64),
        ("a" * 40, "sha256:" + "g" * 64),
    ],
    ids=(
        "git-short",
        "git-long",
        "git-uppercase",
        "git-non-hex",
        "hash-short",
        "hash-uppercase",
        "hash-non-hex",
    ),
)
def test_resource_policy_rejects_noncanonical_git_revision_or_source_hash(
    revision: str,
    source_hash: str,
) -> None:
    """Malformed immutable facts must fail before they can enter an ACL filter."""
    with pytest.raises(ValueError):
        ResourceGrant(
            family="code",
            source_id="repo:checkout:payment.py",
            revision_kind="git_commit",
            revision=revision,
            source_content_hash=source_hash,
        )


@pytest.mark.parametrize(
    ("family", "revision_kind", "revision"),
    [
        ("code", "git_commit", "a" * 40),
        ("bdd", "git_commit", "b" * 64),
        ("doc", "blob_version", "etag:blob-version-17"),
        ("failure", "mysql_version", "mysql-bin.000017:42"),
    ],
)
def test_resource_policy_accepts_explicit_supported_revision_shapes(
    family: str,
    revision_kind: str,
    revision: str,
) -> None:
    ResourceGrant(
        family=family,
        source_id="source-17",
        revision_kind=revision_kind,
        revision=revision,
        source_content_hash="sha256:" + "a" * 64,
    )
