"""Framework-free, fail-closed retrieval authorization values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AuthorizationDenied(Exception):
    """Current verified facts do not authorize the requested operation."""


class PolicyUnavailable(Exception):
    """The authoritative Project Policy could not produce a decision."""


class Classification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


CLASSIFICATIONS_THROUGH: dict[Classification, frozenset[Classification]] = {
    Classification.PUBLIC: frozenset({Classification.PUBLIC}),
    Classification.INTERNAL: frozenset({Classification.PUBLIC, Classification.INTERNAL}),
    Classification.CONFIDENTIAL: frozenset(
        {
            Classification.PUBLIC,
            Classification.INTERNAL,
            Classification.CONFIDENTIAL,
        }
    ),
    Classification.RESTRICTED: frozenset(Classification),
}


@dataclass(frozen=True, slots=True)
class VerifiedSubjectFacts:
    """Identity facts emitted only after the BFF verifies the Entra token."""

    tenant_id: str
    user_id: str
    group_ids: frozenset[str]
    roles: frozenset[str]
    token_verified: bool


@dataclass(frozen=True, slots=True)
class ResourceGrant:
    """Server-resolved immutable resource permission included in a policy decision."""

    family: str
    source_id: str
    revision_kind: str
    revision: str
    source_content_hash: str
    allowed_anchor_keys: frozenset[str] = frozenset()
    allow_all_anchors: bool = False

    def __post_init__(self) -> None:
        if self.family not in {"doc", "code", "bdd", "failure"}:
            raise ValueError("resource grant has an unsupported source family")
        if not all((self.source_id, self.revision_kind, self.revision, self.source_content_hash)):
            raise ValueError("resource grants require immutable source revision facts")


@dataclass(frozen=True, slots=True)
class ProjectPolicy:
    """Authoritative server-side Project Policy decision input."""

    tenant_id: str
    project_id: str
    permission_granted: bool
    allowed_group_ids: frozenset[str]
    classification_ceiling: Classification
    allowed_environments: frozenset[str]
    allowed_source_families: frozenset[str]
    active_corpus_version: str
    acl_digest: str
    policy_version: str
    decision_id: str
    resource_grants: tuple[ResourceGrant, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.tenant_id,
            self.project_id,
            self.active_corpus_version,
            self.acl_digest,
            self.policy_version,
            self.decision_id,
        )
        if not all(required):
            raise ValueError("Project Policy facts must be non-empty")
        if not self.allowed_environments:
            raise ValueError("Project Policy must authorize at least one environment")
        if not self.allowed_source_families:
            raise ValueError("Project Policy must authorize at least one source family")
        if not self.allowed_source_families <= {"doc", "code", "bdd", "failure"}:
            raise ValueError("Project Policy has an unsupported source family")


@dataclass(frozen=True, slots=True)
class AuthorizedActor:
    user_id: str
    allowed_group_ids: frozenset[str]
    roles: frozenset[str]


_CONSTRUCTION_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class RetrievalPolicyContext:
    """Trusted retrieval scope; only the authorization application can create it."""

    tenant_id: str
    project_id: str
    actor: AuthorizedActor
    allowed_classifications: frozenset[Classification]
    allowed_environments: frozenset[str]
    allowed_source_families: frozenset[str]
    active_corpus_version: str
    acl_digest: str
    policy_version: str
    decision_id: str
    resource_grants: tuple[ResourceGrant, ...]

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor: AuthorizedActor,
        allowed_classifications: frozenset[Classification],
        allowed_environments: frozenset[str],
        allowed_source_families: frozenset[str],
        active_corpus_version: str,
        acl_digest: str,
        policy_version: str,
        decision_id: str,
        resource_grants: tuple[ResourceGrant, ...],
        _construction_token: object,
    ) -> None:
        if _construction_token is not _CONSTRUCTION_TOKEN:
            raise TypeError("RetrievalPolicyContext must come from verified authorization")
        values = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "actor": actor,
            "allowed_classifications": allowed_classifications,
            "allowed_environments": allowed_environments,
            "allowed_source_families": allowed_source_families,
            "active_corpus_version": active_corpus_version,
            "acl_digest": acl_digest,
            "policy_version": policy_version,
            "decision_id": decision_id,
            "resource_grants": resource_grants,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)


def _new_retrieval_policy_context(
    *,
    tenant_id: str,
    project_id: str,
    actor: AuthorizedActor,
    allowed_classifications: frozenset[Classification],
    allowed_environments: frozenset[str],
    allowed_source_families: frozenset[str],
    active_corpus_version: str,
    acl_digest: str,
    policy_version: str,
    decision_id: str,
    resource_grants: tuple[ResourceGrant, ...],
) -> RetrievalPolicyContext:
    return RetrievalPolicyContext(
        tenant_id=tenant_id,
        project_id=project_id,
        actor=actor,
        allowed_classifications=allowed_classifications,
        allowed_environments=allowed_environments,
        allowed_source_families=allowed_source_families,
        active_corpus_version=active_corpus_version,
        acl_digest=acl_digest,
        policy_version=policy_version,
        decision_id=decision_id,
        resource_grants=resource_grants,
        _construction_token=_CONSTRUCTION_TOKEN,
    )
