from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.adapters.published_knowledge import PublishedKnowledgeEvidence
from tap.modules.ai.ports.insights import AuthorizedInsightsScope, InsightsAuthorizationChanged
from tap.modules.chat.domain.conversations import FrozenResource
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.domain.review import KnowledgePublication
from tap.modules.knowledge.ports.answers import DocumentStateChanged, ReadyDocumentRevision

SOURCE = "src_" + "1" * 32
HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64
FROZEN = FrozenResource(SOURCE, "doc-1", "rev-1", HASH)
SCOPE = AuthorizedInsightsScope(
    context=VALIDATION_SCOPE,
    user_authorization="delegated-user-token-0001",
    authorization_version="authz-1",
    authorized_resource_refs=(SOURCE,),
)


def _evidence(**changes):
    values = dict(
        family=SourceFamily.DOC,
        chunk_id="chunk-1",
        logical_chunk_id="logical-1",
        title="Runbook",
        content="A failing login test may follow a credential rotation.",
        source=SourceRevisionRef(
            source_id=SOURCE,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision="rev-1",
            source_content_hash=HASH,
            anchor=DocumentAnchor(page=2, inventory_item_id="item-1"),
        ),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
        citation_id="citation-1",
        evidence_label="S1",
        index_revision=IndexRevision("generation-1", "v1", "tapper-demo-v2"),
        embedding_model_version="embed-v1",
        acl_decision_id="decision-1",
        score=0.9,
        publication_id="publication-1",
        approval_digest=HASH,
        approved_item_id="item-1",
    )
    values.update(changes)
    return Evidence(**values)


class Searches:
    scope = VALIDATION_SCOPE

    def __init__(self, evidence=None):
        self.evidence = (_evidence(),) if evidence is None else evidence
        self.current = (ReadyDocumentRevision("doc-1", "rev-1", HASH, SOURCE),)
        self.requests = []

    async def resolve_conversation_selection(self, revisions):
        assert revisions == ("rev-1",)
        return self.current, SimpleNamespace()

    async def search(self, request):
        self.requests.append(request)
        return SimpleNamespace(evidence=self.evidence)


class Authority:
    def __init__(self):
        self.allowed = True

    async def authorize_evidence(self, project_id, *, source_revision_id, approved_item_id):
        assert (project_id, source_revision_id, approved_item_id) == (
            "tapper-demo",
            "rev-1",
            "item-1",
        )
        if not self.allowed:
            raise PermissionError("revoked")

        class Binding:
            publication_id = "publication-1"
            approval_digest = HASH

            def for_revision(self, revision):
                assert revision == "rev-1"
                return self

        return Binding()

    async def revalidate(self, binding):
        if not self.allowed:
            raise PermissionError("revoked")
        return binding


@pytest.mark.asyncio
async def test_ready_enabled_knowledge_can_supply_insights_without_fabricating_approval():
    searches = Searches(
        (_evidence(publication_id=None, approval_digest=None, approved_item_id=None),)
    )
    adapter = PublishedKnowledgeEvidence(
        searches=searches, publication_authority=None, selection=(FROZEN,)
    )
    evidence = await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")
    assert len(evidence) == 1
    details = adapter.citation_details(("citation-1",))
    assert details[0]["publicationId"] is None
    assert details[0]["approvalDigest"] is None
    assert await adapter.reauthorize(SCOPE, evidence)
    assert await adapter.reauthorize_details(SCOPE, details)
    searches.current = ()
    assert not await adapter.reauthorize(SCOPE, evidence)
    assert not await adapter.reauthorize_details(SCOPE, details)


@pytest.mark.asyncio
async def test_direct_knowledge_rejects_unselected_and_cross_project_evidence():
    searches = Searches(
        (_evidence(publication_id=None, approval_digest=None, approved_item_id=None),)
    )
    adapter = PublishedKnowledgeEvidence(
        searches=searches, publication_authority=None, selection=(FROZEN,)
    )
    with pytest.raises(InsightsAuthorizationChanged):
        await adapter.retrieve(
            replace(SCOPE, context=replace(VALIDATION_SCOPE, project_id="other")),
            (SOURCE,),
            "Why did login fail?",
        )
    searches.evidence = (
        _evidence(
            source=replace(_evidence().source, source_id="src_" + "2" * 32),
            publication_id=None,
            approval_digest=None,
            approved_item_id=None,
        ),
    )
    with pytest.raises(InsightsAuthorizationChanged):
        await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")


