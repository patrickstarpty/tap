"""Framework-free, fail-closed retrieval authorization values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MAX_POLICY_GROUP_IDS = 128
MAX_POLICY_STRING_LENGTH = 256


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

    def __post_init__(self) -> None:
        _required_string("tenant_id", self.tenant_id)
        _required_string("user_id", self.user_id)
        _string_set(
            "group_ids",
            self.group_ids,
            allow_empty=True,
            max_items=MAX_POLICY_GROUP_IDS,
            max_item_length=MAX_POLICY_STRING_LENGTH,
        )
        _string_set("roles", self.roles, allow_empty=True)
        _strict_bool("token_verified", self.token_verified)


@dataclass(frozen=True, slots=True)
class ResourceSubtreeGrant:
    """Server-resolved filterable subtree for one authorized structural anchor."""

    anchor_key: str
    root_ids: tuple[str, ...] = ()
    parent_ids: tuple[str, ...] = ()
    logical_chunk_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required_string("anchor_key", self.anchor_key)
        for name, values in (
            ("root_ids", self.root_ids),
            ("parent_ids", self.parent_ids),
            ("logical_chunk_ids", self.logical_chunk_ids),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > 32
                or any(
                    not isinstance(value, str) or not value or len(value) > 256 for value in values
                )
            ):
                raise ValueError(f"{name} must be a bounded immutable identifier tuple")
        if not (self.root_ids or self.parent_ids or self.logical_chunk_ids):
            raise ValueError("resource subtree must contain a filterable locator")


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
    subtree_grants: tuple[ResourceSubtreeGrant, ...] = ()

    def __post_init__(self) -> None:
        _required_string("family", self.family)
        _required_string("source_id", self.source_id)
        _required_string("revision_kind", self.revision_kind)
        if self.revision_kind not in {"git_commit", "blob_version", "mysql_version"}:
            raise ValueError("resource grant has an unsupported revision kind")
        if self.family in {"code", "bdd"} and self.revision_kind != "git_commit":
            raise ValueError("code and BDD resource grants require a Git revision")
        _immutable_revision("revision", self.revision_kind, self.revision)
        _canonical_sha256("source_content_hash", self.source_content_hash)
        _string_set("allowed_anchor_keys", self.allowed_anchor_keys, allow_empty=True)
        _strict_bool("allow_all_anchors", self.allow_all_anchors)
        if (
            not isinstance(self.subtree_grants, tuple)
            or len(self.subtree_grants) > 32
            or not all(isinstance(subtree, ResourceSubtreeGrant) for subtree in self.subtree_grants)
        ):
            raise TypeError("subtree_grants must be a bounded immutable grant tuple")
        if not self.allow_all_anchors and any(
            subtree.anchor_key not in self.allowed_anchor_keys for subtree in self.subtree_grants
        ):
            raise ValueError("resource subtree must belong to an allowed anchor")
        if self.family not in {"doc", "code", "bdd", "failure"}:
            raise ValueError("resource grant has an unsupported source family")


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
        for name in (
            "tenant_id",
            "project_id",
            "active_corpus_version",
            "acl_digest",
            "policy_version",
            "decision_id",
        ):
            _required_string(name, getattr(self, name))
        _strict_bool("permission_granted", self.permission_granted)
        if not isinstance(self.classification_ceiling, Classification):
            raise TypeError("classification_ceiling must be a Classification")
        _string_set(
            "allowed_group_ids",
            self.allowed_group_ids,
            allow_empty=True,
            max_items=MAX_POLICY_GROUP_IDS,
            max_item_length=MAX_POLICY_STRING_LENGTH,
        )
        _string_set("allowed_environments", self.allowed_environments, allow_empty=False)
        _string_set(
            "allowed_source_families",
            self.allowed_source_families,
            allow_empty=False,
        )
        if not isinstance(self.resource_grants, tuple) or not all(
            isinstance(grant, ResourceGrant) for grant in self.resource_grants
        ):
            raise TypeError("resource_grants must be an immutable ResourceGrant tuple")
        if not self.allowed_source_families <= {"doc", "code", "bdd", "failure"}:
            raise ValueError("Project Policy has an unsupported source family")


@dataclass(frozen=True, slots=True)
class AuthorizedActor:
    user_id: str
    allowed_group_ids: frozenset[str]
    roles: frozenset[str]

    def __post_init__(self) -> None:
        _required_string("user_id", self.user_id)
        _string_set(
            "allowed_group_ids",
            self.allowed_group_ids,
            allow_empty=False,
            max_items=MAX_POLICY_GROUP_IDS,
            max_item_length=MAX_POLICY_STRING_LENGTH,
        )
        _string_set("roles", self.roles, allow_empty=True)


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


def _strict_bool(name: str, value: object) -> None:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")


def _required_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _canonical_sha256(name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be a canonical sha256 digest")


def _immutable_revision(name: str, kind: str, value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{name} must be a bounded immutable revision")
    if kind == "git_commit" and (
        len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a canonical Git commit ID")


def _string_set(
    name: str,
    value: object,
    *,
    allow_empty: bool,
    max_items: int | None = None,
    max_item_length: int = MAX_POLICY_STRING_LENGTH,
) -> None:
    if not isinstance(value, frozenset) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise TypeError(f"{name} must be a frozenset of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    if max_items is not None and len(value) > max_items:
        raise ValueError(f"{name} must contain at most {max_items} values")
    if any(len(item) > max_item_length for item in value):
        raise ValueError(f"{name} values must contain at most {max_item_length} characters")
