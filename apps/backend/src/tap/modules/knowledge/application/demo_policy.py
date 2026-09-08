"""Fixed local-demo policy built only through verified authorization values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.application.policy import require_authorized
from tap.modules.access.application.ports import AuthorizationPolicy, ScopeProvider
from tap.modules.access.application.scope import RequestFacts
from tap.modules.access.domain.authorization import ResourceRef
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
    async def load_current_source_revisions(
        self, selected: tuple[tuple[str, str, str], ...]
    ) -> tuple[ReadyDocumentRevision, ...]: ...


def project_policy_for(
    revisions: tuple[ReadyDocumentRevision, ...], *, corpus_version: str = DEMO_CORPUS_VERSION
) -> ProjectPolicy:
    if corpus_version not in {"tapper-demo-v1", "tapper-demo-v2"}:
        raise ValueError("unsupported projection corpus")
    ordered = tuple(sorted(revisions, key=lambda item: item.document_id))
    if not ordered or len(ordered) > 20:
        raise ValueError("demo policy requires one to twenty ready revisions")
    if len({item.document_id for item in ordered}) != len(ordered):
        raise ValueError("demo policy revisions must have unique document IDs")
    grants = tuple(
        ResourceGrant(
            family="doc",
            source_id=item.source_id or item.document_id,
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
            "corpusVersion": corpus_version,
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
        active_corpus_version=corpus_version,
        acl_digest=acl_digest,
        policy_version=DEMO_POLICY_VERSION,
        decision_id=decision_id,
        resource_grants=grants,
    )


def build_demo_policy_context(
    revisions: tuple[ReadyDocumentRevision, ...],
    *,
    corpus_version: str = DEMO_CORPUS_VERSION,
) -> RetrievalPolicyContext:
    return build_retrieval_policy_context(
        DEMO_SUBJECT,
        project_policy_for(revisions, corpus_version=corpus_version),
        requested_tenant_id=DEMO_TENANT_ID,
        requested_project_id=DEMO_PROJECT_ID,
    )


class DocumentPolicyChanged(AuthorizationDenied):
    """Previously selected document facts are stale rather than actor-denied."""


class DemoCurrentPolicyVerifier:
    """Reload the document ledger before each provider action and fail closed."""

    def __init__(
        self,
        repository: ReadyRevisionRepository,
        *,
        authorization_policy: AuthorizationPolicy,
        scope_provider: ScopeProvider,
        corpus_version: str = DEMO_CORPUS_VERSION,
    ) -> None:
        self._repository = repository
        self._authorization_policy = authorization_policy
        self._scope_provider = scope_provider
        if corpus_version not in {"tapper-demo-v1", "tapper-demo-v2"}:
            raise ValueError("unsupported projection corpus")
        self._corpus_version = corpus_version

    async def verify_current(self, expected: RetrievalPolicyContext) -> RetrievalPolicyContext:
        if not _is_demo_context(expected, corpus_version=self._corpus_version):
            raise AuthorizationDenied("expected policy is outside the fixed demo authority")
        scope = await self._scope_provider.current(RequestFacts(project_id=expected.project_id))
        await require_authorized(
            self._authorization_policy,
            scope,
            "knowledge.read",
            ResourceRef(
                enterprise_id=expected.tenant_id, project_id=expected.project_id, kind="knowledge"
            ),
        )
        selected = tuple(
            (grant.source_id, grant.revision, grant.source_content_hash)
            for grant in expected.resource_grants
        )
        try:
            current_rows = await self._repository.load_current_source_revisions(selected)
        except Exception as error:
            raise PolicyUnavailable("current document policy is unavailable") from error
        if set(selected) != {
            (row.source_id, row.revision_id, row.source_content_hash) for row in current_rows
        }:
            raise DocumentPolicyChanged("selected document is no longer ready and current")
        try:
            current = build_demo_policy_context(current_rows, corpus_version=self._corpus_version)
        except (TypeError, ValueError) as error:
            raise DocumentPolicyChanged("current document policy is invalid") from error
        if current != expected:
            raise DocumentPolicyChanged("selected document revision or content hash changed")
        return current


def _is_demo_context(
    value: RetrievalPolicyContext, *, corpus_version: str = DEMO_CORPUS_VERSION
) -> bool:
    return (
        isinstance(value, RetrievalPolicyContext)
        and value.tenant_id == DEMO_TENANT_ID
        and value.project_id == DEMO_PROJECT_ID
        and value.actor.user_id == DEMO_USER_ID
        and value.actor.allowed_group_ids == frozenset({DEMO_GROUP_ID})
        and value.allowed_environments == frozenset({DEMO_ENVIRONMENT})
        and value.allowed_source_families == frozenset({"doc"})
        and value.active_corpus_version == corpus_version
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
