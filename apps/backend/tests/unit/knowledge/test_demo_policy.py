from __future__ import annotations

import asyncio

import pytest

from tap.modules.access.domain.policy import AuthorizationDenied, PolicyUnavailable
from tap.modules.knowledge.application import demo_policy
from tap.modules.knowledge.application.answers import ReadyDocumentRevision
from tap.modules.knowledge.application.demo_policy import (
    DemoCurrentPolicyVerifier,
    build_demo_policy_context,
    project_policy_for,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def ready(document_id: str, revision_id: str, source_hash: str) -> ReadyDocumentRevision:
    return ReadyDocumentRevision(
        document_id=document_id,
        revision_id=revision_id,
        source_content_hash=source_hash,
    )


class ReadyRevisionRepository:
    def __init__(self, rows: tuple[ReadyDocumentRevision, ...]) -> None:
        self.rows = rows
        self.fail = False
        self.requests: list[tuple[str, ...]] = []

    async def load_ready_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        self.requests.append(document_ids)
        if self.fail:
            raise RuntimeError("mysql://root:secret@localhost")
        wanted = set(document_ids)
        return tuple(row for row in self.rows if row.document_id in wanted)


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
    assert policy.project_id == "athena-demo"
    assert policy.allowed_group_ids == frozenset({"athena-local"})
    assert policy.allowed_environments == frozenset({"global"})
    assert policy.allowed_source_families == frozenset({"doc"})
    assert policy.active_corpus_version == "athena-demo-v1"
    assert tuple(grant.source_id for grant in policy.resource_grants) == ("doc_a", "doc_b")
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
    assert context.project_id == "athena-demo"
    assert context.actor.user_id == "athena-local-user"
    assert context.actor.allowed_group_ids == frozenset({"athena-local"})
    assert context.resource_grants[0].revision == "rev_a"


def test_current_policy_verifier_reloads_mysql_and_fails_closed_on_changed_source() -> None:
    async def scenario() -> None:
        initial = ready("doc_a", "rev_a", HASH_A)
        repository = ReadyRevisionRepository((initial,))
        verifier = DemoCurrentPolicyVerifier(repository)
        expected = build_demo_policy_context((initial,))

        assert await verifier.verify_current(expected) == expected
        assert repository.requests == [("doc_a",)]

        repository.rows = (ready("doc_a", "rev_changed", HASH_B),)
        with pytest.raises(AuthorizationDenied):
            await verifier.verify_current(expected)

    asyncio.run(scenario())


def test_current_policy_verifier_maps_ledger_outage_to_policy_unavailable() -> None:
    async def scenario() -> None:
        initial = ready("doc_a", "rev_a", HASH_A)
        repository = ReadyRevisionRepository((initial,))
        repository.fail = True
        verifier = DemoCurrentPolicyVerifier(repository)

        with pytest.raises(PolicyUnavailable, match="current document policy"):
            await verifier.verify_current(build_demo_policy_context((initial,)))

    asyncio.run(scenario())
