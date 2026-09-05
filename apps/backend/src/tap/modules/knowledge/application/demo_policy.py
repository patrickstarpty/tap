"""Fixed local-demo policy built only through verified authorization values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

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
from tap.modules.knowledge.domain.documents import canonical_sha256

if TYPE_CHECKING:
    from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

DEMO_TENANT_ID = "local"
DEMO_PROJECT_ID = "tapper-demo"
DEMO_USER_ID = "tapper-local-user"
DEMO_GROUP_ID = "tapper-local"
DEMO_ENVIRONMENT = "global"
DEMO_CORPUS_VERSION = "tapper-demo-v1"
DEMO_POLICY_VERSION = "tapper-demo-policy-v1"

DEMO_SUBJECT = VerifiedSubjectFacts(
    tenant_id=DEMO_TENANT_ID,
    user_id=DEMO_USER_ID,
    group_ids=frozenset({DEMO_GROUP_ID}),
    roles=frozenset(),
    token_verified=True,
)


class ReadyRevisionRepository(Protocol):
    async def load_ready_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]: ...


def project_policy_for(revisions: tuple[ReadyDocumentRevision, ...]) -> ProjectPolicy:
    ordered = tuple(sorted(revisions, key=lambda item: item.document_id))
    if not ordered or len(ordered) > 20:
        raise ValueError("demo policy requires one to twenty ready revisions")
    if len({item.document_id for item in ordered}) != len(ordered):
        raise ValueError("demo policy revisions must have unique document IDs")
    grants = tuple(
        ResourceGrant(
            family="doc",
            source_id=item.document_id,
            revision_kind="blob_version",
            revision=item.revision_id,
            source_content_hash=item.source_content_hash,
            allow_all_anchors=True,
        )
        for item in ordered
    )
    grant_payload = [
        {
            "family": grant.family,
            "revision": grant.revision,
            "revisionKind": grant.revision_kind,
            "sourceContentHash": grant.source_content_hash,
            "sourceId": grant.source_id,
        }
        for grant in grants
    ]
    acl_digest = _digest({"grants": grant_payload, "schema": "tapper-demo-acl-v1"})
    decision_id = _digest(
        {
            "aclDigest": acl_digest,
            "corpusVersion": DEMO_CORPUS_VERSION,
            "policyVersion": DEMO_POLICY_VERSION,
            "projectId": DEMO_PROJECT_ID,
            "schema": "tapper-demo-decision-v1",
            "tenantId": DEMO_TENANT_ID,
        }
    )
    return ProjectPolicy(
        tenant_id=DEMO_TENANT_ID,
        project_id=DEMO_PROJECT_ID,
        permission_granted=True,
        allowed_group_ids=frozenset({DEMO_GROUP_ID}),
        classification_ceiling=Classification.INTERNAL,
        allowed_environments=frozenset({DEMO_ENVIRONMENT}),
        allowed_source_families=frozenset({"doc"}),
        active_corpus_version=DEMO_CORPUS_VERSION,
        acl_digest=acl_digest,
        policy_version=DEMO_POLICY_VERSION,
        decision_id=decision_id,
        resource_grants=grants,
    )


def build_demo_policy_context(
    revisions: tuple[ReadyDocumentRevision, ...],
) -> RetrievalPolicyContext:
    return build_retrieval_policy_context(
        DEMO_SUBJECT,
        project_policy_for(revisions),
        requested_tenant_id=DEMO_TENANT_ID,
        requested_project_id=DEMO_PROJECT_ID,
    )


class DemoCurrentPolicyVerifier:
    """Reload the document ledger before each provider action and fail closed."""

    def __init__(self, repository: ReadyRevisionRepository) -> None:
        self._repository = repository

    async def verify_current(self, expected: RetrievalPolicyContext) -> RetrievalPolicyContext:
        if not _is_demo_context(expected):
            raise AuthorizationDenied("expected policy is outside the fixed demo authority")
        document_ids = tuple(grant.source_id for grant in expected.resource_grants)
        try:
            current_rows = await self._repository.load_ready_revisions(document_ids)
        except Exception as error:
            raise PolicyUnavailable("current document policy is unavailable") from error
        if tuple(sorted(document_ids)) != tuple(sorted(row.document_id for row in current_rows)):
            raise AuthorizationDenied("selected document is no longer ready and current")
        try:
            current = build_demo_policy_context(current_rows)
        except (TypeError, ValueError) as error:
            raise AuthorizationDenied("current document policy is invalid") from error
        if current != expected:
            raise AuthorizationDenied("selected document revision or content hash changed")
        return current


def _is_demo_context(value: RetrievalPolicyContext) -> bool:
    return (
        isinstance(value, RetrievalPolicyContext)
        and value.tenant_id == DEMO_TENANT_ID
        and value.project_id == DEMO_PROJECT_ID
        and value.actor.user_id == DEMO_USER_ID
        and value.actor.allowed_group_ids == frozenset({DEMO_GROUP_ID})
        and value.allowed_environments == frozenset({DEMO_ENVIRONMENT})
        and value.allowed_source_families == frozenset({"doc"})
        and value.active_corpus_version == DEMO_CORPUS_VERSION
        and value.policy_version == DEMO_POLICY_VERSION
        and 1 <= len(value.resource_grants) <= 20
        and all(
            grant.family == "doc"
            and grant.revision_kind == "blob_version"
            and grant.allow_all_anchors
            for grant in value.resource_grants
        )
    )


def _digest(value: object) -> str:
    return canonical_sha256(_canonical_frame(value))


def _canonical_frame(value: object) -> bytes:
    """Encode typed structured values with byte lengths and sorted mapping keys."""
    if value is None:
        return _frame(b"n", b"")
    if isinstance(value, bool):
        return _frame(b"b", b"1" if value else b"0")
    if isinstance(value, int):
        return _frame(b"i", str(value).encode("ascii"))
    if isinstance(value, str):
        return _frame(b"s", value.encode("utf-8"))
    if isinstance(value, (list, tuple)):
        return _frame(b"l", b"".join(_canonical_frame(item) for item in value))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical policy mappings require string keys")
        payload = b"".join(
            _canonical_frame(key) + _canonical_frame(value[key])
            for key in sorted(value, key=lambda item: item.encode("utf-8"))
        )
        return _frame(b"d", payload)
    raise TypeError("unsupported canonical policy value")


def _frame(tag: bytes, payload: bytes) -> bytes:
    return tag + len(payload).to_bytes(8, byteorder="big", signed=False) + payload