@pytest.mark.asyncio
async def test_published_source_enters_model_evidence_with_actual_search_provenance():
    searches = Searches()
    adapter = PublishedKnowledgeEvidence(
        searches=searches, publication_authority=Authority(), selection=(FROZEN,)
    )
    assert not adapter.search_performed

    evidence = await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")
    assert adapter.search_performed

    assert len(evidence) == 1
    assert evidence[0].resource_ref == SOURCE
    assert evidence[0].citation_id == "citation-1"
    assert evidence[0].excerpt == "A failing login test may follow a credential rotation."
    assert evidence[0].evidence_version.startswith("sha256:")
    assert searches.requests[0].query == "Why did login fail?"
    assert searches.requests[0].resource_refs[0].source_id == SOURCE
    assert adapter.citation_details(("citation-1",))[0]["approvedItemId"] == "item-1"
    assert await adapter.reauthorize(SCOPE, evidence)
    assert await adapter.reauthorize_details(SCOPE, adapter.citation_details(("citation-1",)))


@pytest.mark.asyncio
async def test_changed_frozen_source_and_revoked_approval_fail_closed():
    searches = Searches()
    authority = Authority()
    adapter = PublishedKnowledgeEvidence(
        searches=searches, publication_authority=authority, selection=(FROZEN,)
    )
    evidence = await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")
    searches.current = (ReadyDocumentRevision("doc-1", "rev-2", HASH, SOURCE),)
    assert not await adapter.reauthorize(SCOPE, evidence)
    assert not await adapter.reauthorize_details(SCOPE, adapter.citation_details(("citation-1",)))
    searches.current = (ReadyDocumentRevision("doc-1", "rev-1", HASH, SOURCE),)
    authority.allowed = False
    assert not await adapter.reauthorize(SCOPE, evidence)
    assert not await adapter.reauthorize_details(SCOPE, adapter.citation_details(("citation-1",)))


@pytest.mark.asyncio
async def test_wrong_project_and_unapproved_or_unselected_hits_are_denied():
    searches = Searches()
    adapter = PublishedKnowledgeEvidence(
        searches=searches, publication_authority=Authority(), selection=(FROZEN,)
    )
    wrong_scope = replace(SCOPE, context=replace(VALIDATION_SCOPE, project_id="other"))
    with pytest.raises(InsightsAuthorizationChanged):
        await adapter.retrieve(wrong_scope, (SOURCE,), "Why did login fail?")
    searches.evidence = (_evidence(approved_item_id=None),)
    with pytest.raises(InsightsAuthorizationChanged):
        await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")
    searches.evidence = (
        _evidence(source=replace(_evidence().source, source_id="src_" + "2" * 32)),
    )
    with pytest.raises(InsightsAuthorizationChanged):
        await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")


@pytest.mark.asyncio
async def test_no_frozen_knowledge_selection_does_not_search():
    searches = Searches()
    adapter = PublishedKnowledgeEvidence(
        searches=searches, publication_authority=Authority(), selection=()
    )
    assert await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?") == ()
    assert searches.requests == []
    assert not adapter.search_performed


@pytest.mark.asyncio
async def test_stale_frozen_selection_reports_authorization_change():
    class StaleSearches(Searches):
        async def resolve_conversation_selection(self, revisions):
            raise DocumentStateChanged("selected source changed")

    adapter = PublishedKnowledgeEvidence(
        searches=StaleSearches(), publication_authority=Authority(), selection=(FROZEN,)
    )
    with pytest.raises(InsightsAuthorizationChanged):
        await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")
    assert not adapter.search_performed


@pytest.mark.asyncio
async def test_current_publication_authority_blocks_revoked_source_before_delivery():
    now = datetime(2026, 9, 27, tzinfo=UTC)

    class Publications:
        current = KnowledgePublication(
            publication_id="publication-1",
            project_id="tapper-demo",
            review_id="review-1",
            review_version=1,
            approval_digest=HASH,
            source_revision_ids=("rev-1",),
            approved_item_ids=("item-1",),
            generation="generation-1",
            published_by="reviewer-1",
            published_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=1),
        )

        async def current_publication(self):
            return self.current

    publications = Publications()
    adapter = PublishedKnowledgeEvidence(
        searches=Searches(),
        publication_authority=PublishedKnowledgeAuthority(publications, now=lambda: now),
        selection=(FROZEN,),
    )
    evidence = await adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")
    assert await adapter.reauthorize(SCOPE, evidence)
    unapproved = Searches(
        (
            _evidence(
                source=replace(
                    _evidence().source,
                    anchor=DocumentAnchor(page=2, inventory_item_id="item-2"),
                ),
                approved_item_id="item-2",
            ),
        )
    )
    unapproved_adapter = PublishedKnowledgeEvidence(
        searches=unapproved,
        publication_authority=PublishedKnowledgeAuthority(publications, now=lambda: now),
        selection=(FROZEN,),
    )
    with pytest.raises(AuthorizationDenied):
        await unapproved_adapter.retrieve(SCOPE, (SOURCE,), "Why did login fail?")
    publications.current = replace(
        publications.current, status="withdrawn", withdrawn_by="reviewer-1", withdrawn_at=now
    )
    assert not await adapter.reauthorize(SCOPE, evidence)
    assert not await adapter.reauthorize_details(SCOPE, adapter.citation_details(("citation-1",)))
