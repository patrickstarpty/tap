"""Construct trusted retrieval policy contexts from two authoritative inputs."""

from __future__ import annotations

from datetime import datetime

from tap.modules.access.domain.authorization import (
    AuthorizationDecision,
    ProjectPrincipal,
    ResourceRef,
)
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

_PROJECT_ACTION_RESOURCE_KINDS = {
    "knowledge.citation.read": "knowledge-citation",
    "knowledge.evidence.read": "knowledge-evidence",
    "knowledge.original.read": "knowledge-original",
    "knowledge.publish": "knowledge-publication",
    "knowledge.review.approve": "knowledge-review",
    "knowledge.review.edit": "knowledge-review",
    "test-plans.review": "test-plan",
}


def authorize_project_action(
    principal: ProjectPrincipal,
    action: str,
    resource: ResourceRef,
    *,
    expected_audience: str,
    now: datetime,
    separation_actor_id: str | None = None,
) -> AuthorizationDecision:
    """Authorize one current project action without trusting caller-provided scope."""
    if not isinstance(principal, ProjectPrincipal) or not isinstance(resource, ResourceRef):
        return AuthorizationDecision(False, "invalid-authority")
    if not principal.enabled:
        return AuthorizationDecision(False, "principal-disabled")
    if principal.expires_at <= now:
        return AuthorizationDecision(False, "principal-expired")
    if principal.audience != expected_audience:
        return AuthorizationDecision(False, "audience-mismatch")
    if (principal.enterprise_id, principal.project_id) != (
        resource.enterprise_id,
        resource.project_id,
    ):
        return AuthorizationDecision(False, "scope-mismatch")
    if action not in principal.actions:
        return AuthorizationDecision(False, "action-not-allowed")
    expected_kind = _PROJECT_ACTION_RESOURCE_KINDS.get(action)
    if expected_kind is None:
        return AuthorizationDecision(False, "action-not-allowed")
    if resource.kind != expected_kind:
        return AuthorizationDecision(False, "resource-kind-mismatch")
    if action == "knowledge.review.approve" and principal.actor_id == separation_actor_id:
        return AuthorizationDecision(False, "separation-of-duties")
    return AuthorizationDecision(True, "project-action-allowed")


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
