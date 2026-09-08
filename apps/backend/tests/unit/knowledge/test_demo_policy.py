from __future__ import annotations

import asyncio

import pytest

from tap.modules.access.adapters.validation import (
    ValidationAuthorizationPolicy,
    ValidationScopeProvider,
)
from tap.modules.access.domain.authorization import ActorPrincipal
from tap.modules.access.domain.policy import PolicyUnavailable
from tap.modules.knowledge.application import demo_policy
from tap.modules.knowledge.application.answers import ReadyDocumentRevision
from tap.modules.knowledge.application.demo_policy import (
    DemoCurrentPolicyVerifier,
    build_demo_policy_context,
    project_policy_for,
)
from tap.modules.knowledge.domain.sources import legacy_source_id

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def test_explicit_projection_corpus_is_bound_into_policy_and_decision():
    revisions = (
        ReadyDocumentRevision(
            "doc_" + "1" * 32, "rev_" + "2" * 64, "sha256:" + "3" * 64, "src_" + "4" * 32
        ),
    )
    old = build_demo_policy_context(revisions, corpus_version="tapper-demo-v1")
    current = build_demo_policy_context(revisions, corpus_version="tapper-demo-v2")
    assert current.active_corpus_version == "tapper-demo-v2"
    assert current.decision_id != old.decision_id
    assert current.resource_grants == old.resource_grants
    with pytest.raises(ValueError):
        build_demo_policy_context(revisions, corpus_version="unknown")


def ready(document_id: str, revision_id: str, source_hash: str) -> ReadyDocumentRevision:
    return ReadyDocumentRevision(
        document_id=document_id,
        source_id=legacy_source_id("tapper-demo", document_id),
        revision_id=revision_id,
        source_content_hash=source_hash,
    )


class IdentityRegistry:
    async def get_principal(
        self, enterprise_id: str, project_id: str, actor_id: str
    ) -> ActorPrincipal:
        return ActorPrincipal(
            enterprise_id=enterprise_id,
            actor_id=actor_id,
            principal_type="VALIDATION",
            enabled=True,
        )


class ReadyRevisionRepository:
    def __init__(self, rows: tuple[ReadyDocumentRevision, ...]) -> None:
        self.rows = rows
        self.fail = False
        self.requests: list[tuple[tuple[str, str, str], ...]] = []

    async def load_current_source_revisions(
        self, selected: tuple[tuple[str, str, str], ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        self.requests.append(selected)
        if self.fail:
            raise RuntimeError("mysql://root:secret@localhost")
        wanted = {item[0] for item in selected}
        return tuple(row for row in self.rows if row.source_id in wanted)


def test_fixed_demo_policy_is_deterministic_and_binds_only_selected_revisions() -> None:
    """Changing order must not change authority; adding a source must change it."""
    first = ready("doc_b", "rev_b", HASH_B)
    second = ready("doc_a", "rev_a", HASH_A)

    policy = project_policy_for((first, second))
    reversed_policy = project_policy_for((second, first))
    narrower = project_policy_for((second,))

    assert policy == reversed_policy
    assert policy.acl_digest == reversed_policy.acl_digest
    assert policy.decision_id == reversed_policy.decision_id
    assert policy.acl_digest != narrower.acl_digest
    assert policy.decision_id != narrower.decision_id
    assert policy.tenant_id == "local"
    assert policy.project_id == "tapper-demo"
    assert policy.allowed_group_ids == frozenset({"tapper-local"})
    assert policy.allowed_environments == frozenset({"global"})
    assert policy.allowed_source_families == frozenset({"doc"})
    assert policy.active_corpus_version == "tapper-demo-v1"
    assert tuple(grant.source_id for grant in policy.resource_grants) == tuple(
        legacy_source_id("tapper-demo", item) for item in ("doc_a", "doc_b")
    )
    assert all(grant.revision_kind == "blob_version" for grant in policy.resource_grants)
    assert all(grant.allow_all_anchors for grant in policy.resource_grants)


def test_demo_policy_hash_framing_cannot_confuse_field_boundaries() -> None:
    """A delimiter-based grant digest would collide for adversarial identifier boundaries."""
    left = project_policy_for((ready("doc:a|rev:b", "rev:c", HASH_A),))
    right = project_policy_for((ready("doc:a", "rev:b|rev:c", HASH_A),))

    assert left.acl_digest != right.acl_digest
    assert left.decision_id != right.decision_id


def test_policy_digest_frames_types_lengths_and_mapping_order() -> None:
    assert demo_policy._digest({"b": ["a", "bc"], "a": "x"}) == demo_policy._digest(
        {"a": "x", "b": ["a", "bc"]}
    )
    assert demo_policy._digest({"x": ["a", "bc"]}) != demo_policy._digest({"x": ["ab", "c"]})
    assert demo_policy._digest({"x": ["1"]}) != demo_policy._digest({"x": 1})


def test_demo_policy_context_uses_the_verified_authorization_factory() -> None:
    context = build_demo_policy_context((ready("doc_a", "rev_a", HASH_A),))

    assert context.tenant_id == "local"
    assert context.project_id == "tapper-demo"
    assert context.actor.user_id == "tapper-local-user"
    assert context.actor.allowed_group_ids == frozenset({"tapper-local"})
    assert context.resource_grants[0].revision == "rev_a"


def test_current_policy_verifier_reloads_mysql_and_fails_closed_on_changed_source() -> None:
    async def scenario() -> None:
        initial = ready("doc_a", "rev_a", HASH_A)
        repository = ReadyRevisionRepository((initial,))
        verifier = DemoCurrentPolicyVerifier(
            repository,
            authorization_policy=ValidationAuthorizationPolicy(IdentityRegistry()),
            scope_provider=ValidationScopeProvider(),
        )
        expected = build_demo_policy_context((initial,))

        assert await verifier.verify_current(expected) == expected
        assert repository.requests == [
            ((initial.source_id, initial.revision_id, initial.source_content_hash),)
        ]

        repository.rows = (ready("doc_a", "rev_changed", HASH_B),)
        with pytest.raises(demo_policy.DocumentPolicyChanged):
            await verifier.verify_current(expected)

    asyncio.run(scenario())


def test_current_policy_verifier_maps_ledger_outage_to_policy_unavailable() -> None:
    async def scenario() -> None:
        initial = ready("doc_a", "rev_a", HASH_A)
        repository = ReadyRevisionRepository((initial,))
        repository.fail = True
        verifier = DemoCurrentPolicyVerifier(
            repository,
            authorization_policy=ValidationAuthorizationPolicy(IdentityRegistry()),
            scope_provider=ValidationScopeProvider(),
        )

        with pytest.raises(PolicyUnavailable, match="current document policy"):
            await verifier.verify_current(build_demo_policy_context((initial,)))

    asyncio.run(scenario())
