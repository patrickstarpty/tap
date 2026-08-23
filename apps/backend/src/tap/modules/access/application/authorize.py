"""Construct trusted retrieval policy contexts from two authoritative inputs."""

from __future__ import annotations

from tap.modules.access.domain.policy import (
    CLASSIFICATIONS_THROUGH,
    AuthorizationDenied,
    AuthorizedActor,
    PolicyUnavailable,
    ProjectPolicy,
    RetrievalPolicyContext,
    VerifiedSubjectFacts,
    _new_retrieval_policy_context,
)


def _validate_search_in_value(value: str) -> None:
    if not value or "|" in value:
        raise AuthorizationDenied("policy identifier is not a canonical safe value")


def build_retrieval_policy_context(
    subject: VerifiedSubjectFacts,
    policy: ProjectPolicy | None,
    *,
    requested_tenant_id: str,
    requested_project_id: str,
) -> RetrievalPolicyContext:
    """Intersect verified identity with current Project Policy or fail closed."""
    if not subject.token_verified:
        raise AuthorizationDenied("subject facts were not verified")
    if policy is None:
        raise PolicyUnavailable("Project Policy is unavailable")
    if not policy.permission_granted:
        raise AuthorizationDenied("project permission has been revoked")
    if not (subject.tenant_id == policy.tenant_id == requested_tenant_id):
        raise AuthorizationDenied("tenant does not match the policy decision")
    if policy.project_id != requested_project_id:
        raise AuthorizationDenied("project does not match the policy decision")

    allowed_groups = subject.group_ids & policy.allowed_group_ids
    if not allowed_groups:
        raise AuthorizationDenied("subject has no allowed project group")
    for group_id in allowed_groups:
        _validate_search_in_value(group_id)

    actor = AuthorizedActor(
        user_id=subject.user_id,
        allowed_group_ids=frozenset(allowed_groups),
        roles=frozenset(subject.roles),
    )
    return _new_retrieval_policy_context(
        tenant_id=policy.tenant_id,
        project_id=policy.project_id,
        actor=actor,
        allowed_classifications=CLASSIFICATIONS_THROUGH[policy.classification_ceiling],
        allowed_environments=frozenset(policy.allowed_environments),
        allowed_source_families=frozenset(policy.allowed_source_families),
        active_corpus_version=policy.active_corpus_version,
        acl_digest=policy.acl_digest,
        policy_version=policy.policy_version,
        decision_id=policy.decision_id,
        resource_grants=policy.resource_grants,
    )
